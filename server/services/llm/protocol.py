"""Shared, durable types for native LLM providers.

The dataclasses in this module deliberately contain only JSON-safe data.  SDK
objects are useful while normalising a response, but must never leak into the
agent/Temporal message history.  ``MessageWireV2`` is represented as a plain
dictionary so it can be recorded by Temporal without a custom payload codec.

All providers implement :class:`LLMProvider` (structural typing via Protocol).
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    TypedDict,
    runtime_checkable,
)


MESSAGE_WIRE_VERSION = 2
"""Current durable message representation version."""

SUPPORTED_MESSAGE_WIRE_VERSIONS = frozenset({1, 2})
"""Every durable version this worker can decode; never derive from current."""

NATIVE_MESSAGE_WIRE_VERSIONS = frozenset({2})
"""Versioned native-agent formats accepted by the Temporal activity."""

MAX_PROVIDER_STATE_DEPTH = 20
BINARY_STATE_MARKER = "__opencompany_bytes_base64__"
"""Marker used to persist SDK byte strings in ordinary JSON state."""


def encode_binary_state(value: Any) -> Any:
    """Encode bytes for durable provider state; preserve other values exactly."""

    if isinstance(value, memoryview):
        value = value.tobytes()
    elif isinstance(value, bytearray):
        value = bytes(value)
    if isinstance(value, bytes):
        return {
            BINARY_STATE_MARKER: base64.b64encode(value).decode("ascii")
        }
    return value


def decode_binary_state(value: Any) -> Any:
    """Decode an exact binary marker while leaving ordinary strings untouched."""

    if not (
        isinstance(value, Mapping)
        and set(value) == {BINARY_STATE_MARKER}
    ):
        return value
    encoded = value.get(BINARY_STATE_MARKER)
    if not isinstance(encoded, str):
        raise ValueError("Invalid durable binary-state marker")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid durable binary-state base64") from exc


class MessageWireV2(TypedDict):
    """Versioned JSON object recorded in memory and Temporal histories."""

    version: int
    role: str
    content: str
    blocks: List[Dict[str, Any]]
    tool_calls: List[Dict[str, Any]]
    tool_call_id: Optional[str]
    name: Optional[str]
    provider_state: Dict[str, Any]


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
    # Gemini 3+ thinking_level — None unless the user explicitly set it.
    # Fabricating a default here makes the gemini provider send
    # thinking_level alongside thinking_budget, which Vertex rejects on
    # 2.5-era models (400 INVALID_ARGUMENT).
    level: Optional[str] = None
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
    # Providers occasionally emit invalid or non-object JSON.  Keep the raw
    # value so a caller can return a deterministic tool error and, critically,
    # replay the exact assistant turn back to the same provider.
    raw_arguments: Optional[str] = None
    parse_error: Optional[str] = None

    @classmethod
    def from_raw(cls, *, id: str, name: str, arguments: Any) -> "ToolCall":
        """Build a call without allowing malformed model output to crash.

        Valid JSON objects populate ``args``.  Any other value is preserved in
        ``raw_arguments`` and described by ``parse_error``.
        """

        if isinstance(arguments, Mapping):
            return cls(id=id, name=name, args=dict(arguments))

        raw = arguments if isinstance(arguments, str) else _json_dump(arguments)
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            return cls(
                id=id,
                name=name,
                args={},
                raw_arguments=raw,
                parse_error=f"Invalid JSON tool arguments: {exc}",
            )
        if not isinstance(parsed, dict):
            return cls(
                id=id,
                name=name,
                args={},
                raw_arguments=raw,
                parse_error=(
                    "Tool arguments must decode to a JSON object, "
                    f"not {type(parsed).__name__}"
                ),
            )
        return cls(id=id, name=name, args=parsed, raw_arguments=raw)


@dataclass
class ContentBlock:
    """Provider-neutral, ordered content within a message.

    ``type`` is intentionally an open string rather than an enum: providers
    can add a new block without making old workers unable to decode history.
    Known values are ``text``, ``reasoning``, ``tool_call`` and
    ``tool_result``.
    """

    type: str
    text: str = ""
    tool_call: Optional[ToolCall] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """Normalized chat message.

    The original flat fields remain the compatibility surface. ``blocks``
    preserves provider response ordering and ``provider_state`` contains the
    minimal same-provider continuation metadata (for example an Anthropic
    thinking signature or Gemini thought signature).
    """

    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    blocks: List[ContentBlock] = field(default_factory=list)
    provider_state: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.blocks:
            self.blocks = _default_blocks(self)
        # Fail close at construction time for provider-produced state.  This
        # prevents an SDK response object or unbounded blob entering a durable
        # workflow history.
        self.provider_state = _validated_provider_state(self.provider_state)


@dataclass
class Usage:
    """Token usage metrics."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
            "reasoning_tokens",
        ):
            setattr(self, field_name, _safe_token_count(getattr(self, field_name)))
        if not self.total_tokens:
            self.total_tokens = self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cache_creation_tokens=(
                self.cache_creation_tokens + other.cache_creation_tokens
            ),
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


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
    assistant_message: Optional[Message] = None

    def __post_init__(self) -> None:
        """Keep the flat response API while exposing a replayable message."""

        if self.assistant_message is None:
            blocks: List[ContentBlock] = []
            if self.thinking:
                blocks.append(ContentBlock(type="reasoning", text=self.thinking))
            if self.content:
                blocks.append(ContentBlock(type="text", text=self.content))
            blocks.extend(
                ContentBlock(type="tool_call", tool_call=call)
                for call in self.tool_calls
            )
            self.assistant_message = Message(
                role="assistant",
                content=self.content,
                tool_calls=list(self.tool_calls),
                blocks=blocks,
            )
            return

        # A provider may populate only the canonical message.  Preserve the
        # convenience fields expected by existing chat callers.
        if not self.content:
            self.content = self.assistant_message.content
        if not self.tool_calls:
            self.tool_calls = list(self.assistant_message.tool_calls)
        if self.thinking is None:
            reasoning = [
                block.text
                for block in self.assistant_message.blocks
                if block.type == "reasoning" and block.text
            ]
            self.thinking = "\n\n".join(reasoning) or None


