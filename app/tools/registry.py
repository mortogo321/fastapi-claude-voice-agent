"""Tool registry: maps tool names to JSON schemas and async handlers.

Schemas follow Anthropic's tool spec exactly so the registry output can be
sent verbatim as the `tools` parameter on `messages.stream(...)`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.tools.book_slot import book_slot, book_slot_spec
from app.tools.check_availability import check_availability, check_availability_spec
from app.tools.send_confirmation import send_confirmation, send_confirmation_spec

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolEntry:
    spec: dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(self, spec: dict[str, Any], handler: ToolHandler) -> None:
        self._tools[spec["name"]] = ToolEntry(spec=spec, handler=handler)

    def tool_specs(self) -> list[dict[str, Any]]:
        return [entry.spec for entry in self._tools.values()]

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return await entry.handler(args)
        except Exception as exc:
            return {"error": str(exc)}


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(check_availability_spec, check_availability)
    registry.register(book_slot_spec, book_slot)
    registry.register(send_confirmation_spec, send_confirmation)
    return registry
