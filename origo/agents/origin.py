"""Origin agent: the verdict of each agreement, from evaluate_origin.

The code builds every call from the state (product, price, materials with their origin for
each agreement): the model never copies a value, so it cannot change one. The model only
decides what needs judgement, in one call for the whole run: which operations the producer
does, and, when a rule has several subdivisions, which one matches the product."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from origo.agents.prompts import DOMAIN_CONTEXT
from origo.engine.registry import ToolRegistry
from origo.engine.tools import Operation, evaluate_origin
from origo.llm import chat
from origo.state import Agreement, OrigoState

REGISTRY = ToolRegistry().register(evaluate_origin)


class Subdivision(BaseModel):
    agreement: Agreement
    number: int = Field(ge=1, description="Number of the rule set that matches the product.")


class Choices(BaseModel):
    evidence: str = Field(
        description="What the user says the producer does with the materials, in one sentence."
    )
    operations: list[Operation] | None = Field(
        default=None,
        description="null for normal manufacturing. Otherwise the operations the user names: "
        "fabricacion, montaje or mecanizado confer origin; embalaje, etiquetado, pintado or "
        "limpieza alone never do.",
    )
    subdivisions: list[Subdivision] = Field(
        default_factory=list,
        max_length=4,
        description="One per agreement listed under 'Rules with several subdivisions'; empty "
        "if there is none.",
    )


SYSTEM = (
    DOMAIN_CONTEXT
    + """

Your job: decide what the origin evaluation cannot know by itself. Do not evaluate the origin:
a tool does that with your choices.
- operations: from the user's words only.
- subdivisions: for each rule with several subdivisions, the number of the one whose
  description matches the product."""
)


def _subdivision_listing(rules: dict) -> str:
    """The rules with several rule sets, numbered, for the prompt."""
    lines = []
    for agreement, rule in rules.items():
        sets = rule.get("rule_sets", [])
        if len(sets) > 1:
            lines.append(f"{agreement}:")
            lines += [f"  {rs['number']}) {rs['subdivision']}" for rs in sets]
    return "\n".join(lines)


def origin(state: OrigoState) -> dict:
    """LangGraph node: writes state['evaluations'], agreement -> evaluate_origin result."""
    plan, rules, run = state["plan"], state.get("rules", {}), state.get("run", "default")
    listing = _subdivision_listing(rules)
    task = (
        f"The user's message:\n\n{state['question']}\n\nProduct: {plan.product} "
        f"(HS {plan.product_hs_code}).\n\nRules with several subdivisions:\n{listing or 'none'}"
    )
    message = chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}],
        agent="origin",
        run=run,
        schema=Choices,
    )
    choices = Choices.model_validate_json(message.content)
    chosen = {s.agreement: s.number for s in choices.subdivisions}

    evaluations = {}
    for agreement in plan.agreements:
        rule = rules.get(agreement, {})
        if "error" in rule or not rule:
            evaluations[agreement] = {"error": f"no rule of origin: {rule.get('error', 'missing')}"}
            continue
        arguments = {
            "agreement": agreement,
            "product_hs_code": plan.product_hs_code,
            "exw_price_eur": plan.exw_price_eur,
            "materials": [
                {
                    "name": m.name,
                    "hs_code": m.hs_code,
                    "origin": m.origin_for(agreement),
                    "value_eur": m.value_eur,
                    "supplier_declaration": m.supplier_declaration,
                }
                for m in state.get("materials", [])
            ],
            "operations": choices.operations,
            "subdivision": chosen.get(agreement) if len(rule["rule_sets"]) > 1 else None,
        }
        record = REGISTRY.call("evaluate_origin", arguments)
        evaluations[agreement] = record.result if record.ok else {"error": record.error}
    return {"evaluations": evaluations, "operations": choices.operations}
