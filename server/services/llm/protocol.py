"""Shared, durable types for native LLM providers.

The dataclasses in this module deliberately contain only JSON-safe data.  SDK
objects are useful while normalising a response, but must never leak into the
agent/Temporal message history.  ``MessageWire`` is represented as a plain
dictionary so it can be recorded by Temporal without a custom payload codec.

All providers implement :class:`LLMProvider` (structural typing via Protocol).
"""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    TypedDict,
    runtime_checkable,
)


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


class MessageWire(TypedDict):
    """The one JSON message shape recorded in memory and Temporal histories."""

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


@dataclass(frozen=True)
class StreamEvent:
    """A piece of a response as the provider produces it.

    ``kind`` is ``text`` (answer text) or ``reasoning`` (thinking text).
    Streaming never changes the response: the provider still returns the
    whole :class:`LLMResponse` it would have returned without a sink, and
    the deltas, joined, are that response's text.
    """

    kind: str
    delta: str


#: Receives each :class:`StreamEvent` while a response streams.
StreamSink = Callable[[StreamEvent], Awaitable[None]]


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
    # Media descriptor for ``type == "image"`` blocks, discriminated by
    # ``kind``: durable ``file_ref`` (a FileRef dump) or transient ``bytes``
    # (hydrated request material — the wire codec refuses to serialize it).
    source: Optional[Dict[str, Any]] = None


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
    # Some context-management APIs expose active (post-compaction) usage at
    # the top level and a separate iteration list for total billed work.
    billing_usage: Optional[Usage] = None
    model: str = ""
    finish_reason: str = "stop"
    raw: Any = None
    assistant_message: Optional[Message] = None

    def __post_init__(self) -> None:
        """Keep the flat response API while exposing a replayable message."""

        if self.billing_usage is None:
            self.billing_usage = self.usage
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
    BILLING = "billing"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONTEXT_LENGTH = "context_length"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server"
    # A 2xx whose body is not a completion: the request reached something
    # that does not speak this wire format at that path (RFC-0003 D9).
    PROTOCOL = "protocol"
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
    # Set by the raiser when the category text alone cannot say what the
    # user must change (e.g. which URL answered with a non-completion).
    # Must already be safe for public surfaces: no credential, no query
    # string, no userinfo.
    public_message: Optional[str] = None

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

        if self.public_message:
            return self.public_message

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
            "sarvam": "Sarvam AI",
            "ollama": "Ollama",
            "lmstudio": "LM Studio",
        }
        # Common nouns take an article, so they read "The <noun>" as a
        # subject and "the <noun>" as an object. A name also works as an
        # adjective ("the configured OpenAI model"); a noun with its article
        # does not, so the two model-scoped messages are phrased around it.
        generic_nouns = {"openai_compatible": "OpenAI-compatible endpoint"}
        # A named endpoint's reference is "openai_compatible:<slug>".
        provider_key = str(self.provider or "").strip().lower().partition(":")[0]
        if provider_key in provider_names:
            provider = provider_object = provider_names[provider_key]
            not_found = f"The configured {provider} model or endpoint was not found."
            context_length = f"The request exceeds the {provider} model context window."
        else:
            noun = generic_nouns.get(provider_key, "language model provider")
            provider = f"The {noun}"
            provider_object = f"the {noun}"
            not_found = f"{provider} did not find the configured model."
            context_length = f"The request exceeds the context window of the model at {provider_object}."
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
            LLMErrorCategory.BILLING.value: (
                f"{provider} blocked this request because a spending limit or available credits were exhausted."
            ),
            LLMErrorCategory.QUOTA.value: (
                f"{provider}'s daily quota is exhausted or this model has no available quota."
            ),
            LLMErrorCategory.RATE_LIMIT.value: (
                f"{provider} is rate-limiting requests. "
                "Retry after a short delay."
            ),
            LLMErrorCategory.INVALID_REQUEST.value: (
                f"{provider} rejected the model request configuration."
            ),
            LLMErrorCategory.NOT_FOUND.value: not_found,
            LLMErrorCategory.CONTEXT_LENGTH.value: context_length,
            LLMErrorCategory.TIMEOUT.value: (
                f"The request to {provider_object} timed out."
            ),
            LLMErrorCategory.CONNECTION.value: (
                f"Could not connect to {provider_object}."
            ),
            LLMErrorCategory.SERVER.value: (
                f"{provider} is temporarily unavailable."
            ),
            LLMErrorCategory.PROTOCOL.value: (
                f"{provider} answered without a completion. "
                "Check the configured base URL."
            ),
            LLMErrorCategory.UNKNOWN.value: (
                f"{provider} request failed."
            ),
        }
        return messages.get(category, messages[LLMErrorCategory.UNKNOWN.value])

    @property
    def hint(self) -> Optional[str]:
        """Fixed recovery advice; never copy the provider's raw response."""
        return {
            LLMErrorCategory.BILLING: "Review the provider project's billing, spending cap, and credits. Resume after access is restored; repeating the request will not fix it.",
            LLMErrorCategory.QUOTA: "Check the model's quota for your API project. Resume after the quota resets or is increased, or choose a model with available quota.",
            LLMErrorCategory.AUTHENTICATION: "Update the provider credential in Settings > Connectors, then resume.",
            LLMErrorCategory.PERMISSION: "Check the credential's project and model permissions, then resume after access is restored.",
            LLMErrorCategory.NOT_FOUND: "Choose a model available to this provider and credential, then apply the change and resume.",
            LLMErrorCategory.PROTOCOL: "Correct the provider's base URL and API configuration, then resume.",
            LLMErrorCategory.CONTEXT_LENGTH: "Reduce or clear the agent's context before trying again.",
            LLMErrorCategory.INVALID_REQUEST: "Check the model's supported request settings before trying again.",
        }.get(self.category)

    @property
    def requires_user_action(self) -> bool:
        return not self.retryable and self.category in {
            LLMErrorCategory.BILLING, LLMErrorCategory.QUOTA, LLMErrorCategory.AUTHENTICATION,
            LLMErrorCategory.PERMISSION, LLMErrorCategory.NOT_FOUND,
            LLMErrorCategory.PROTOCOL,
        }

    def as_node_error(self):
        from services.plugin import NodeUserError

        return NodeUserError(
            self.user_message, hint=self.hint,
            requires_user_action=self.requires_user_action,
        )

    @classmethod
    def from_exception(cls, provider: str, exc: BaseException) -> "LLMError":
        body = _error_body(exc)
        status = next((parsed for candidate in (
            getattr(exc, "status_code", None),
            getattr(getattr(exc, "response", None), "status_code", None),
            getattr(exc, "code", None), body.get("code"),
        ) if (parsed := _http_status(candidate)) is not None), None)
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            code = str(code)
        if not code:
            code = body.get("code") or body.get("type") or body.get("status")

        request_id = (
            getattr(exc, "request_id", None)
            or _header(getattr(exc, "response", None), "x-request-id")
            or _header(getattr(exc, "response", None), "request-id")
        )
        response = getattr(exc, "response", None)
        retry_after_value = _header(response, "retry-after")
        retry_after = _retry_delay(retry_after_value)
        if retry_after is None:
            retry_at = _http_date(retry_after_value)
            if retry_at is not None:
                reference = _http_date(_header(response, "date")) or datetime.now(timezone.utc)
                retry_after = _retry_delay(max(0.0, (retry_at - reference).total_seconds()))
        milliseconds = _header(response, "retry-after-ms")
        parsed_milliseconds = _optional_float(milliseconds)
        ms_delay = _retry_delay(parsed_milliseconds / 1000) if parsed_milliseconds is not None else None
        if ms_delay is not None and (retry_after is None or ms_delay > retry_after):
            retry_after, retry_after_value = ms_delay, milliseconds
        # google-genai stores the REST error envelope in ``details``, not
        # ``body``. Google sends pacing as google.rpc.RetryInfo there, often
        # without a Retry-After header. Never retry earlier than either hint.
        for detail in _google_error_details(exc):
            if detail.get("@type") != "type.googleapis.com/google.rpc.RetryInfo":
                continue
            raw_delay = detail.get("retryDelay")
            if not isinstance(raw_delay, str) or not re.fullmatch(r"\d+(?:\.\d{1,9})?s", raw_delay):
                continue
            delay = _retry_delay(raw_delay[:-1])
            if delay is not None and (retry_after is None or delay > retry_after):
                retry_after, retry_after_value = delay, raw_delay
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
# Durable MessageWire codec
# ---------------------------------------------------------------------------


