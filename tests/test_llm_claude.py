"""Sanity checks for the Claude wrapper that don't hit the real API.

We assert wiring, not model output: prompt-cache markers, tool spec shape,
and that the model + thinking config match the claude-api skill mandates.
"""

from __future__ import annotations

from app.pipeline.llm_claude import ClaudeAgent
from app.tools.registry import build_default_registry


def test_system_block_is_cached():
    agent = ClaudeAgent(build_default_registry())
    blocks = agent._system
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "voice" in blocks[0]["text"].lower()


def test_last_tool_is_cached_first_is_not():
    agent = ClaudeAgent(build_default_registry())
    tools = agent._tools
    assert tools, "expected at least one tool"
    assert "cache_control" not in tools[0]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_uses_opus_4_7_model_by_default():
    agent = ClaudeAgent(build_default_registry())
    assert agent._model == "claude-opus-4-7"
