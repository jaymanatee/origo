"""The loop every agent with tools runs: call the model, execute its tool_calls, give it the
results, until it answers without calling anything.

Native tool calls (vLLM): names and arguments come apart from the text, so none of ARCA's
parsing and recovery is needed. Each agent gets only its own tools, and never one that needs
confirmation: issuing a statement is done by the graph after the human confirms."""

from __future__ import annotations

import json

from origo.engine.registry import ToolRegistry
from origo.engine.schemas import ToolCallRecord
from origo.llm import chat


def use_tools(
    messages: list[dict],
    registry: ToolRegistry,
    *,
    agent: str,
    run: str = "default",
    thinking: bool = False,
    max_rounds: int = 5,
):
    """Run the tool loop. Returns the model's last message (no tool_calls) and every call made.
    ``messages`` is extended in place with the assistant and tool messages."""
    if any(t.requires_confirmation for t in registry.tools.values()):
        raise ValueError("tools that need confirmation are run by the graph, not by an agent")
    records: list[ToolCallRecord] = []
    for _ in range(max_rounds):
        message = chat(
            messages, agent=agent, run=run, tools=registry.definitions(), thinking=thinking
        )
        if not message.tool_calls:
            return message, records
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [call.model_dump() for call in message.tool_calls],
            }
        )
        for call in message.tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as error:
                record = ToolCallRecord(
                    name=call.function.name, arguments={}, ok=False,
                    error=f"arguments are not valid JSON: {error}",
                )
            else:
                record = registry.call(call.function.name, arguments)
            records.append(record)
            payload = {"result": record.result} if record.ok else {"error": record.error}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            )
    raise RuntimeError(f"{agent}: still calling tools after {max_rounds} rounds")