def message_to_wire(message: Message) -> MessageWire:
    """Serialize a message to the JSON-safe wire contract."""

    return {
        "role": message.role,
        "content": message.content,
        "blocks": [_block_to_wire(block) for block in message.blocks],
        "tool_calls": [_tool_call_to_wire(call) for call in message.tool_calls],
        "tool_call_id": message.tool_call_id,
        "name": message.name,
        "provider_state": _validated_provider_state(message.provider_state),
    }


def message_from_wire(value: Mapping[str, Any]) -> Message:
    """Decode the wire shape produced by :func:`message_to_wire`."""

    if not isinstance(value, Mapping) or not (
        value.get("role") or value.get("type")
    ):
        raise ValueError("Invalid message wire object: missing role")

    calls = [
        _tool_call_from_wire(call)
        for call in value.get("tool_calls", ())
        if isinstance(call, Mapping)
    ]
    blocks = [
        _block_from_wire(block)
        for block in value.get("blocks", ())
        if isinstance(block, Mapping)
    ]
    return Message(
        role=str(value.get("role") or value.get("type") or "user"),
        content=str(value.get("content") or ""),
        tool_calls=calls,
        tool_call_id=_optional_str(value.get("tool_call_id")),
        name=_optional_str(value.get("name")),
        blocks=blocks,
        provider_state=dict(value.get("provider_state") or {}),
    )


