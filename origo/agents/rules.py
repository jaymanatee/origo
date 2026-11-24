"""Rules node: the rule of origin of the product under each agreement of the plan.

Not an agent: get_origin_rule(hs_code, agreement) has nothing to decide, so the code calls it
with the plan's values. A model would only add cost and a chance to get the arguments wrong
(the argument errors were most of ARCA's failures)."""

from __future__ import annotations

from origo.engine.registry import ToolRegistry
from origo.engine.tools import get_origin_rule
from origo.state import OrigoState

REGISTRY = ToolRegistry().register(get_origin_rule)


def rules(state: OrigoState) -> dict:
    """LangGraph node: writes state['rules'], agreement -> the rule, or {'error': ...}."""
    plan = state["plan"]
    if not plan.product_hs_code:
        return {"rules": {}}
    found = {}
    for agreement in plan.agreements:
        record = REGISTRY.call(
            "get_origin_rule", {"hs_code": plan.product_hs_code, "agreement": agreement}
        )
        found[agreement] = record.result if record.ok else {"error": record.error}
    return {"rules": found}
