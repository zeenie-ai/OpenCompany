"""Shared types and provider protocol for native LLM providers.

All providers implement LLMProvider (structural typing via Protocol).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------


@dataclass
class ThinkingConfig:
    """Unified thinking/reasoning configuration.

    - Anthropic: budget_tokens (int)
    - OpenAI o-series / GPT-5: reasoning_effort (low/medium/high)
    - Gemini 2.5: thinking_budget (int tokens)
    - Gemini 3+: thinking_level (low/medium/high)
    - Groq Qwen3: reasoning_format (parsed/hidden)
    """

    enabled: bool = False
    budget: int = 2048
    effort: str = "medium"
    level: str = "medium"  # Gemini 3+ thinking_level
    format: str = "parsed"


@dataclass
class ToolDef:
    """Tool definition passed to the LLM."""

    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema


@dataclass
class ToolCall:
    """A tool invocation returned by the LLM."""

    id: str
    name: str
    args: Dict[str, Any]


@dataclass
class Message:
    """Normalized chat message."""

    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Usage:
    """Token usage metrics."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""

    content: str = ""
    thinking: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    finish_reason: str = "stop"
    raw: Any = None


# ---------------------------------------------------------------------------
# Provider protocol (structural typing)
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    provider_name: str

    async def chat(
        self,
        messages: List[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: Optional[ThinkingConfig] = None,
        tools: Optional[List[ToolDef]] = None,
    ) -> LLMResponse: ...

    async def fetch_models(self, api_key: str) -> List[str]: ...
