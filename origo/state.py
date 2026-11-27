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
# The partner country of each agreement (ISO 3166 alpha-2).
PARTNER = {"TCA": "GB", "UE-JP": "JP", "UE-KR": "KR", "CETA": "CA"}
Country = Literal["UE", "GB", "JP", "KR", "CA", "OTRO", "DESCONOCIDO"]
Kind = Literal["evaluate", "knowledge"]


def _hs6(value: str) -> str:
    """'8414.59', '8414 59' or '841459' -> '841459'; anything else is refused.

    '8501.1' is '8501.10': a code typed in Excel is stored as a number and loses its trailing
    zero (the subheading always has two digits after the point)."""
    text = str(value).strip()
    if re.fullmatch(r"\d{4}\.\d", text):
        text += "0"
    digits = re.sub(r"\D", "", text)
    if len(digits) < 6:
        raise ValueError(f"{value!r} is not an HS code of at least 6 digits")
    return digits[:6]


class Material(BaseModel):
    name: str = Field(description="Material name, as the user wrote it.")
    hs_code: str = Field(description="HS code of the material, as the user wrote it.")
    country_name: str = Field(
        description="The country where the material was made, as the user wrote it "
        "(e.g. 'Francia'), or 'desconocido'."
    )
    country: Country = Field(
        description="The code of country_name: UE for any EU member state (France, "
        "Germany, Italy...); GB, JP, KR or CA for the United Kingdom, Japan, South Korea or "
        "Canada; OTRO for any other country (China, United States...); DESCONOCIDO if the "
        "user does not say."
    )
    value_eur: float = Field(gt=0, description="Value of the material in euros.")
    supplier_declaration: bool = Field(
        default=False,
        description="True only if the user says the supplier gave a declaration of origin.",
    )

    @field_validator("hs_code", mode="before")
    @classmethod
    def _normalise_hs(cls, value):
        return _hs6(value)

    def origin_for(self, agreement: Agreement) -> str:
        """The origin evaluate_origin expects, for this agreement (computed, not asked)."""
        if self.country in ("UE", "DESCONOCIDO"):
            return self.country
        return "SOCIO" if self.country == PARTNER[agreement] else "TERCERO"


class StatementRequest(BaseModel):
    """The data of the statement on origin, when the user asks for one. Only what the user
    gave: a value the user did not give is null (and goes to the plan's missing), never made
    up; the verifier checks them against the question."""

    invoice_number: str | None = Field(
        default=None, description="Number of the invoice the statement goes on, or null."
    )
    exporter_name: str | None = Field(
        default=None, description="Name of the exporting company, or null."
    )
    place: str | None = Field(default=None, description="Place of issue (a city), or null.")
    consignment_value_eur: float | None = Field(
        default=None,
        gt=0,
        description="Total value of the consignment (the invoice) in euros, or null. Not the "
        "price of one product.",
    )
    exporter_reference: str | None = Field(
        default=None,
        description="REX number or authorised exporter number, exactly as the user wrote it, "
        "or null.",
    )
    agreement: Agreement | None = Field(
        default=None,
        description="The agreement the statement is for (where this invoice goes), or null if "
        "the user does not say.",
    )


