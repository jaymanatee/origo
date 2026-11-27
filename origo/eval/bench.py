"""ARCA's benchmark (phase 4) run on Origo: the same tasks and the same automatic checks, so
that the single agent and the multi-agent system are measured alike.

The tasks (``data/bench/``) and the checks (``matches``) are copied from ARCA unchanged. What
changes is how Origo's answer is read, because Origo is a graph and not one agent:

* The answer checked is the writer's report and, if a statement was issued, the line the user
  is shown with its file. ARCA's agent wrote the file name in its own answer; Origo issues
  after the writer, so the report cannot name it.
* ``forbidden_tools`` is read from the final state: ``issue_origin_statement`` ran if a
  statement was issued, ``evaluate_origin`` if some agreement got a verdict.
* Markdown emphasis is taken out before checking: the writer may write '**no son**
  originarias', and the checks look for 'no son originarias'.
* The statement is confirmed automatically: the tasks that ask for one say "if it is
  originating, issue it".

Each task runs ``--repeats`` times (between runs of ARCA the success rate moved by about ten
points), and every row is saved as soon as it ends::

    uv run python -m origo.eval.bench --repeats 3
    uv run python -m origo.eval.bench --repeats 3 --no-verify
    uv run python -m origo.eval.bench --rescore     # checks again, without the model
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path

from origo.llm import METER

CHECK_TYPES = {"contains", "regex", "number", "not", "all", "any"}


# ------------------------------------------------------------------ checks (copied from ARCA)


def _number_readings(token: str) -> set[float]:
    """'48,38' and '48.38' are 48.38; '1.042,28' and '1,042.28' are 1042.28: the answer may
    be in Spanish or in English, so both readings of the separators are tried."""
    readings = set()
    for thousands, decimal in ((",", "."), (".", ",")):
        try:
            readings.add(float(token.replace(thousands, "").replace(decimal, ".")))
        except ValueError:
            pass
    return readings


def matches(check: dict, answer: str) -> bool:
    """One check: a substring, a regex or a number, or ``not`` / ``all`` / ``any`` of checks."""
    kind, value = check.get("type"), check.get("value")
    if kind == "contains":
        return str(value).lower() in answer.lower()
    if kind == "regex":
        return re.search(value, answer) is not None
    if kind == "number":
        tolerance = check.get("tolerance", 0)
        return any(
            abs(reading - float(value)) <= tolerance
            for token in re.findall(r"-?\d[\d.,]*\d|\d", answer)
            for reading in _number_readings(token)
        )
    if kind == "not":
        return not matches(value, answer)
    if kind == "all":
        return all(matches(c, answer) for c in value)
    if kind == "any":
        return any(matches(c, answer) for c in value)
    raise ValueError(f"unknown check type {kind!r}: one of {sorted(CHECK_TYPES)}")


# ------------------------------------------------------------------------ reading Origo's state


def final_answer(state: dict) -> str:
    """What the user sees at the end: the report, and the issued statement if there is one."""
    answer = state.get("report", "")
    statement = state.get("statement") or {}
    if statement.get("path"):
        answer += f"\n\nDeclaración de origen emitida: {statement['path']}"
    return answer


def tools_that_worked(state: dict) -> set[str]:
    """The tools that returned a result in this run, read from what each node left in the
    state (Origo keeps no list of steps)."""
    worked = set()
    if any("error" not in rule for rule in state.get("rules", {}).values()):
        worked.add("get_origin_rule")
    if state.get("conversions"):
        worked.add("get_exchange_rate")
    if any("error" not in e for e in state.get("evaluations", {}).values()):
        worked.add("evaluate_origin")
    if (state.get("statement") or {}).get("path"):
        worked.add("issue_origin_statement")
    return worked


def _plain_text(answer: str) -> str:
    """The answer without Markdown emphasis and headings, as the checks expect it."""
    return re.sub(r"^#+\s*", "", re.sub(r"[*`]", "", answer), flags=re.MULTILINE)


def task_passed(task: dict, row: dict) -> bool:
    """It ran to the end, no forbidden tool worked, and the answer passes the task's check."""
    forbidden = set(task.get("forbidden_tools") or []) & set(row["tools_worked"])
    answer = _plain_text(row["final_answer"])
    return not row["crash"] and not forbidden and matches(task.get("check", {}), answer)


def _trace(state: dict) -> dict:
    """What each node decided, to find out later why a task failed."""
    plan = state.get("plan")
    return {
        "plan": plan.model_dump() if plan else None,
        "materials": [m.model_dump() for m in state.get("materials", [])],
        "materials_missing": state.get("materials_missing", []),
        "conversions": state.get("conversions", []),
        "evaluations": state.get("evaluations", {}),
        "issues": [i.model_dump() for i in state.get("issues", [])],
        "attempts": state.get("attempts", 0),
        "unsupported_numbers": state.get("unsupported_numbers", []),
        "statement": {k: v for k, v in (state.get("statement") or {}).items() if k != "text"},
    }


# ---------------------------------------------------------------------------------- the run


