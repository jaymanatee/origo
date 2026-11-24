"""Verifier: checks, in code, that what the agents wrote comes from the user or from a tool.

It never corrects anything: each problem becomes an Issue for the agent that has to redo its
part, with a message that says what to fix. What code cannot check (whether 'UE' is right
for 'Francia') is not checked. Missing data is not an issue: the user is asked for it."""

from __future__ import annotations

import re
import unicodedata

from origo.agents.extractor import read_bom
from origo.state import Issue, OrigoState, _hs6

_CODE = re.compile(r"\b\d{4}(?:[.\s]?\d{2}|\.\d)\b")


def _plain(text: str) -> str:
    """Lower case, no accents, only letters and digits: 'Haya (Francia)' -> 'haya francia'."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _codes(text: str) -> set[str]:
    """The 6-digit HS codes written in the text ('8501.10', '8501 10', '8501.1' from Excel)."""
    codes = set()
    for match in _CODE.finditer(text):
        try:
            codes.add(_hs6(match.group()))
        except ValueError:
            pass
    return codes


def _amounts(text: str) -> set[float]:
    """Every number of the text, read with a decimal point and with a decimal comma
    ('1.200' is 1.2 and 1200; '18,50' is 18.5 and 1850)."""
    values = set()
    for token in re.findall(r"\d[\d.,]*\d|\d", text):
        for thousands, decimal in ((",", "."), (".", ",")):
            try:
                values.add(float(token.replace(thousands, "").replace(decimal, ".")))
            except ValueError:
                pass
    return values


def _given(value: float, amounts: set[float]) -> bool:
    return any(abs(value - a) < 0.005 for a in amounts)


def _evaluation_issue(agreement: str, error: str) -> Issue | None:
    """Which agent can fix an evaluate_origin error; None if no agent can (the writer says it)."""
    if "subdivision" in error:
        return Issue(agent="origin", message=f"{agreement}: {error}")
    if "add up to" in error:
        return Issue(agent="extractor", message=f"{agreement}: {error}")
    if "did not answer" in error or "no rule of origin" in error:
        return Issue(agent="rules", message=f"{agreement}: {error}")
    if "FOB basis" in error or "checked by hand" in error:
        return None
    return Issue(agent="origin", message=f"{agreement}: {error}")


def verifier(state: OrigoState) -> dict:
    """LangGraph node: writes state['issues'] (empty: all checks pass) and counts the attempt."""
    attempts = state.get("attempts", 0) + 1
    plan = state["plan"]
    if plan.kind != "evaluate":
        return {"issues": [], "attempts": attempts}

    source = state["question"]
    if state.get("bom_file"):
        source += "\n" + read_bom(state["bom_file"])
    plain, codes = _plain(source), _codes(source)
    amounts = _amounts(source) | {c["amount_eur"] for c in state.get("conversions", [])}
    issues: list[Issue] = []

    def check(ok: bool, agent: str, message: str) -> None:
        if not ok:
            issues.append(Issue(agent=agent, message=message))

    # The plan (orchestrator).
    if plan.product_hs_code:
        check(
            plan.product_hs_code in codes,
            "orchestrator",
            f"product_hs_code {plan.product_hs_code} is not in the user's message: copy the "
            "product's code as the user wrote it, or null if the user gave none.",
        )
    if plan.exw_price_eur:
        check(
            _given(plan.exw_price_eur, amounts),
            "orchestrator",
            f"exw_price_eur {plan.exw_price_eur} is not a value the user gave: copy it, or null.",
        )
    if plan.statement:
        for field in ("invoice_number", "exporter_name", "exporter_reference"):
            value = getattr(plan.statement, field)
            check(
                value is None or _plain(value) in plain,
                "orchestrator",
                f"statement.{field} '{value}' is not in the user's message: copy it exactly, "
                "or null if the user gave none.",
            )
        value = plan.statement.consignment_value_eur
        check(
            value is None or _given(value, amounts),
            "orchestrator",
            f"statement.consignment_value_eur {value} is not a value the user gave: copy the "
            "value of the consignment (the invoice), not the price of one product, or null.",
        )

    # The materials (extractor).
    for m in state.get("materials", []):
        check(
            m.hs_code in codes,
            "extractor",
            f"material '{m.name}': HS {m.hs_code} is not in the user's text or file: copy it, "
            "or leave the material out and say so in missing.",
        )
        check(
            _given(m.value_eur, amounts),
            "extractor",
            f"material '{m.name}': value_eur {m.value_eur} is not a value the user gave nor an "
            "amount_eur of get_exchange_rate: copy it or convert it.",
        )
        words = [w for w in _plain(m.country_name).split() if len(w) > 3]
        check(
            m.country == "DESCONOCIDO" or any(w in plain for w in words),
            "extractor",
            f"material '{m.name}': country '{m.country_name}' is not in the user's text: copy "
            "the country the user wrote, or DESCONOCIDO.",
        )
        check(
            not (
                m.hs_code == plan.product_hs_code
                and plan.exw_price_eur
                and m.value_eur >= plan.exw_price_eur
            ),
            "extractor",
            f"material '{m.name}' has the product's own HS code and is worth the whole ex-works "
            "price: it is the product itself, not one of its materials. Leave it out.",
        )

    # The evaluations (origin, rules, or extractor, depending on the error).
    for agreement, evaluation in state.get("evaluations", {}).items():
        if "error" in evaluation and (issue := _evaluation_issue(agreement, evaluation["error"])):
            issues.append(issue)

    return {"issues": issues, "attempts": attempts}
