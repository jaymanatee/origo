"""Writer: the answer to the user, from the facts in the state and nothing else.

The code gathers the facts (verdicts, rules, materials, warnings, what is missing) and explains
every code in them (criteria, warnings, tolerance); the model only writes them up in the
user's language. Every figure is already computed: the model never adds, converts or guesses
one. Without the explanations it filled the gaps itself (it tied the tolerance to MAXNOM)."""

from __future__ import annotations

import json

from origo.agents.prompts import DOMAIN_CONTEXT
from origo.llm import chat
from origo.state import OrigoState

GLOSSARY = """\
Criteria of a rule of origin (a rule lists alternatives: meeting one is enough):
- WO: wholly obtained in the EU.
- CC: every non-originating material changes chapter (2 digits) with respect to the product.
- CTH: every non-originating material changes heading (4 digits).
- CTSH: every non-originating material changes subheading (6 digits).
- MAXNOM X %: the non-originating materials are at most X % of the ex-works price.
- RVC X %: the regional value content is at least X % (of the FOB price if it says FOB).
General tolerance: only for CC, CTH and CTSH. Non-originating materials that do not change
classification may still be used if together they are at most that % of the ex-works price.
It never widens a MAXNOM or RVC threshold."""

# What each warning of the engine means (origo/engine/origin_engine.py, `flags`).
WARNINGS = {
    "material_misma_partida": "a non-originating material has the same heading (4 digits) as "
    "the product, so it does not change heading",
    "subdivision_distinta": "a non-originating material has the product's heading but another "
    "subheading: it changes subheading (CTSH) but not heading (CTH)",
    "excepcion_partida": "a non-originating material comes from a heading the rule excludes",
    "proveedor_sin_declaracion": "materials made in the EU or in the partner country count as "
    "non-originating without a supplier's declaration of origin; with one they could count as "
    "originating",
    "origen_desconocido": "a material of unknown origin counts as non-originating",
    "operacion_insuficiente": "the producer only packs, labels, paints or cleans: these "
    "operations never confer origin",
    "tolerancia_usada": "the verdict relies on the general tolerance",
    "umbral_exacto": "a value is exactly at a threshold",
    "tolerancia_no_amplia_maxnom": "the product misses a MAXNOM or RVC threshold by less than the "
    "tolerance, but the tolerance never widens those thresholds",
}

SYSTEM = (
    DOMAIN_CONTEXT
    + "\n\n"
    + GLOSSARY
    + """

Your job: write the answer to the user, in the user's language, from the facts you are given
and nothing else.
- Every figure, code, rule, verdict and explanation comes from the facts and the definitions
  above. Never add one of your own, never compute a new one, no advice of your own.
- If facts.missing is not empty: there is no verdict. Ask the user for exactly what is missing.
- For each agreement in facts.results: the verdict (originating or not), the criterion met (or
  why none is), the rule applied, and the materials that are non-originating and why.
- Explain each warning with its explanation in the facts.
- Say what could not be checked (not_checked) and any error, plainly, without a verdict.
- Agreements in facts.not_covered: the system does not cover them.
- facts.unresolved: problems the system could not fix; say the answer may be affected.
- If facts.statement_requested: the statement on origin can only be issued for an originating
  product, after the user confirms its data.
- Clear and short: a summary first, then the detail per agreement. Markdown is fine."""
)


def _result(evaluation: dict) -> dict:
    """An evaluation for the writer: no internal id, warnings explained, tolerance named."""
    result = {k: v for k, v in evaluation.items() if k not in ("evaluation_id", "warnings")}
    if "tolerance_pct" in result:
        result["general_tolerance_pct"] = result.pop("tolerance_pct")
    if evaluation.get("warnings"):
        result["warnings"] = {w: WARNINGS.get(w, w) for w in evaluation["warnings"]}
    return result


def _facts(state: OrigoState) -> dict:
    """What the writer may use: the results of the other nodes, without internal fields."""
    plan = state["plan"]
    facts = {
        "question_kind": plan.kind,
        "product": plan.product,
        "product_hs_code": plan.product_hs_code,
        "exw_price_eur": plan.exw_price_eur,
        "not_covered": plan.out_of_scope,
        "missing": plan.missing + plan.gaps(),
        "statement_requested": plan.statement is not None,
    }
    if plan.kind == "knowledge":
        facts["knowledge"] = "The knowledge base is not available yet: say so, without answering."
        return facts
    facts["materials"] = [
        m.model_dump(include={"name", "hs_code", "country_name", "value_eur"})
        for m in state.get("materials", [])
    ]
    facts["conversions"] = [
        {k: c[k] for k in ("currency", "month", "amount", "amount_eur")}
        for c in state.get("conversions", [])
    ]
    facts["operations"] = state.get("operations") or "normal manufacturing"
    facts["results"] = {
        agreement: _result(evaluation)
        for agreement, evaluation in state.get("evaluations", {}).items()
    }
    facts["unresolved"] = [issue.message for issue in state.get("issues", [])]
    return facts


def writer(state: OrigoState) -> dict:
    """LangGraph node: writes state['report']."""
    facts = json.dumps(_facts(state), ensure_ascii=False, indent=1)
    task = f"The user's message:\n\n{state['question']}\n\nThe facts:\n{facts}"
    message = chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}],
        agent="writer",
        run=state.get("run", "default"),
    )
    return {"report": message.content or ""}