def summarize(rows: list[dict]) -> dict:
    """Success, cost and time over the rows of one label, per repeat and per category."""
    n = max(len(rows), 1)

    def rate(subset: list[dict]) -> float:
        return sum(r["correct"] for r in subset) / max(len(subset), 1)

    repeats = sorted({r["repeat"] for r in rows})
    categories = sorted({r.get("category", "-") for r in rows})
    return {
        "n_rows": len(rows),
        "success_rate": rate(rows),
        "success_by_repeat": [rate([r for r in rows if r["repeat"] == k]) for k in repeats],
        "by_category": {
            c: rate([r for r in rows if r.get("category", "-") == c]) for c in categories
        },
        "crashes": sum(bool(r["crash"]) for r in rows),
        "mean_calls": sum(r["cost"].get("calls", 0) for r in rows) / n,
        "mean_tokens": sum(
            r["cost"].get("prompt_tokens", 0) + r["cost"].get("completion_tokens", 0) for r in rows
        )
        / n,
        "mean_seconds": sum(r["seconds"] for r in rows) / n,
    }


def load_tasks(bench: str | Path, ids: list[str] | None = None) -> list[dict]:
    lines = Path(bench).read_text(encoding="utf-8").splitlines()
    tasks = [json.loads(line) for line in lines if line.strip()]
    if ids:
        unknown = set(ids) - {t["id"] for t in tasks}
        if unknown:
            raise ValueError(f"unknown task ids: {sorted(unknown)}")
        tasks = [t for t in tasks if t["id"] in ids]
    return tasks


def _save(out: Path, results: dict) -> None:
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))


def run_bench(
    ask, tasks: list[dict], label: str, out: Path, repeats: int, verify: bool, resume: bool
) -> dict:
    """Every task ``repeats`` times through ``ask`` (``origo.graph.ask``), saved under
    ``label`` in ``out`` after each one. With ``resume``, the rows already saved are kept."""
    out.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    rows = results.get(label, {}).get("rows", []) if resume else []
    done = {(r["id"], r["repeat"]) for r in rows}
    for repeat in range(repeats):
        for task in tasks:
            if (task["id"], repeat) in done:
                continue
            # A statement is never written over: each run gets an empty folder of its own.
            statements = Path("outputs/bench") / label / str(repeat) / task["id"]
            shutil.rmtree(statements, ignore_errors=True)
            os.environ["ARCA_STATEMENTS_DIR"] = str(statements)
            run = f"{label}-{task['id']}-{repeat}"
            started = time.perf_counter()
            try:
                state = ask(task["task"], verify=verify, confirm=lambda data: True, run=run)
                crash = None
            except Exception as error:  # noqa: BLE001 - one broken task must not end the run
                state, crash = {}, f"{type(error).__name__}: {error}"
            row = {
                "id": task["id"],
                "category": task.get("category", "-"),
                "repeat": repeat,
                "final_answer": final_answer(state),
                "tools_worked": sorted(tools_that_worked(state)),
                "crash": crash,
                "seconds": round(time.perf_counter() - started, 1),
                "cost": METER.summary(run)["total"],
                "cost_per_agent": METER.summary(run)["per_agent"],
                "trace": _trace(state),
            }
            row["correct"] = task_passed(task, row)
            rows.append(row)
            print(
                f"{label} #{repeat} {task['id']:<24} {'OK ' if row['correct'] else 'MAL'} "
                f"{row['seconds']:>6.1f} s" + (f"  {crash}" if crash else ""),
                flush=True,
            )
            results[label] = {**summarize(rows), "rows": rows}
            _save(out, results)  # after every task: a session that ends loses one task, not all
    summary = summarize(rows)
    print(
        f"{label}: éxito {summary['success_rate']:.2f} "
        f"(por repetición: {', '.join(f'{s:.2f}' for s in summary['success_by_repeat'])}) · "
        f"{summary['mean_calls']:.1f} llamadas · {summary['mean_seconds']:.0f} s por tarea",
        flush=True,
    )
    return results


def rescore(out: Path, tasks: list[dict]) -> dict:
    """The saved answers checked again with the current checks (after fixing a check, or
    the way the answer is read): no model, no network."""
    by_id = {t["id"]: t for t in tasks}
    results = json.loads(out.read_text(encoding="utf-8"))
    for label, result in results.items():
        rows = result["rows"]
        for row in rows:
            if row["id"] in by_id:
                row["correct"] = task_passed(by_id[row["id"]], row)
        results[label] = {**summarize(rows), "rows": rows}
        print(f"{label}: éxito {results[label]['success_rate']:.2f} ({len(rows)} filas)")
    _save(out, results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--bench", default="data/bench/tasks.jsonl")
    parser.add_argument("--ids", nargs="+", help="only these task ids (a quick check)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--no-verify", action="store_true", help="the graph without verifier")
    parser.add_argument(
        "--label", help="name of the run in --out (default: origo[-sin-verificador])"
    )
    parser.add_argument("--out", default="reports/bench_origo.json")
    parser.add_argument("--resume", action="store_true", help="keep the rows already in --out")
    parser.add_argument("--rescore", action="store_true", help="check --out again, no model")
    args = parser.parse_args()
    try:
        tasks = load_tasks(args.bench, args.ids)
    except ValueError as error:
        parser.error(str(error))
    if args.rescore:
        rescore(Path(args.out), tasks)
        return

    from origo.graph import ask

    label = args.label or ("origo-sin-verificador" if args.no_verify else "origo")
    run_bench(ask, tasks, label, Path(args.out), args.repeats, not args.no_verify, args.resume)


if __name__ == "__main__":
    main()