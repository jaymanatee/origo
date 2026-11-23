"""What the agents know and pass on: the state that travels through the graph.

Two levels:

* What each agent produces is a Pydantic model (``Plan``, ``Material``, ``Issue``...): it is
  validated the moment the agent writes it, so a bad value fails in that agent, not three
  agents later. The same models give the JSON schema the model is asked to follow.
* ``OrigoState`` is the container that goes from node to node. Each node receives it whole
  and returns only the fields it changed; LangGraph merges them in. ``rules`` and
  ``evaluations`` are kept per agreement and merged (not overwritten), because one question
  may ask about several markets and each market can be worked on in parallel.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator

Agreement = Literal["TCA", "UE-JP", "UE-KR", "CETA"]
Origin = Literal["UE", "SOCIO", "TERCERO", "DESCONOCIDO"]


def _hs6(value: str) -> str:
    """'8414.59', '8414 59' or '841459' -> '841459'; anything else is refused."""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 6:
        raise ValueError(f"{value!r} is not an HS code of at least 6 digits")
    return digits[:6]


class Material(BaseModel):
    """One line of the bill of materials, as the extractor reads it."""

    name: str
    hs_code: str = Field(description="HS code of the material, 6 digits")
    origin: Origin = Field(
        description="UE: made in the EU. SOCIO: made in the partner country. "
        "TERCERO: any other country. DESCONOCIDO: unknown"
    )
    value_eur: float = Field(gt=0, description="Value of the material in euros")
    supplier_declaration: bool = Field(
        default=False, description="True if the supplier gave a supplier's declaration"
    )

    @field_validator("hs_code", mode="before")
    @classmethod
    def _normalise_hs(cls, value):
        return _hs6(value)


class StatementRequest(BaseModel):
    """The data of the statement on origin, when the user asks for one. Only what the user
    gave: the verifier checks these values against the question (never made up)."""

    invoice_number: str
    exporter_name: str
    place: str
    consignment_value_eur: float = Field(gt=0)
    exporter_reference: str = ""


class Plan(BaseModel):
    """What the orchestrator understood from the question."""

    product: str
    product_hs_code: str
    exw_price_eur: float | None = Field(default=None, gt=0)
    agreements: list[Agreement] = Field(min_length=1)
    statement: StatementRequest | None = None
    missing: list[str] = Field(
        default_factory=list,
        description="Data the user has to give before the product can be evaluated",
    )

    @field_validator("product_hs_code", mode="before")
    @classmethod
    def _normalise_hs(cls, value):
        return _hs6(value)


class Issue(BaseModel):
    """A problem the verifier found, and which agent has to redo its part."""

    agent: Literal["orchestrator", "extractor", "rules", "origin"]
    message: str


def merge(old: dict, new: dict) -> dict:
    """Reducer for the per-agreement fields: parallel branches add their own key."""
    return {**(old or {}), **(new or {})}


class OrigoState(TypedDict, total=False):
    run: str  # id of this run, for the cost meter (llm.METER)
    question: str  # the user's question
    bom_file: str | None  # path of the bill of materials (Excel), if any
    plan: Plan  # orchestrator
    materials: list[Material]  # extractor
    rules: Annotated[dict[str, dict], merge]  # rules agent: agreement -> get_origin_rule result
    evaluations: Annotated[dict[str, dict], merge]  # origin agent: agreement -> evaluate_origin
    issues: list[Issue]  # verifier; empty means everything checks
    attempts: int  # verifier loops so far, to stop before looping forever
    report: str  # writer: the opinion, material by material, with the rule cited
    statement: dict | None  # writer, after the human confirmed: issued or why not