def messages_to_wire(messages: Iterable[Message]) -> List[MessageWire]:
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


def _durable_source(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Only ref-shaped image sources are durable; hydrated bytes are transient."""
    if source is None:
        return None
    if source.get("kind") == "bytes":
        raise ValueError("image bytes must not enter durable state")
    return _json_safe(dict(source))


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
        "source": _durable_source(block.source),
    }


def _block_from_wire(value: Mapping[str, Any]) -> ContentBlock:
    tool_call = value.get("tool_call")
    metadata = value.get("metadata")
    source = value.get("source")
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
        source=dict(source) if isinstance(source, Mapping) else None,
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


def _http_status(value: Any) -> Optional[int]:
    """Accept actual HTTP codes, never an object's incidental int coercion."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        status = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        status = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{3}", value.strip()):
        status = int(value.strip())
    else:
        return None
    return status if 100 <= status <= 599 else None


def _retry_delay(value: Any) -> Optional[float]:
    """Reject pacing values that cannot become a runtime retry duration."""
    delay = _optional_float(value)
    if isinstance(value, bool) or delay is None:
        return None
    return 0.0 if delay == 0 else valid_retry_delay(delay)


def valid_retry_delay(value: Any) -> Optional[float]:
    """A positive delay safe for both Python and protobuf retry durations."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    delay = _optional_float(value)
    # google.protobuf.Duration represents at most ten thousand years.
    if delay is None or not math.isfinite(delay) or not 0 < delay <= 315576000000:
        return None
    try:
        timedelta(seconds=delay)
    except OverflowError:
        return None
    return delay


def _http_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        date = parsedate_to_datetime(value)
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _error_body(exc: BaseException) -> Mapping[str, Any]:
    for attribute in ("body", "details"):
        body = getattr(exc, attribute, None)
        if isinstance(body, Mapping):
            error = body.get("error", body)
            if isinstance(error, Mapping):
                return error
    return {}


def _google_error_details(exc: BaseException) -> List[Mapping[str, Any]]:
    """Read Google's typed details without exposing provider bodies publicly."""
    body = getattr(exc, "details", None)
    if not isinstance(body, Mapping):
        body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return []
    error = body.get("error", body)
    details = error.get("details") if isinstance(error, Mapping) else None
    return [item for item in details if isinstance(item, Mapping)] if isinstance(details, list) else []


def _google_quota_exhausted(exc: BaseException) -> bool:
    for detail in _google_error_details(exc):
        if detail.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
            continue
        violations = detail.get("violations")
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, Mapping):
                continue
            quota_id = re.sub(r"[^a-z]", "", str(violation.get("quotaId") or "").lower())
            quota_value = violation.get("quotaValue")
            if "perday" in quota_id or (not isinstance(quota_value, bool) and _optional_float(quota_value) == 0):
                return True
    # Some Gemini responses omit quotaValue but name the zero limit in the
    # message. A bare RESOURCE_EXHAUSTED or "check quota" stays retryable:
    # Vertex shared-capacity throttling uses exactly that generic response.
    body = _error_body(exc)
    message = str(getattr(exc, "message", "") or body.get("message") or "").lower()
    return (getattr(exc, "status", None) or body.get("status")) == "RESOURCE_EXHAUSTED" and bool(
        re.search(r"quota exceeded for metric:[^\r\n]*\blimit:\s*0(?:[,\s]|$)", message)
    )


