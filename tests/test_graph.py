"""The routes of the graph, with fake nodes: no model, no API, no network."""

import origo.graph as graph
from origo.state import Issue, Material, Plan

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


def fake_nodes(monkeypatch, calls, plan=PLAN, materials=MATERIALS, issues_first=None):
    """Replace every node by a fake that records its name and returns fixed values."""

    def node(name, update):
        def run(state):
            calls.append(name)
            return update(state) if callable(update) else update

        run.__name__ = name
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
    node("origin", {"evaluations": {"TCA": {}}, "operations": None})
    node("verifier", verifier)
    node("writer", {"report": "ok"})


def test_full_route(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls)
    state = graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "rules", "origin", "verifier", "writer"]
    assert state["report"] == "ok"


def test_knowledge_and_missing_data_go_to_the_writer(monkeypatch):
    for plan in (Plan(kind="knowledge"), Plan(kind="evaluate", agreements=["TCA"])):
        calls = []
        fake_nodes(monkeypatch, calls, plan=plan)
        graph.build().invoke({"question": "q"})
        assert calls == ["orchestrator", "writer"]


def test_no_materials_go_to_the_writer(monkeypatch):
    calls = []
    fake_nodes(monkeypatch, calls, materials=[])
    graph.build().invoke({"question": "q"})
    assert calls == ["orchestrator", "extractor", "writer"]


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
