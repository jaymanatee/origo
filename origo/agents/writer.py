"""Writer: the answer to the user, from the facts in the state and nothing else.

The code gathers the facts (verdicts, rules, materials, warnings, what is missing) and explains
every code in them (criteria, warnings, tolerance); the model only writes them up in the
user's language. Every figure is already computed: the model never adds, converts or guesses
one. Without the explanations it filled the gaps itself (it tied the tolerance to MAXNOM).

The code checks that every figure of the answer is in the facts, the question or the
glossary. If one is not, the answer is rejected once and written again; it is never edited."""

from __future__ import annotations

import json
import re

from origo.agents.prompts import DOMAIN_CONTEXT
from origo.agents.statement import statement_gaps
from origo.agents.verifier import _amounts
from origo.llm import chat
from origo.state import KnowledgeAnswer, OrigoState

GLOSSARY = """\
Criteria of a rule of origin (a rule lists alternatives: meeting one is enough). Use these
names in Spanish:
- WO (enteramente obtenido): wholly obtained in the EU.
- CC (cambio de capítulo): every non-originating material changes chapter (2 digits).
- CTH (cambio de partida): every non-originating material changes heading (4 digits).
- CTSH (cambio de subpartida): every non-originating material changes subheading (6 digits).
- MAXNOM X % (valor máximo de materiales no originarios): the non-originating materials are
  at most X % of the ex-works price.
- RVC X % (contenido de valor regional): at least X % (of the FOB price if it says FOB).
General tolerance (tolerancia general): only for CC, CTH and CTSH. Non-originating materials
that do not change classification may still be used if together they are at most that % of
the ex-works price. It never widens a MAXNOM or RVC threshold.
Agreements, in Spanish: TCA = Acuerdo de Comercio y Cooperación UE-Reino Unido; UE-JP =
Acuerdo de Asociación Económica UE-Japón; UE-KR = Acuerdo de Libre Comercio UE-Corea; CETA =
Acuerdo Económico y Comercial Global UE-Canadá."""

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
- Name each criterion and agreement as in the definitions above. Do not explain how a
  criterion is met beyond the trace of the result: the tool already checked it.
- If facts.results is empty and facts.missing is not: there is no verdict; ask the user for
  exactly what is missing. If there are results and facts.missing is not empty, give the
  results and ask for what is missing.
- For each agreement in facts.results: the verdict (originating or not), the criterion met (or
  why none is), the rule applied, and the materials that are non-originating and why.
- Explain each warning with its explanation in the facts.
- Say what could not be checked (not_checked) and any error, plainly, without a verdict.
- Agreements in facts.not_covered: the system does not cover them.
- facts.knowledge: the answers to the questions about the rules. Give each answer that has
  sources and name them after it (document and section). Status not_found: the knowledge base
  does not answer it; not_covered: it is about an agreement the system does not cover. In
  both cases say so and do not answer it.
- facts.unresolved: problems the system could not fix; say the answer may be affected.
- If facts.statement_requested: only facts.statement_missing says what the statement lacks.
  If it is empty, nothing is missing (no REX number is needed below 6,000 EUR): say the
  statement is ready and the user will be asked to confirm its data. Otherwise say it cannot
  be issued yet and why (facts.statement_missing).
- Clear and short: a summary first, then the detail per agreement. Markdown is fine."""
)

_NUMBER = re.compile(r"\d[\d.,]*\d|\d")


def unsupported_numbers(report: str, sources: list[str]) -> list[str]:
    """Figures of the report that no source contains. A figure passes if any reading of it
    (decimal point or comma, rounded to 0, 1 or 2 decimals) is a number of a source, or if
    its digits are a code of a source ('4407.92' and '440792'). Counters 0-10 always pass
    (section numbers, '4 digits')."""
    allowed, codes = set(), set()
    for source in sources:
        for value in _amounts(source):
            allowed |= {round(value, 2), round(value, 1), float(round(value))}
        codes |= set(re.findall(r"\d+", source))
    unsupported = set()
    for token in _NUMBER.findall(report):
        readings = {round(value, 2) for value in _amounts(token)}
        if any(value <= 10 and value == int(value) for value in readings):
            continue
        if readings & allowed or re.sub(r"\D", "", token) in codes:
            continue
        unsupported.add(token)
    return sorted(unsupported)


def _result(evaluation: dict) -> dict:
    """An evaluation for the writer: no internal id, warnings explained, tolerance named."""
    result = {k: v for k, v in evaluation.items() if k not in ("evaluation_id", "warnings")}
    if "tolerance_pct" in result:
        result["general_tolerance_pct"] = result.pop("tolerance_pct")
    if evaluation.get("warnings"):
        result["warnings"] = {w: WARNINGS.get(w, w) for w in evaluation["warnings"]}
    return result


def _missing(state: OrigoState) -> list[str]:
    """What the user must still give, computed by code (never the orchestrator's free text:
    it kept listing a REX number the statement did not need)."""
    plan = state["plan"]
    extracted = "materials" in state  # the extractor ran, and had its chance to convert the price
    missing = plan.gaps(converting=not extracted)
    if "exw_price_eur" in missing and plan.exw_price_currency:  # given, but not converted
        missing[missing.index("exw_price_eur")] = (
            f"exw_price_eur (the user gave it in {plan.exw_price_currency} and it could not be "
            "converted to euros)"
        )
    if extracted:
        if not state["materials"]:
            missing.append("the bill of materials (each material's HS code, country and value)")
        missing += state.get("materials_missing", [])
    return missing


KNOWLEDGE_STATUS = {
    "answered": "answered from the knowledge base",
    "not_found": "the knowledge base does not answer it",
    "not_covered": "it is about an agreement the system does not cover",
}


def _knowledge(answer: KnowledgeAnswer) -> dict:
    """An answer about the rules for the writer: only the passages it cites, as its sources."""
    return {
        "question": answer.question,
        "status": KNOWLEDGE_STATUS[answer.status],
        "answer": answer.answer,
        "sources": [
            {"document": p.source, "section": p.section, "text": p.text} for p in answer.sources()
        ],
    }


def _facts(state: OrigoState) -> dict:
    """What the writer may use: the results of the other nodes, without internal fields."""
    plan = state["plan"]
    facts = {
        "question_kind": plan.kind,
        "product": plan.product,
        "product_hs_code": plan.product_hs_code,
        "exw_price_eur": plan.exw_price_eur,
        "not_covered": plan.out_of_scope,
        "missing": _missing(state),
        "statement_requested": plan.statement is not None,
        "statement_missing": statement_gaps(state),
    }
    if state.get("knowledge"):
        facts["knowledge"] = [_knowledge(answer) for answer in state["knowledge"]]
    if plan.kind == "knowledge":
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
    """LangGraph node: writes state['report'] and the figures it could not back up."""
    run = state.get("run", "default")
    facts = json.dumps(_facts(state), ensure_ascii=False, indent=1)
    task = f"The user's message:\n\n{state['question']}\n\nThe facts:\n{facts}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    sources = [facts, state["question"], GLOSSARY]
    for attempt in range(2):  # one rewrite at most: never edited, never blocked
        report = chat(messages, agent="writer", run=run, temperature=0.2).content or ""
        unsupported = unsupported_numbers(report, sources)
        if not unsupported or attempt == 1:
            break
        messages += [
            {"role": "assistant", "content": report},
            {
                "role": "user",
                "content": f"These figures are not in the facts: {', '.join(unsupported)}. Write "
                "the whole answer again with only figures from the facts.",
            },
        ]
    return {"report": report, "unsupported_numbers": unsupported}