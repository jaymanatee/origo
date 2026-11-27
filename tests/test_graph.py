"""The routes of the graph, with fake nodes: no model, no API, no network."""

from origo import graph
from origo.state import Issue, Material, Plan, StatementRequest

PLAN = Plan(
    kind="evaluate",
    product="silla",
    product_hs_code="940161",
    exw_price_eur=120,
    agreements=["TCA"],
)
MATERIALS = [
    Material(name="madera", hs_code="440792", country_name="Francia", country="UE", value_eur=30)
]
STATEMENT_PLAN = PLAN.model_copy(
    update={
        "statement": StatementRequest(
            invoice_number="F-1", exporter_name="ACME", place="Valencia", consignment_value_eur=1200
        )
    }
)
USD_PLAN = PLAN.model_copy(update={"exw_price_eur": None, "exw_price_currency": "USD"})
RECORDS = "¿Cuánto tiempo hay que conservar los documentos?"
MIXED_PLAN = PLAN.model_copy(update={"knowledge_questions": [RECORDS]})
KNOWLEDGE_PLAN = Plan(kind="knowledge", agreements=["TCA"], knowledge_questions=[RECORDS])


def fake_nodes(
    monkeypatch, calls, plan=PLAN, materials=MATERIALS, issues_first=None, evaluations=None
):
    """Replace every node but confirm by a fake that records its name and returns fixed
    values."""

    def node(name, update):
        def run(state):
            calls.append(name)
            return update(state) if callable(update) else update

        monkeypatch.setattr(graph, name, run)

    def verifier(state):
        attempts = state.get("attempts", 0) + 1
        return {
            "issues": issues_first if attempts == 1 and issues_first else [],
            "attempts": attempts,
        }

    node("orchestrator", {"plan": plan})
    node("extractor", {"materials": materials})
    node("rules", {"rules": {"TCA": {}}})
    node("origin", {"evaluations": evaluations or {"TCA": {}}, "operations": None})
    node("knowledge", {"knowledge": []})
    node("verifier", verifier)
    node("writer", {"report": "ok"})
    node("issue", {"statement": {"confirmed": True, "path": "F-1.txt"}})


def test_full_route(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls)
    state = graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "rules", "origin", "verifier", "writer"]
    assert state["report"] == "ok"


def test_nothing_to_evaluate_goes_through_the_checks_to_the_writer(monkeypatch):
    only_mercosur = PLAN.model_copy(update={"agreements": [], "out_of_scope": ["Mercosur"]})
    missing_data = Plan(kind="evaluate", agreements=["TCA"])
    for plan in (Plan(kind="knowledge"), missing_data, only_mercosur):
        calls = []
        fake_nodes(monkeypatch, calls, plan=plan)
        graph.build().invoke({"question": "q"})
        assert calls == ["orchestrator", "verifier", "writer"]


def test_a_price_in_another_currency_is_not_missing_until_the_extractor_tried():
    no_price = PLAN.model_copy(update={"exw_price_eur": None})
    assert graph.after_plan({"plan": no_price}) == "verifier"
    assert graph.after_plan({"plan": USD_PLAN}) == "extractor"
    converted = USD_PLAN.model_copy(update={"exw_price_eur": 110.5})
    assert graph.after_extraction({"plan": converted, "materials": MATERIALS}) == "rules"
    assert graph.after_extraction({"plan": USD_PLAN, "materials": MATERIALS}) == "verifier"


def test_a_price_in_another_currency_is_converted_and_evaluated(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, plan=USD_PLAN)
    converted = {
        "plan": USD_PLAN.model_copy(update={"exw_price_eur": 110.5}),
        "materials": MATERIALS,
    }
    monkeypatch.setattr(graph, "extractor", lambda state: calls.append("extractor") or converted)
    graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "rules", "origin", "verifier", "writer"]


def test_a_price_not_converted_goes_to_the_writer(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, plan=USD_PLAN)  # the fake extractor leaves the price as it is
    graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "verifier", "writer"]


def test_no_materials_go_to_the_writer(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, materials=[])
    graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "verifier", "writer"]


def test_an_issue_sends_back_to_the_first_agent_that_must_redo(monkeypatch):
    calls = []
    issues = [Issue(agent="origin", message="x"), Issue(agent="extractor", message="y")]
    fake_nodes(monkeypatch, calls, issues_first=issues)
    state = graph.build().invoke({"question": "q"})
    assert calls == [
        "orchestrator",
        "extractor",
        "rules",
        "origin",
        "verifier",
        "extractor",
        "rules",
        "origin",
        "verifier",
        "writer",
    ]
    assert state["attempts"] == 2


