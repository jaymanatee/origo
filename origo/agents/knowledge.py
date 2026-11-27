"""Knowledge agent: each question about the rules -> an answer from the knowledge base.

For each question of ``plan.knowledge_questions``:

1. the code searches the 5 best passages, from the plan's agreements and general documents
   (``origo/rag/search.py``), not from the agreements the question happens to name;
2. the model answers from those passages only, and says which ones it used (structured
   output), or says that they do not answer it.

The citations are checked by the verifier, and the writer explains the answer with its
sources. If the message only asks about agreements the system does not cover (Australia),
nothing is searched: the corpus would answer with the rule of another agreement.
"""

from __future__ import annotations

from origo.agents.prompts import DOMAIN_CONTEXT, issues_note
from origo.llm import chat
from origo.rag.search import search
from origo.state import Answer, KnowledgeAnswer, OrigoState, Passage

SYSTEM = (
    DOMAIN_CONTEXT
    + """

Your job: answer one question about the rules from the numbered passages of the knowledge
base you are given, and nothing else, as JSON.
- found: true only if the passages answer the question for the agreements of the user's
  message. If they do not, found is false, answer and cited are empty: never answer from
  memory, and never with a passage of another agreement.
- answer: short, in English, with each figure (months, years, euros) exactly as the passages
  give it, and the article if the passage names it.
- cited: the numbers of the passages the answer comes from."""
)


def _task(question: str, passages: list[Passage], agreements: list[str]) -> str:
    scope = ", ".join(agreements) if agreements else "none named (a general question)"
    blocks = [f"[{n}] ({p.source})\n{p.text}" for n, p in enumerate(passages, start=1)]
    return (
        f"Agreements of the user's message: {scope}\n\nQuestion: {question}\n\nPassages:\n\n"
        + "\n\n".join(blocks)
    )


def knowledge(state: OrigoState) -> dict:
    """LangGraph node: writes state['knowledge'], one answer per question about the rules."""
    plan = state["plan"]
    if not plan.agreements and plan.out_of_scope:
        return {
            "knowledge": [
                KnowledgeAnswer(question=q, status="not_covered") for q in plan.knowledge_questions
            ]
        }
    note = issues_note(state, "knowledge")
    answers = []
    for question in plan.knowledge_questions:
        passages = search(question, plan.agreements)
        message = chat(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _task(question, passages, plan.agreements) + note},
            ],
            agent="knowledge",
            run=state.get("run", "default"),
            schema=Answer,
        )
        answer = Answer.model_validate_json(message.content)
        if not answer.found:
            answers.append(
                KnowledgeAnswer(question=question, status="not_found", passages=passages)
            )
            continue
        answers.append(
            KnowledgeAnswer(
                question=question,
                status="answered",
                answer=answer.answer,
                cited=answer.cited,
                passages=passages,
            )
        )
    return {"knowledge": answers}