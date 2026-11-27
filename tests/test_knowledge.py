"""The questions about the rules: the search filter, the knowledge agent, its checks and what
the writer gets. A fake search and a fake model: no corpus, no model, no network."""

import json
from types import SimpleNamespace

from origo.agents import knowledge as knowledge_module
from origo.agents.knowledge import knowledge
from origo.agents.verifier import verifier
from origo.agents.writer import _facts
from origo.rag.search import keep_agreements, search
from origo.state import Issue, KnowledgeAnswer, Passage, Plan

RECORDS = "¿Cuánto tiempo hay que conservar los documentos?"
CETA_PLAN = Plan(kind="knowledge", agreements=["CETA"], knowledge_questions=[RECORDS])
CETA = Passage(source="ceta/protocol_origin.md", section="Article 28", text="three years")
KOREA = Passage(source="korea/protocol_rules_of_origin.md", section="Article 18", text="five years")
REX = Passage(source="guides/aeat_rex.pdf", section="REX", text="the REX number does not expire")


# --- the search: filtered by the plan's agreements


def test_the_filter_keeps_the_agreements_of_the_plan_and_general_documents():
    keep = keep_agreements(["UE-KR"])
    assert keep(KOREA.source)
    assert keep(REX.source)  # a general document
    assert not keep(CETA.source)
    assert not keep("tca/rules_of_origin.md")
    assert keep_agreements([]) is None  # no agreement: nothing filtered


def test_search_asks_the_retriever_for_five_passages_with_the_filter():
    asked = {}

    class Retriever:
        def search(self, query, top_k, keep):
            asked.update(query=query, top_k=top_k, keep=keep)
            chunk = SimpleNamespace(source=CETA.source, section=CETA.section, text=CETA.text)
            return [SimpleNamespace(chunk=chunk, score=1.0)]

    assert search(RECORDS, ["CETA"], retriever=Retriever()) == [CETA]
    assert asked["top_k"] == 5
    assert asked["keep"](CETA.source) and not asked["keep"](KOREA.source)


# --- the knowledge agent


def fake(monkeypatch, found=True, cited=(1,), passages=(CETA, REX)):
    """A fake search and a fake model; returns what they were asked."""
    seen = {"searches": [], "prompts": []}

    def fake_search(question, agreements):
        seen["searches"].append((question, agreements))
        return list(passages)

    def fake_chat(messages, **kwargs):
        seen["prompts"].append(messages[-1]["content"])
        answer = {"found": found, "answer": "Three years." if found else "", "cited": list(cited)}
        return SimpleNamespace(content=json.dumps(answer))

    monkeypatch.setattr(knowledge_module, "search", fake_search)
    monkeypatch.setattr(knowledge_module, "chat", fake_chat)
    return seen


def test_the_agent_searches_with_the_agreements_of_the_plan(monkeypatch):
    seen = fake(monkeypatch)
    (answer,) = knowledge({"plan": CETA_PLAN})["knowledge"]
    assert seen["searches"] == [(RECORDS, ["CETA"])]  # the question names no agreement
    assert answer.status == "answered"
    assert answer.sources() == [CETA]
    assert "[1] (ceta/protocol_origin.md)" in seen["prompts"][0]


def test_passages_that_do_not_answer_give_no_answer(monkeypatch):
    fake(monkeypatch, found=False, cited=(2,))
    (answer,) = knowledge({"plan": CETA_PLAN})["knowledge"]
    assert answer.status == "not_found"
    assert answer.answer == "" and answer.cited == []


def test_an_agreement_not_covered_is_not_searched(monkeypatch):
    seen = fake(monkeypatch)
    plan = Plan(kind="knowledge", out_of_scope=["Australia"], knowledge_questions=[RECORDS])
    (answer,) = knowledge({"plan": plan})["knowledge"]
    assert answer.status == "not_covered"
    assert seen["searches"] == [] and seen["prompts"] == []


def test_a_general_question_is_searched_without_filter(monkeypatch):
    seen = fake(monkeypatch, passages=(REX,))
    plan = Plan(kind="knowledge", knowledge_questions=["¿Caduca el número REX?"])
    knowledge({"plan": plan})
    assert seen["searches"] == [("¿Caduca el número REX?", [])]


def test_the_retry_gets_the_verifier_problems(monkeypatch):
    seen = fake(monkeypatch)
    issues = [Issue(agent="knowledge", message="cites [7], but the passages are 1 to 2")]
    knowledge({"plan": CETA_PLAN, "issues": issues})
    assert "cites [7]" in seen["prompts"][0]


# --- the verifier's checks of the answers


def check(answer: KnowledgeAnswer, plan=CETA_PLAN):
    state = {"question": RECORDS, "plan": plan, "knowledge": [answer]}
    return [issue.message for issue in verifier(state)["issues"]]


def answered(cited, passages=(CETA, REX)):
    return KnowledgeAnswer(
        question=RECORDS, status="answered", answer="x", cited=list(cited), passages=list(passages)
    )


def test_a_good_answer_passes():
    assert check(answered([1, 2])) == []


def test_an_answer_must_cite_a_passage():
    assert "cites no passage" in check(answered([]))[0]


def test_an_answer_cannot_cite_a_passage_it_was_not_given():
    assert "the passages are 1 to 2" in check(answered([7]))[0]


def test_an_answer_cannot_cite_another_agreement():
    (problem,) = check(answered([1], passages=(KOREA,)))
    assert "another agreement" in problem


def test_answers_without_an_answer_are_not_checked():
    assert check(KnowledgeAnswer(question=RECORDS, status="not_found")) == []


def test_a_knowledge_plan_without_its_questions_goes_back_to_the_orchestrator():
    state = {"question": RECORDS, "plan": Plan(kind="knowledge", agreements=["CETA"])}
    assert [issue.agent for issue in verifier(state)["issues"]] == ["orchestrator"]


def test_issues_of_the_knowledge_agent_go_back_to_it():
    state = {"question": RECORDS, "plan": CETA_PLAN, "knowledge": [answered([])]}
    assert [issue.agent for issue in verifier(state)["issues"]] == ["knowledge"]


# --- what the writer gets


def test_the_writer_gets_the_answer_and_only_the_cited_passages():
    facts = _facts({"question": RECORDS, "plan": CETA_PLAN, "knowledge": [answered([1])]})
    (item,) = facts["knowledge"]
    assert item["status"] == "answered from the knowledge base"
    assert item["sources"] == [
        {"document": CETA.source, "section": CETA.section, "text": CETA.text}
    ]


def test_the_writer_is_told_why_there_is_no_answer():
    not_covered = KnowledgeAnswer(question=RECORDS, status="not_covered")
    facts = _facts({"question": RECORDS, "plan": CETA_PLAN, "knowledge": [not_covered]})
    assert facts["knowledge"][0]["status"] == "it is about an agreement the system does not cover"
    assert facts["knowledge"][0]["sources"] == []