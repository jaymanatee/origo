"""The benchmark runner, with a fake ``ask``: the real tasks and checks of ARCA, no model, no
network."""

import json
import os
from pathlib import Path

import pytest

from origo.eval.bench import (
    _plain_text,
    load_tasks,
    rescore,
    run_bench,
    task_passed,
    tools_that_worked,
)

BENCH = Path(__file__).resolve().parents[1] / "data" / "bench" / "tasks.jsonl"
TASKS = {t["id"]: t for t in load_tasks(BENCH)}
ORIGINATING = {"TCA": {"originating": True, "evaluation_id": "EV-1"}}


def fake_ask(states: dict, seen: list):
    """An ``ask`` that answers each task with a fixed final state, and records each run."""

    def ask(question, verify, confirm, run):
        assert confirm({}) is True  # the benchmark confirms the statement by itself
        seen.append({"run": run, "verify": verify, "dir": os.environ["ARCA_STATEMENTS_DIR"]})
        state = states[question]
        if isinstance(state, Exception):
            raise state
        return state

    return ask


def run(tmp_path, monkeypatch, states_by_id, repeats=1, resume=False, seen=None):
    monkeypatch.chdir(tmp_path)  # outputs/bench/ goes to the test's folder
    tasks = [TASKS[i] for i in states_by_id]
    states = {TASKS[i]["task"]: s for i, s in states_by_id.items()}
    seen = [] if seen is None else seen
    out = tmp_path / "bench.json"
    results = run_bench(fake_ask(states, seen), tasks, "origo", out, repeats, True, resume)
    return results["origo"], seen, out


def test_the_issued_statement_is_part_of_the_answer(tmp_path, monkeypatch):
    issued = {
        "report": "Las lavadoras son originarias según el TCA (cambio de partida).",
        "evaluations": ORIGINATING,
        "statement": {"confirmed": True, "path": "outputs/bench/LV-2026-0312.txt"},
    }
    result, _, _ = run(tmp_path, monkeypatch, {"declaracion-01": issued})
    assert result["rows"][0]["correct"]
    assert result["rows"][0]["tools_worked"] == ["evaluate_origin", "issue_origin_statement"]


def test_a_forbidden_statement_fails_the_task():
    task = TASKS["declaracion-03"]  # not originating: nothing may be issued
    row = {"final_answer": "Las bicicletas no son originarias.", "crash": None}
    assert task_passed(task, {**row, "tools_worked": ["evaluate_origin"]})
    assert not task_passed(task, {**row, "tools_worked": ["issue_origin_statement"]})


def test_tools_are_read_from_the_state():
    state = {
        "rules": {"TCA": {"rule_sets": []}},
        "evaluations": {"TCA": {"error": "no rule of origin: missing"}},
    }
    assert tools_that_worked(state) == {"get_origin_rule"}  # a failed evaluation is no verdict


def test_markdown_does_not_hide_the_verdict():
    assert _plain_text("## Resultado\nLas bicicletas **no son** originarias.") == (
        "Resultado\nLas bicicletas no son originarias."
    )
    row = {"final_answer": "Las bicicletas **no son** originarias.", "crash": None}
    assert task_passed(TASKS["veredicto-01"], {**row, "tools_worked": ["evaluate_origin"]})


def test_a_crash_is_a_failure_and_the_run_goes_on(tmp_path, monkeypatch):
    answer = {"report": "Las bicicletas no son originarias.", "evaluations": ORIGINATING}
    states = {"cambio-01": RuntimeError("writer: the answer was cut"), "veredicto-01": answer}
    result, _, _ = run(tmp_path, monkeypatch, states)
    crashed, passed = result["rows"]
    assert crashed["crash"] == "RuntimeError: writer: the answer was cut"
    assert not crashed["correct"]
    assert passed["correct"]
    assert result["crashes"] == 1


def test_each_run_gets_its_own_statements_folder(tmp_path, monkeypatch):
    answer = {"report": "No son originarias."}
    states = {"veredicto-01": answer, "declaracion-03": answer}
    result, seen, _ = run(tmp_path, monkeypatch, states, repeats=2)
    assert len({s["dir"] for s in seen}) == 4
    assert len({s["run"] for s in seen}) == 4
    assert result["success_by_repeat"] == [1.0, 1.0]


def test_resume_runs_only_what_is_missing(tmp_path, monkeypatch):
    states = {"veredicto-01": {"report": "No son originarias."}}
    seen = []
    run(tmp_path, monkeypatch, states, repeats=1, seen=seen)
    result, seen, _ = run(tmp_path, monkeypatch, states, repeats=2, resume=True, seen=seen)
    assert [s["run"] for s in seen] == ["origo-veredicto-01-0", "origo-veredicto-01-1"]
    assert result["n_rows"] == 2


def test_rescore_uses_the_current_checks(tmp_path, monkeypatch):
    states = {"veredicto-01": {"report": "No son originarias."}}
    _, _, out = run(tmp_path, monkeypatch, states)
    stricter = {**TASKS["veredicto-01"], "check": {"type": "contains", "value": "MAXNOM"}}
    assert rescore(out, [stricter])["origo"]["success_rate"] == 0.0
    assert json.loads(out.read_text())["origo"]["rows"][0]["correct"] is False


def test_unknown_ids_are_refused():
    with pytest.raises(ValueError, match="no-such-task"):
        load_tasks(BENCH, ["no-such-task"])