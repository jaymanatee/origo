"""Orchestrator: the user's question -> a Plan (structured output)."""

from __future__ import annotations

from pathlib import Path

from origo.agents.prompts import DOMAIN_CONTEXT, issues_note
from origo.llm import chat
from origo.state import OrigoState, Plan

SYSTEM = (
    DOMAIN_CONTEXT
    + """

Your job: read the user's question and write the plan of the work, as JSON.
- kind: "evaluate" if the user has a product and wants to know whether it is originating, or
  wants an origin statement for it (even if the message also asks about the rules);
  "knowledge" if the user only asks how the rules work.
- knowledge_questions: each question about how the rules work (how long a statement is
  valid, how long to keep records, REX, who may make out a statement...), rewritten so that
  it is understood alone, in the user's language. Not whether the product is originating:
  that is the evaluation. The agreement does not need to be in the question: it goes in
  agreements, like the rest of the message. Empty if there are none.
- agreements: the covered agreements the user asks about, by their code. A country is its
  agreement (United Kingdom -> TCA). Each one once.
- out_of_scope: agreements or countries the user asks about that are not covered, as written.
- product_hs_code and exw_price_eur: only if the user wrote them; the price only if it is in
  euros. A price in another currency: exw_price_eur null and exw_price_currency its ISO code
  (USD, GBP...); another agent converts it. Never deduce an HS code from the product's
  description.
- statement: only if the user asks to issue an origin statement; copy only what the user
  gives and null for the rest. Do not list its missing data in missing: the system checks
  the statement by itself.
- missing: what the user must still give for the work, in a few words each, in the user's
  language. If the user gives no materials and attaches no file, say that the materials are
  missing; otherwise nothing about them (codes, values, currencies), and no amount in another
  currency: another agent reads and converts them."""
)


def orchestrator(state: OrigoState) -> dict:
    """LangGraph node: writes state['plan']."""
    question = state["question"]
    if state.get("bom_file"):
        question += f"\n\n[Attached bill of materials: {Path(state['bom_file']).name}]"
    question += issues_note(state, "orchestrator")
    message = chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
        agent="orchestrator",
        run=state.get("run", "default"),
        schema=Plan,
    )
    return {"plan": Plan.model_validate_json(message.content)}