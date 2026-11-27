"""The checks made in code (verifier, what is missing), on fixed states: no model, no API, no
network."""

import pytest

from origo.agents.verifier import verifier
from origo.agents.writer import _missing
from origo.state import Material, Plan

CHAIRS = (
    "Fabrico sillas de madera (9401.61) y las vendo a 120 EUR franco fábrica. Materiales: "
    "madera de haya de Francia (4407.92) por 30 EUR, tela de Corea (5407.61) por 40 EUR, "
    "y tornillos de China (7318.15) que compré en septiembre de 2026 por 6 USD. "
    "¿Son originarias para Corea, Reino Unido y Mercosur?"
)
CHAIRS_PLAN = Plan(
    kind="evaluate",
    product="sillas de madera",
    product_hs_code="9401.61",
    exw_price_eur=120,
    agreements=["UE-KR", "TCA"],
    out_of_scope=["Mercosur"],
)
SCREWS_IN_EUR = {"currency": "USD", "month": "2026-09", "amount": 6, "amount_eur": 5.2}
FAN_PLAN = Plan(
    kind="evaluate",
    product="ventilador",
    product_hs_code="841451",
    exw_price_eur=200,
    agreements=["TCA"],
)


def material(name="motor", hs_code="850110", country_name="China", country="OTRO", value=50):
    return Material(
        name=name, hs_code=hs_code, country_name=country_name, country=country, value_eur=value
    )


CHAIRS_MATERIALS = [
    material("haya", "440792", "Francia", "UE", 30),
    material("tela", "540761", "Corea", "KR", 40),
    material("tornillos", "731815", "China", "OTRO", 5.2),
]


def issues(question, plan=FAN_PLAN, materials=(), conversions=()):
    state = {
        "question": question,
        "plan": plan,
        "materials": list(materials),
        "conversions": list(conversions),
    }
    return [(issue.agent, issue.message) for issue in verifier(state)["issues"]]


def test_the_chairs_pass():
    assert issues(CHAIRS, CHAIRS_PLAN, CHAIRS_MATERIALS, [SCREWS_IN_EUR]) == []


# --- an amount in another currency is not a value in euros


def test_a_material_in_dollars_copied_as_euros_is_an_issue():
    copied = [*CHAIRS_MATERIALS[:2], material("tornillos", "731815", "China", "OTRO", 6)]
    found = issues(CHAIRS, CHAIRS_PLAN, copied, [SCREWS_IN_EUR])
    assert [agent for agent, _ in found] == ["extractor"]
    assert "tornillos" in found[0][1]


@pytest.mark.parametrize("price", ["200 USD", "USD 200", "$200", "200 dólares", "£200", "200 GBP"])
def test_a_price_in_another_currency_copied_as_euros_is_an_issue(price):
    question = f"Vendo ventiladores (8414.51) a {price}. Motor (8501.10) de China por 50 EUR."
    found = issues(question, materials=[material()])
    assert [agent for agent, _ in found] == ["orchestrator"]
    assert "exw_price_currency" in found[0][1]


def test_a_converted_price_passes():
    question = "Vendo ventiladores (8414.51) a 230 USD. Motor (8501.10) de China por 50 EUR."
    conversion = {"currency": "USD", "month": "2026-10", "amount": 230, "amount_eur": 200}
    assert issues(question, materials=[material()], conversions=[conversion]) == []


# --- country names


@pytest.mark.parametrize("country", ["UK", "USA", "EE.UU."])
def test_a_short_country_name_is_found(country):
    question = f"Ventiladores (8414.51) a 200 EUR. Motor (8501.10) de {country} por 50 EUR."
    assert issues(question, materials=[material(country_name=country)]) == []


def test_a_short_country_name_must_be_a_whole_word():
    question = "Ventiladores (8414.51) a 200 EUR. Motor (8501.10) de China por 50 EUR, a causa..."
    found = issues(question, materials=[material(country_name="USA")])
    assert [agent for agent, _ in found] == ["extractor"]  # 'usa' is in 'causa', not a word


def test_a_country_the_user_did_not_write_is_an_issue():
    question = "Ventiladores (8414.51) a 200 EUR. Motor (8501.10) de China por 50 EUR."
    found = issues(question, materials=[material(country_name="Estados Unidos de América")])
    assert [agent for agent, _ in found] == ["extractor"]  # 'de' alone does not count


# --- HS codes of 8 and 10 digits (CN, TARIC)


@pytest.mark.parametrize(
    ("product", "motor"),
    [("84145100", "85011000"), ("8414510000", "8501100000"), ("8414.51.00", "8501.10.00")],
)
def test_cn_and_taric_codes_are_found(product, motor):
    question = f"Ventiladores ({product}) a 200 EUR. Motor ({motor}) de China por 50 EUR."
    assert issues(question, materials=[material()]) == []


# --- what the writer says is missing


def test_a_price_in_another_currency_is_not_missing_before_the_extractor():
    plan = FAN_PLAN.model_copy(
        update={"product_hs_code": None, "exw_price_eur": None, "exw_price_currency": "USD"}
    )
    assert _missing({"plan": plan}) == ["product_hs_code"]


def test_a_price_not_converted_is_missing_and_says_why():
    plan = FAN_PLAN.model_copy(update={"exw_price_eur": None, "exw_price_currency": "USD"})
    missing = _missing({"plan": plan, "materials": [material()]})
    assert len(missing) == 1
    assert "USD" in missing[0] and "could not be converted" in missing[0]