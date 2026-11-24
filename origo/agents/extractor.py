"""Extractor: the bill of materials, from the question or an attached Excel, in euros."""

from __future__ import annotations

import re

import pandas as pd
from pydantic import BaseModel, Field

from origo.agents.prompts import DOMAIN_CONTEXT
from origo.agents.tooling import use_tools
from origo.engine.registry import ToolRegistry
from origo.engine.tools import ECB_CURRENCIES, get_exchange_rate
from origo.llm import chat
from origo.state import Material, OrigoState


class Extraction(BaseModel):
    materials: list[Material] = Field(max_length=50, description="The bill of materials.")
    exw_price_eur: float | None = Field(
        default=None,
        gt=0,
        description="Ex-works price of the product in euros, only if the user gave it in "
        "another currency and get_exchange_rate converted it.",
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Only materials left out because the user gave no HS code or no value, in "
        "a few words each, in the user's language. A value converted from another currency is "
        "given, not missing.",
    )


SYSTEM = (
    DOMAIN_CONTEXT
    + """

Your job: extract the bill of materials of the product (name, HS code, country where it was
made, value in euros, supplier declaration).
- Copy names, codes and values from the user's text or file. Never make up a value.
- A material without HS code or without value: leave it out and say so in missing. Nothing
  else goes in missing (no supplier declaration just means supplier_declaration false).
- A value in another currency: convert it with get_exchange_rate (currency, the month the user
  names, amount) and use its amount_eur. The same for the product's ex-works price. A value
  converted this way is given, not missing."""
)

# An amount in a currency other than the euro: the ECB codes, symbols and their names.
FOREIGN = re.compile(
    r"\b(?:" + "|".join(sorted(ECB_CURRENCIES)) + r")\b|[$£¥₩]"
    r"|d[oó]lar|libras?\b|yen(?:es)?\b|wons?\b|yuan|francos? suizos?",
    re.IGNORECASE,
)


def read_bom(path: str, max_rows: int = 200) -> str:
    """The Excel as CSV text: the model maps the columns, whatever their names."""
    table = pd.read_excel(path, dtype=str).dropna(how="all").head(max_rows)
    return table.to_csv(index=False)


def extractor(state: OrigoState) -> dict:
    """LangGraph node: writes state['materials'] and completes the plan."""
    plan, run = state["plan"], state.get("run", "default")
    text = state["question"]
    if state.get("bom_file"):
        text += "\n\nAttached bill of materials (CSV):\n" + read_bom(state["bom_file"])
    task = (
        f"The user's message:\n\n{text}\n\nYour task is only the bill of materials: do not "
        "answer the user's question, other agents do that."
    )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]

    if FOREIGN.search(text):  # phase 1, only when there is something to convert
        registry = ToolRegistry().register(get_exchange_rate)
        use_tools(messages, registry, agent="extractor", run=run)  # adds calls and results
        messages.append({"role": "user", "content": "Now write the extraction as JSON."})

    message = chat(messages, agent="extractor", run=run, schema=Extraction)
    extraction = Extraction.model_validate_json(message.content)

    update = {"missing": plan.missing + extraction.missing}
    if plan.exw_price_eur is None and extraction.exw_price_eur is not None:
        update["exw_price_eur"] = extraction.exw_price_eur
    return {"materials": extraction.materials, "plan": plan.model_copy(update=update)}