def test_out_of_attempts_goes_to_the_writer(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls)
    monkeypatch.setattr(
        graph,
        "verifier",
        lambda state: {
            "issues": [Issue(agent="origin", message="x")],
            "attempts": state.get("attempts", 0) + 1,
        },
    )
    state = graph.build().invoke({"question": "q"})
    assert state["attempts"] == graph.MAX_ATTEMPTS
    assert state["report"] == "ok"


def test_without_verifier(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls)
    graph.build(verify=False).invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "rules", "origin", "writer"]


# --- questions about the rules (knowledge agent)


def test_a_question_about_the_rules_goes_to_the_knowledge_agent(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, plan=KNOWLEDGE_PLAN)
    graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "knowledge", "verifier", "writer"]


def test_a_mixed_question_evaluates_and_then_asks_the_knowledge_agent(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, plan=MIXED_PLAN)
    graph.build().invoke({"question": "q"})
    assert calls == [
        "orchestrator",
        "extractor",
        "rules",
        "origin",
        "knowledge",
        "verifier",
        "writer",
    ]


def test_a_mixed_question_with_missing_data_still_gets_its_answer_about_the_rules(monkeypatch):
    calls = []
    plan = MIXED_PLAN.model_copy(update={"exw_price_eur": None})
    fake_nodes(monkeypatch, calls, plan=plan)
    graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "knowledge", "verifier", "writer"]


def test_a_bad_citation_only_redoes_the_knowledge_agent(monkeypatch):
    calls = []
    issues = [Issue(agent="knowledge", message="x")]
    fake_nodes(monkeypatch, calls, plan=MIXED_PLAN, issues_first=issues)
    graph.build().invoke({"question": "q"})
    assert calls == [
        "orchestrator",
        "extractor",
        "rules",
        "origin",
        "knowledge",
        "verifier",
        "knowledge",  # only the knowledge agent is redone
        "verifier",
        "writer",
    ]


def test_an_extractor_issue_redoes_the_knowledge_agent_too(monkeypatch):
    calls = []
    issues = [Issue(agent="extractor", message="x")]
    fake_nodes(monkeypatch, calls, plan=MIXED_PLAN, issues_first=issues)
    graph.build().invoke({"question": "q"})
    after_first_check = calls[calls.index("verifier") + 1 :]
    assert after_first_check == ["extractor", "rules", "origin", "knowledge", "verifier", "writer"]


def test_without_verifier_the_knowledge_agent_goes_to_the_writer(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, plan=MIXED_PLAN)
    graph.build(verify=False).invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "rules", "origin", "knowledge", "writer"]


ORIGINATING = {"TCA": {"originating": True, "evaluation_id": "EV-X"}}


def test_statement_issued_after_yes(monkeypatch):
    calls, shown = [], []
    fake_nodes(monkeypatch, calls, plan=STATEMENT_PLAN, evaluations=ORIGINATING)
    state = graph.ask("q", confirm=lambda data: shown.append(data) or True)
    assert calls[-2:] == ["writer", "issue"]
    assert shown[0]["invoice_number"] == "F-1"
    assert shown[0]["evaluation_id"] == "EV-X"
    assert state["statement"]["path"] == "F-1.txt"


def test_statement_not_issued_after_no(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, plan=STATEMENT_PLAN, evaluations=ORIGINATING)
    state = graph.ask("q", confirm=lambda data: False)
    assert "issue" not in calls
    assert state["statement"] == {"confirmed": False}


def test_statement_not_offered_when_not_originating(monkeypatch):
    calls, shown = [], []
    not_originating = {"TCA": {"originating": False, "evaluation_id": "EV-Y"}}
    fake_nodes(monkeypatch, calls, plan=STATEMENT_PLAN, evaluations=not_originating)
    graph.ask("q", confirm=lambda data: shown.append(data) or True)
    assert shown == []
    assert calls[-1] == "writer"


def test_statement_not_offered_without_its_data(monkeypatch):
    calls, shown = [], []
    plan = PLAN.model_copy(update={"statement": StatementRequest(place="Valencia")})
    fake_nodes(monkeypatch, calls, plan=plan, evaluations=ORIGINATING)
    graph.ask("q", confirm=lambda data: shown.append(data) or True)
    assert shown == []