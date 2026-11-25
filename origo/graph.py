"""The graph: which node runs after which, decided by code (LangGraph).

    orchestrator ─┬─ knowledge question, or data missing ─────────────────────────┐
                  └─ extractor ─┬─ no materials ──────────────────────────────────┤
                                └─ rules → origin → verifier ─┬─ all checks pass ─┤
                                                              ├─ out of attempts ─┤
                                                              └─ issues: back to  │
                                                                 the first agent  │
                                                                 that must redo   ▼
                                                                               writer → END

The model never chooses the next step: it cannot issue before evaluating, nor skip the checks.
``build(verify=False)`` leaves the verifier out (origin → writer), for the comparison
"with and without verifier"."""

from __future__ import annotations

import uuid

from langgraph.graph import END, START, StateGraph

from origo.agents.extractor import extractor
from origo.agents.orchestrator import orchestrator
from origo.agents.origin import origin
from origo.agents.rules import rules
from origo.agents.verifier import verifier
from origo.agents.writer import writer
from origo.state import OrigoState

MAX_ATTEMPTS = 2  # verifier runs: the first check and one retry
ORDER = ("orchestrator", "extractor", "rules", "origin")  # redoing one redoes the ones after it


def after_plan(state: OrigoState) -> str:
    plan = state["plan"]
    return "writer" if plan.kind == "knowledge" or plan.gaps() else "extractor"


def after_extraction(state: OrigoState) -> str:
    return "rules" if state.get("materials") else "writer"


def after_verification(state: OrigoState) -> str:
    issues = state.get("issues", [])
    if not issues or state.get("attempts", 0) >= MAX_ATTEMPTS:
        return "writer"
    return min((issue.agent for issue in issues), key=ORDER.index)


def build(verify: bool = True):
    graph = StateGraph(OrigoState)
    for node in (orchestrator, extractor, rules, origin, writer):
        graph.add_node(node.__name__, node)
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
    graph.add_edge("writer", END)
    return graph.compile()


def ask(question: str, bom_file: str | None = None, verify: bool = True) -> OrigoState:
    """One run of the system: the final state (the answer is state['report'])."""
    state = {"run": uuid.uuid4().hex[:8], "question": question, "bom_file": bom_file}
    return build(verify).invoke(state)
