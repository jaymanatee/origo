"""The one door to the LLM: Qwen3.8-27B-FP8 of the DGX central service (vLLM, OpenAI API).

Every call of every agent goes through ``chat``, so the cost of the system is measured in
one place: prompt, completion and reasoning tokens and latency, per agent and per run. The
report compares architectures by these numbers as much as by accuracy.

Reasoning is chosen per call (``thinking``): in a first test the model spent its 300 tokens
thinking and returned no answer, and without thinking it answered fast and confidently
wrong. Which agents think is a design decision, and an ablation.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

MODEL = os.environ.get("ORIGO_LLM_MODEL", "qwen3.8-27b-fp8")


@dataclass
class Call:
    run: str  # which run of the system (one user question)
    agent: str  # which agent made the call
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    seconds: float


class Meter:
    """Every call, kept per run. A lock, because agents of one run may call in parallel."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._lock = threading.Lock()

    def record(self, call: Call) -> None:
        with self._lock:
            self.calls.append(call)

    def summary(self, run: str) -> dict:
        """Totals of one run, overall and per agent."""
        per_agent: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for c in (c for c in self.calls if c.run == run):
            totals = per_agent[c.agent]
            totals["calls"] += 1
            totals["prompt_tokens"] += c.prompt_tokens
            totals["completion_tokens"] += c.completion_tokens
            totals["reasoning_tokens"] += c.reasoning_tokens
            totals["seconds"] += c.seconds
        overall = defaultdict(float)
        for totals in per_agent.values():
            for key, value in totals.items():
                overall[key] += value
        return {"total": dict(overall), "per_agent": {a: dict(t) for a, t in per_agent.items()}}


METER = Meter()
_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ["ORIGO_LLM_BASE_URL"],
            api_key=os.environ["ORIGO_LLM_API_KEY"],
            timeout=float(os.environ.get("ORIGO_LLM_TIMEOUT", "300")),
        )
    return _client


def _all_required(schema: dict) -> dict:
    """The JSON schema with every property required, nested models too: with structured
    output the model may leave out any property that is not required, and it did (agreements
    and out_of_scope of a Plan). Optional fields still accept null or []."""
    for node in [schema, *schema.get("$defs", {}).values()]:
        if "properties" in node:
            node["required"] = list(node["properties"])
    return schema


def chat(
    messages: list[dict],
    *,
    agent: str,
    run: str = "default",
    tools: list[dict] | None = None,
    thinking: bool = False,
    max_tokens: int | None = None,
    schema: type[BaseModel] | None = None,
    temperature: float | None = None,
):
    """One call to the model; returns the assistant message (``.content``, ``.tool_calls``,
    and ``.reasoning`` when thinking). Sampling as Qwen recommends for each mode, with a
    fixed seed so that runs can be repeated."""
    kwargs = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens or (8192 if thinking else 2048),
        "temperature": temperature if temperature is not None else (0.6 if thinking else 0.7),
        "top_p": 0.95 if thinking else 0.8,
        "seed": 0,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": thinking}},
    }
    if tools:
        kwargs["tools"] = tools
    if schema is not None:  # structured output: vLLM only lets the model write valid JSON
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": _all_required(schema.model_json_schema()),
            },
        }
        # vLLM only constrains the output with the schema; it does not show it to the model.
        # Without it, the model never reads the descriptions (it wrote DESCONOCIDO for
        # France: it did not know what UE meant).
        hint = (
            "\n\nAnswer only with a JSON object that follows this JSON schema (read the "
            "descriptions of the fields):\n"
            + json.dumps(kwargs["response_format"]["json_schema"]["schema"], ensure_ascii=False)
        )
        messages = [*messages]
        if messages and messages[0]["role"] == "system":
            messages[0] = {**messages[0], "content": messages[0]["content"] + hint}
        else:
            messages.insert(0, {"role": "system", "content": hint.strip()})
        kwargs["messages"] = messages
    started = time.perf_counter()
    response = client().chat.completions.create(**kwargs)
    usage = response.usage
    details = getattr(usage, "completion_tokens_details", None)
    METER.record(
        Call(
            run=run,
            agent=agent,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=(getattr(details, "reasoning_tokens", 0) or 0) if details else 0,
            seconds=time.perf_counter() - started,
        )
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":  # cut at max_tokens: content is partial or empty
        raise RuntimeError(f"{agent}: the answer was cut at max_tokens={kwargs['max_tokens']}")
    return choice.message
