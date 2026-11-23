"""The record of one tool call (copied from ARCA's api/schemas.py)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: Any = None
    ok: bool = True
    error: str | None = None
    latency_ms: float = 0.0
