"""The statement on origin: checked by code, confirmed by the user, issued by the graph.

No agent can issue a statement (use_tools refuses tools that need confirmation). The graph
only offers it when code finds everything in place: the user asked for one, the product is
originating under that agreement, the user gave every datum, and the checks passed. Then the
graph stops (LangGraph ``interrupt``) and shows the data to the user; only a yes issues it."""

from __future__ import annotations

from langgraph.types import interrupt

from origo.engine.registry import ToolRegistry
from origo.engine.tools import EXPORTER_REFERENCE_THRESHOLD_EUR, issue_origin_statement
from origo.state import OrigoState

REGISTRY = ToolRegistry().register(issue_origin_statement)


def statement_agreement(state: OrigoState) -> str | None:
    """The agreement the statement is for: the one the user named, or the only one asked."""
    plan = state["plan"]
    if plan.statement is None:
        return None
    if plan.statement.agreement:
        return plan.statement.agreement
    return plan.agreements[0] if len(plan.agreements) == 1 else None


def statement_gaps(state: OrigoState) -> list[str]:
    """What stops the statement, computed by code ([] when it can be offered to the user)."""
    plan = state["plan"]
    if plan.statement is None:
        return []
    request, gaps = plan.statement, []
    agreement = statement_agreement(state)
    if agreement is None:
        gaps.append("the agreement (destination) the statement is for")
    for field in ("invoice_number", "exporter_name", "place", "consignment_value_eur"):
        if getattr(request, field) is None:
            gaps.append(field)
    value = request.consignment_value_eur or 0
    if value > EXPORTER_REFERENCE_THRESHOLD_EUR and not request.exporter_reference:
        gaps.append(
            f"exporter_reference (REX number: required above {EXPORTER_REFERENCE_THRESHOLD_EUR} "
            "EUR)"
        )
    evaluation = state.get("evaluations", {}).get(agreement or "", {})
    if agreement and not evaluation.get("originating"):
        gaps.append(f"an originating verdict under {agreement}")
    if state.get("issues"):
        gaps.append("checks that pass (there are unresolved problems)")
    return gaps


def _arguments(state: OrigoState) -> dict:
    """The arguments of issue_origin_statement, all from the state."""
    request, agreement = state["plan"].statement, statement_agreement(state)
    return {
        "evaluation_id": state["evaluations"][agreement]["evaluation_id"],
        "invoice_number": request.invoice_number,
        "exporter_name": request.exporter_name,
        "place": request.place,
        "consignment_value_eur": request.consignment_value_eur,
        "exporter_reference": request.exporter_reference or "",
    }


def confirm(state: OrigoState) -> dict:
    """LangGraph node: stops the graph until the user answers. On resume this node runs again
    from the start, so everything before ``interrupt`` must be safe to repeat (it only reads)."""
    data = {"agreement": statement_agreement(state), **_arguments(state)}
    answer = interrupt(data)  # the value of Command(resume=...): True to issue
    return {"statement": {"confirmed": bool(answer)}}


def issue(state: OrigoState) -> dict:
    """LangGraph node: issues the statement the user confirmed, with the data of the state."""
    record = REGISTRY.call("issue_origin_statement", _arguments(state))
    result = record.result if record.ok else {"error": record.error}
    return {"statement": {"confirmed": True, **result}}