class LLMErrorCategory(str, Enum):
    """Stable categories used by retry and user-error policies."""

    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONTEXT_LENGTH = "context_length"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server"
    UNKNOWN = "unknown"


@dataclass
class LLMError(Exception):
    """Provider-independent structured SDK failure."""

    message: str
    provider: str
    category: LLMErrorCategory = LLMErrorCategory.UNKNOWN
    retryable: bool = False
    status_code: Optional[int] = None
    provider_code: Optional[str] = None
    request_id: Optional[str] = None
    retry_after: Optional[float] = None
    retry_after_raw: Optional[str] = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    @property
    def user_message(self) -> str:
        """Return a category-based message that is safe for public surfaces.

        ``message`` intentionally retains the original SDK text for exception
        chaining and operator diagnostics. Provider messages can include
        request payload fragments, internal endpoint URLs, or credential
        details, so execution boundaries must expose this property instead.
        """

        provider_names = {
            "anthropic": "Anthropic",
            "gemini": "Gemini",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
            "groq": "Groq",
            "cerebras": "Cerebras",
            "xai": "xAI",
            "deepseek": "DeepSeek",
            "kimi": "Kimi",
            "mistral": "Mistral",
            "ollama": "Ollama",
            "lmstudio": "LM Studio",
        }
        provider_key = str(self.provider or "").strip().lower()
        provider = provider_names.get(
            provider_key, "The language model provider"
        )
        provider_object = (
            provider
            if provider_key in provider_names
            else "the language model provider"
        )
        category = (
            self.category.value
            if isinstance(self.category, LLMErrorCategory)
            else str(self.category or LLMErrorCategory.UNKNOWN.value)
        )
        messages = {
            LLMErrorCategory.AUTHENTICATION.value: (
                f"{provider} authentication failed. "
                "Check the configured API key."
            ),
            LLMErrorCategory.PERMISSION.value: (
                f"{provider} denied this request. "
                "Check account and model access."
            ),
            LLMErrorCategory.RATE_LIMIT.value: (
                f"{provider} is rate-limiting requests. "
                "Retry after a short delay."
            ),
            LLMErrorCategory.INVALID_REQUEST.value: (
                f"{provider} rejected the model request configuration."
            ),
            LLMErrorCategory.NOT_FOUND.value: (
                f"The configured {provider_object} model or endpoint "
                "was not found."
            ),
            LLMErrorCategory.CONTEXT_LENGTH.value: (
                f"The request exceeds the {provider_object} model "
                "context window."
            ),
            LLMErrorCategory.TIMEOUT.value: (
                f"The request to {provider_object} timed out."
            ),
            LLMErrorCategory.CONNECTION.value: (
                f"Could not connect to {provider_object}."
            ),
            LLMErrorCategory.SERVER.value: (
                f"{provider} is temporarily unavailable."
            ),
            LLMErrorCategory.UNKNOWN.value: (
                f"{provider} request failed."
            ),
        }
        return messages.get(category, messages[LLMErrorCategory.UNKNOWN.value])

    @classmethod
    def from_exception(cls, provider: str, exc: BaseException) -> "LLMError":
        status = _optional_int(
            getattr(exc, "status_code", None)
            or getattr(getattr(exc, "response", None), "status_code", None)
            or getattr(exc, "code", None)
        )
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            code = str(code)
        body = getattr(exc, "body", None)
        if not code and isinstance(body, Mapping):
            error_body = body.get("error", body)
            if isinstance(error_body, Mapping):
                code = error_body.get("code") or error_body.get("type")

        request_id = (
            getattr(exc, "request_id", None)
            or _header(getattr(exc, "response", None), "x-request-id")
            or _header(getattr(exc, "response", None), "request-id")
        )
        retry_after_value = _header(
            getattr(exc, "response", None), "retry-after"
        )
        retry_after = _optional_float(retry_after_value)
        category = _classify_error(exc, status)
        return cls(
            message=str(exc),
            provider=provider,
            category=category,
            retryable=category
            in {
                LLMErrorCategory.RATE_LIMIT,
                LLMErrorCategory.TIMEOUT,
                LLMErrorCategory.CONNECTION,
                LLMErrorCategory.SERVER,
            },
            status_code=status,
            provider_code=str(code) if code is not None else None,
            request_id=str(request_id) if request_id is not None else None,
            retry_after=retry_after,
            retry_after_raw=(
                str(retry_after_value)
                if retry_after_value is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Durable MessageWireV2 codec
# ---------------------------------------------------------------------------


def message_to_wire(message: Message) -> MessageWireV2:
    """Serialize a message to the versioned, JSON-safe wire contract."""

    return {
        "version": MESSAGE_WIRE_VERSION,
        "role": message.role,
        "content": message.content,
        "blocks": [_block_to_wire(block) for block in message.blocks],
        "tool_calls": [_tool_call_to_wire(call) for call in message.tool_calls],
        "tool_call_id": message.tool_call_id,
        "name": message.name,
        "provider_state": _validated_provider_state(message.provider_state),
    }


def message_from_wire(value: Mapping[str, Any]) -> Message:
    """Decode MessageWireV2 (and the pre-version flat native shape)."""

    version = value.get("version", value.get("wire_version", 1))
    if version not in SUPPORTED_MESSAGE_WIRE_VERSIONS:
        raise ValueError(f"Unsupported message wire version: {version!r}")

    calls = [
        _tool_call_from_wire(call)
        for call in value.get("tool_calls", ())
        if isinstance(call, Mapping)
    ]
    blocks = (
        [
            _block_from_wire(block)
            for block in value.get("blocks", ())
            if isinstance(block, Mapping)
        ]
        if version in NATIVE_MESSAGE_WIRE_VERSIONS
        else []
    )
    return Message(
        role=str(value.get("role") or value.get("type") or "user"),
        content=str(value.get("content") or ""),
        tool_calls=calls,
        tool_call_id=_optional_str(value.get("tool_call_id")),
        name=_optional_str(value.get("name")),
        blocks=blocks,
        provider_state=dict(value.get("provider_state") or {}),
    )


def messages_to_wire(messages: Iterable[Message]) -> List[MessageWireV2]:
    return [message_to_wire(message) for message in messages]


def messages_from_wire(values: Iterable[Mapping[str, Any]]) -> List[Message]:
    return [message_from_wire(value) for value in values]


def _default_blocks(message: Message) -> List[ContentBlock]:
    blocks: List[ContentBlock] = []
    if message.content:
        block_type = "tool_result" if message.role == "tool" else "text"
        blocks.append(
            ContentBlock(
                type=block_type,
                text=message.content,
                tool_call_id=message.tool_call_id,
                name=message.name,
            )
        )
    blocks.extend(
        ContentBlock(type="tool_call", tool_call=call)
        for call in message.tool_calls
    )
    return blocks


def _tool_call_to_wire(call: ToolCall) -> Dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "args": _json_safe(call.args),
        "raw_arguments": call.raw_arguments,
        "parse_error": call.parse_error,
    }


def _tool_call_from_wire(value: Mapping[str, Any]) -> ToolCall:
    args = value.get("args")
    return ToolCall(
        id=str(value.get("id") or ""),
        name=str(value.get("name") or ""),
        args=dict(args) if isinstance(args, Mapping) else {},
        raw_arguments=_optional_str(value.get("raw_arguments")),
        parse_error=_optional_str(value.get("parse_error")),
    )


def _block_to_wire(block: ContentBlock) -> Dict[str, Any]:
    return {
        "type": block.type,
        "text": block.text,
        "tool_call": (
            _tool_call_to_wire(block.tool_call) if block.tool_call else None
        ),
        "tool_call_id": block.tool_call_id,
        "name": block.name,
        "metadata": _json_safe(block.metadata),
    }


def _block_from_wire(value: Mapping[str, Any]) -> ContentBlock:
    tool_call = value.get("tool_call")
    metadata = value.get("metadata")
    return ContentBlock(
        type=str(value.get("type") or "text"),
        text=str(value.get("text") or ""),
        tool_call=(
            _tool_call_from_wire(tool_call)
            if isinstance(tool_call, Mapping)
            else None
        ),
        tool_call_id=_optional_str(value.get("tool_call_id")),
        name=_optional_str(value.get("name")),
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
    )


def _validated_provider_state(value: Any) -> Dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("provider_state must be a JSON object")
    # Continuation blocks can legitimately exceed 256 KiB. Anthropic signed
    # thinking, Gemini thought-signature turns, and OpenAI encrypted Responses
    # output must be replayed byte-for-byte; rejecting by size turned a valid
    # provider response into a local failure. Keep the JSON-only/depth guards,
    # but do not truncate or reject otherwise valid durable state.
    return _json_safe(dict(value))


def _json_safe(value: Any, *, _depth: int = 0) -> Any:
    if _depth > MAX_PROVIDER_STATE_DEPTH:
        raise ValueError(
            f"JSON value exceeds maximum depth {MAX_PROVIDER_STATE_DEPTH}"
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite floats are not valid durable JSON")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, _depth=_depth + 1) for item in value]
    raise TypeError(
        "Durable LLM state must contain only JSON values; "
        f"got {type(value).__name__}"
    )


def _safe_token_count(value: Any) -> int:
    parsed = _optional_int(value)
    return max(0, parsed or 0)


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _json_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _header(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get(name)
    except (AttributeError, TypeError):
        return None


def _classify_error(
    exc: BaseException, status: Optional[int]
) -> LLMErrorCategory:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if status == 401 or "authentication" in name or "api key" in message:
        return LLMErrorCategory.AUTHENTICATION
    if status == 403 or "permission" in name:
        return LLMErrorCategory.PERMISSION
    if status == 429 or "ratelimit" in name or "rate limit" in message:
        return LLMErrorCategory.RATE_LIMIT
    if status == 404 or "notfound" in name:
        return LLMErrorCategory.NOT_FOUND
    if (
        "context_length" in message
        or "context length" in message
        or "too many tokens" in message
    ):
        return LLMErrorCategory.CONTEXT_LENGTH
    if status in {400, 409, 422} or "badrequest" in name:
        return LLMErrorCategory.INVALID_REQUEST
    if status == 408 or "timeout" in name:
        return LLMErrorCategory.TIMEOUT
    if "connection" in name:
        return LLMErrorCategory.CONNECTION
    if status is not None and status >= 500:
        return LLMErrorCategory.SERVER
    return LLMErrorCategory.UNKNOWN


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