class Plan(BaseModel):
    kind: Kind = Field(
        description="evaluate: the user wants to know whether a product is originating, or to "
        "issue an origin statement for it (the message may also ask about the rules: those "
        "questions go to knowledge_questions). knowledge: only questions about how the rules "
        "work, with no product to evaluate."
    )
    product: str | None = Field(default=None, description="The product, as the user names it.")
    product_hs_code: str | None = Field(
        default=None, description="HS code of the product, only if the user wrote it."
    )
    exw_price_eur: float | None = Field(
        default=None,
        gt=0,
        description="Ex-works price of the product, only if the user gave it in euros.",
    )
    exw_price_currency: str | None = Field(
        default=None,
        description="Only if the user gave the ex-works price in a currency other than the "
        "euro: its ISO code (USD, GBP, JPY...), and exw_price_eur null. Otherwise null.",
    )
    agreements: list[Agreement] = Field(
        default_factory=list,
        max_length=4,
        description="Agreements of the question that the system covers: TCA (United Kingdom), "
        "UE-JP (Japan), UE-KR (South Korea), CETA (Canada).",
    )
    out_of_scope: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Agreements or partner countries the user asks about that are not in "
        "'agreements' (e.g. Mercosur, Australia), as the user wrote them.",
    )
    statement: StatementRequest | None = Field(
        default=None,
        description="Only if the user asks to issue a statement on origin; otherwise null.",
    )
    knowledge_questions: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="The questions about how the rules work (validity, records, REX, "
        "procedures...), each one rewritten to be understood alone, without the rest of the "
        "message, in the user's language. Not whether the product is originating: that is the "
        "evaluation. Empty if there are none.",
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Data the user must give and did not, in a few words each. Nothing about "
        "the materials, no amount given in another currency, and nothing about the statement "
        "on origin: other agents and checks handle them.",
    )

    @field_validator("product_hs_code", mode="before")
    @classmethod
    def _normalise_hs(cls, value):
        return None if value in (None, "") else _hs6(value)

    def gaps(self, converting: bool = False) -> list[str]:
        """What an evaluation needs and the plan does not have, computed by code.

        ``converting``: before the extractor runs, a price in another currency is not missing,
        because the extractor converts it. Without this the graph went straight to the writer
        and asked the user for a price they had given in USD."""
        if self.kind != "evaluate":
            return []
        needed = {
            "product_hs_code": self.product_hs_code,
            "exw_price_eur": self.exw_price_eur or (converting and self.exw_price_currency),
            "agreements": self.agreements or self.out_of_scope,
        }
        return [name for name, value in needed.items() if not value]


class Passage(BaseModel):
    """A passage of the knowledge base, as the search returns it."""

    source: str  # document of the corpus, e.g. 'korea/protocol_rules_of_origin.md'
    section: str  # heading the passage is under
    text: str


class Answer(BaseModel):
    """What the model writes for one question about the rules (structured output)."""

    found: bool = Field(
        description="true if the passages answer the question; false if they do not."
    )
    answer: str = Field(
        description="The answer, only from the passages, with the figures as they say them. "
        "Empty if found is false."
    )
    cited: list[int] = Field(
        description="Numbers of the passages the answer comes from ([1] is 1). Empty if found "
        "is false."
    )


class KnowledgeAnswer(BaseModel):
    """One question about the rules and its answer, with the passages it was given."""

    question: str
    status: Literal["answered", "not_found", "not_covered"]
    # answered: the passages answer it; not_found: they do not; not_covered: the question is
    # only about agreements the system does not cover, so nothing was searched.
    answer: str = ""
    cited: list[int] = Field(default_factory=list)  # numbers of the passages, from 1
    passages: list[Passage] = Field(default_factory=list)  # what the search returned

    def sources(self) -> list[Passage]:
        """The passages the answer cites (the numbers that exist)."""
        return [self.passages[n - 1] for n in self.cited if 1 <= n <= len(self.passages)]


class Issue(BaseModel):
    """A problem the verifier found, and which agent has to redo its part."""

    agent: Literal["orchestrator", "extractor", "rules", "origin", "knowledge"]
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
    conversions: list[dict]  # extractor: get_exchange_rate results (amount -> amount_eur)
    materials_missing: list[str]  # extractor: materials left out (no HS code or no value)
    rules: Annotated[dict[str, dict], merge]  # rules agent: agreement -> get_origin_rule result
    evaluations: Annotated[dict[str, dict], merge]  # origin agent: agreement -> evaluate_origin
    operations: list[str] | None  # origin agent: what the producer does (None: manufacturing)
    knowledge: list[KnowledgeAnswer]  # knowledge agent: one per question about the rules
    issues: list[Issue]  # verifier; empty means everything checks
    attempts: int  # verifier loops so far, to stop before looping forever
    report: str  # writer: the opinion, material by material, with the rule cited
    unsupported_numbers: list[str]  # writer: figures of the report not found in the facts
    statement: dict | None  # writer, after the human confirmed: issued or why not