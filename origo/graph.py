"""The graph: which node runs after which, decided by code (LangGraph).

    orchestrator ─┬─ knowledge question, or data missing ─────────────────────────┐
                  └─ extractor ─┬─ no materials, or the price not converted ──────┤
                                └─ rules → origin → verifier ─┬─ all checks pass ─┤
                                                              ├─ out of attempts ─┤
                                                              └─ issues: back to  │
                                                                 the first agent  │
                                                                 that must redo   ▼
                                                                                writer
    writer ─┬─ a statement was asked and code finds it ready → confirm ─┬─ yes → issue → END
            └─ otherwise → END                                          └─ no ─────────→ END

The model never chooses the next step: it cannot issue before evaluating, nor skip the checks,
nor issue without the user's yes. ``build(verify=False)`` leaves the verifier out
(origin → writer), for the comparison "with and without verifier"."""

from __future__ import annotations

import json
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from origo.agents.extractor import extractor
from origo.agents.orchestrator import orchestrator
from origo.agents.origin import origin
from origo.agents.rules import rules
from origo.agents.statement import confirm, issue, statement_gaps
from origo.agents.verifier import verifier
from origo.agents.writer import writer
from origo.state import OrigoState

MAX_ATTEMPTS = 2  # verifier runs: the first check and one retry
ORDER = ("orchestrator", "extractor", "rules", "origin")  # redoing one redoes the ones after it
# The Pydantic models of the state, allowed in the checkpoints that the interrupt needs.
STATE_MODELS = [("origo.state", name) for name in ("Plan", "Material", "StatementRequest", "Issue")]


def after_plan(state: OrigoState) -> str:
    plan = state["plan"]
    # A price in another currency is not missing yet: the extractor converts it.
    return "writer" if plan.kind == "knowledge" or plan.gaps(converting=True) else "extractor"


def after_extraction(state: OrigoState) -> str:
    # The price is still missing here if it was in another currency and was not converted.
    return "rules" if state.get("materials") and not state["plan"].gaps() else "writer"


def after_verification(state: OrigoState) -> str:
    issues = state.get("issues", [])
    if not issues or state.get("attempts", 0) >= MAX_ATTEMPTS:
        return "writer"
    return min((issue.agent for issue in issues), key=ORDER.index)


def after_writer(state: OrigoState) -> str:
    asked = state["plan"].statement is not None
    return "confirm" if asked and not statement_gaps(state) else END


def after_confirm(state: OrigoState) -> str:
    return "issue" if state["statement"]["confirmed"] else END


def build(verify: bool = True, checkpointer=None):
    """The compiled graph. A statement needs a checkpointer (the interrupt saves the state
    while it waits for the user); ``ask`` gives one."""
    graph = StateGraph(OrigoState)
    nodes = {
        "orchestrator": orchestrator,
        "extractor": extractor,
        "rules": rules,
        "origin": origin,
        "writer": writer,
        "confirm": confirm,
        "issue": issue,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges("orchestrator", after_plan, ["extractor", "writer"])
    graph.add_conditional_edges("extractor", after_extraction, ["rules", "writer"])
    graph.add_edge("rules", "origin")
    if verify:
        graph.add_node("verifier", verifier)
        graph.add_edge("origin", "verifier")
        graph.add_conditional_edges("verifier", after_verification, [*ORDER, "writer"])
    else:
        graph.add_edge("origin", "writer")
    graph.add_conditional_edges("writer", after_writer, ["confirm", END])
    graph.add_conditional_edges("confirm", after_confirm, ["issue", END])
    graph.add_edge("issue", END)
    return graph.compile(checkpointer=checkpointer)


def ask_in_terminal(data: dict) -> bool:
    """The default confirmation: show the statement's data and wait for a yes."""
    print("\nStatement on origin, ready to issue:")
    print(json.dumps(data, ensure_ascii=False, indent=1))
    return input("Issue it? [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")


def ask(
    question: str,
    bom_file: str | None = None,
    verify: bool = True,
    confirm=ask_in_terminal,
    run: str | None = None,
):
    """One run of the system: the final state (the answer is state['report'], the issued
    statement state['statement']). ``confirm(data) -> bool`` answers the interrupt. ``run``
    names the run in the cost meter (llm.METER); the benchmark gives one so that it can read
    the cost even of a run that crashed."""
    run = run or uuid.uuid4().hex[:8]
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=STATE_MODELS))
    graph = build(verify, checkpointer=saver)
    config = {"configurable": {"thread_id": run}}
    state = graph.invoke({"run": run, "question": question, "bom_file": bom_file}, config)
    while "__interrupt__" in state:  # stopped at confirm: the user decides
        state = graph.invoke(Command(resume=confirm(state["__interrupt__"][0].value)), config)
    return state