def _classify_error(
    exc: BaseException, status: Optional[int]
) -> LLMErrorCategory:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    body = _error_body(exc)
    message += " " + str(body.get("message") or "").lower()
    codes = {str(value).lower() for value in (getattr(exc, "code", None), body.get("code"), body.get("type"), body.get("status")) if value is not None}
    # A quota/credit exhaustion can arrive as 429, but backoff cannot fix it.
    # Match narrow billing signatures before the generic HTTP classifiers.
    if status in {None, 400, 402, 403, 429} and (
        status == 402 or codes & {"insufficient_quota", "billing_hard_limit_reached", "insufficient_credits", "credit_balance_too_low", "prepayment_credits_depleted"}
        or any(marker in message for marker in (
            "spend cap breached", "insufficient_quota", "insufficient credits",
            "credit balance is too low", "billing_hard_limit_reached",
            "prepayment credits are depleted", "prepay credit balance is depleted",
        ))
    ):
        return LLMErrorCategory.BILLING
    if status == 429 and _google_quota_exhausted(exc):
        return LLMErrorCategory.QUOTA
    # HTTP status is authoritative; throttles and outages can mention the
    # API key without indicating that authentication has failed.
    if status == 429:
        return LLMErrorCategory.RATE_LIMIT
    if status is not None and status >= 500:
        return LLMErrorCategory.SERVER
    if status == 401:
        return LLMErrorCategory.AUTHENTICATION
    if status == 403:
        return LLMErrorCategory.PERMISSION
    if status == 408:
        return LLMErrorCategory.TIMEOUT
    if codes & {"invalid_api_key", "api_key_invalid", "authentication_error", "unauthenticated"}:
        return LLMErrorCategory.AUTHENTICATION
    if status == 400 and any(marker in message for marker in ("api key not valid", "invalid api key", "incorrect api key")):
        return LLMErrorCategory.AUTHENTICATION
    if status is None and ("authentication" in name or "api key" in message):
        return LLMErrorCategory.AUTHENTICATION
    if status is None and "permission" in name:
        return LLMErrorCategory.PERMISSION
    if status == 404:
        return LLMErrorCategory.NOT_FOUND
    if (
        "context_length" in message
        or "context length" in message
        or "too many tokens" in message
    ):
        return LLMErrorCategory.CONTEXT_LENGTH
    if status in {400, 409, 422}:
        return LLMErrorCategory.INVALID_REQUEST
    if status is None and ("ratelimit" in name or "rate limit" in message):
        return LLMErrorCategory.RATE_LIMIT
    if "notfound" in name:
        return LLMErrorCategory.NOT_FOUND
    if "badrequest" in name:
        return LLMErrorCategory.INVALID_REQUEST
    if "timeout" in name:
        return LLMErrorCategory.TIMEOUT
    if "connection" in name:
        return LLMErrorCategory.CONNECTION
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
        context_management: Optional[Dict[str, Any]] = None,
        on_event: Optional[StreamSink] = None,
        effort: Optional[str] = None,
    ) -> LLMResponse:
        """One turn. A provider that streams (``streaming`` in
        llm_defaults.json) hands each delta to ``on_event`` as it arrives and
        still returns the same response; the others never receive one.
        ``effort`` (``low`` / ``high``) is the owner's chat choice, sent only
        for a model its provider lists in ``effort_models``."""
        ...

    async def fetch_models(self, api_key: str) -> List[str]: ...
