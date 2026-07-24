"""Provider-neutral agent execution primitives.

This module is the shared boundary between orchestration (the in-process
``AIService`` loop and Temporal activities) and the native provider SDK layer.
It deliberately has no eager LangChain imports.  ``AgentToolSpec.to_langchain``
exists only so Temporal histories recorded before the native cutover can drain;
it is lazy and is removed with the legacy activity branch.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Type

from pydantic import BaseModel, ValidationError

from core.logging import get_logger
from services.llm.messages import filter_empty_messages
from services.llm.protocol import (
    LLMError,
    LLMResponse,
    Message,
    ThinkingConfig,
    ToolCall,
    ToolDef,
    Usage,
)
from services.tool_identity import DuplicateToolNameError

logger = get_logger(__name__)


@dataclass
class AgentToolSpec:
    """LLM-visible definition plus local execution/validation metadata."""

    definition: ToolDef
    args_schema: Optional[Type[BaseModel]] = None
    execution: Dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def parameters(self) -> Dict[str, Any]:
        return self.definition.parameters

    def to_langchain(self):
        """Build the legacy adapter used only by pre-cutover Temporal runs."""

        from langchain_core.tools import StructuredTool

        def _placeholder(**kwargs):
            return kwargs

        return StructuredTool.from_function(
            name=self.name,
            description=self.description,
            func=_placeholder,
            args_schema=self.args_schema,
        )


def _tool_definition(tool: ToolDef | AgentToolSpec) -> ToolDef:
    return tool.definition if isinstance(tool, AgentToolSpec) else tool


def _tool_specs_by_name(
    tools: Sequence[ToolDef | AgentToolSpec],
) -> Dict[str, AgentToolSpec]:
    return {
        tool.name: tool
        for tool in tools
        if isinstance(tool, AgentToolSpec)
    }


def add_usage(total: Usage, current: Optional[Usage]) -> Usage:
    """Accumulate normalized provider usage without mutating either input."""

    if current is None:
        return total
    return Usage(
        input_tokens=total.input_tokens + current.input_tokens,
        output_tokens=total.output_tokens + current.output_tokens,
        total_tokens=total.total_tokens + current.total_tokens,
        cache_creation_tokens=(
            total.cache_creation_tokens + current.cache_creation_tokens
        ),
        cache_read_tokens=total.cache_read_tokens + current.cache_read_tokens,
        reasoning_tokens=total.reasoning_tokens + current.reasoning_tokens,
    )


async def run_native_llm_step(
    chat_unifier,
    *,
    provider: str,
    api_key: str,
    messages: Sequence[Message],
    model: str,
    temperature: float,
    max_tokens: int,
    thinking: Optional[ThinkingConfig] = None,
    tools: Optional[Sequence[ToolDef | AgentToolSpec]] = None,
    sdk_max_retries: int = 0,
    explicit_max_retries: int = 2,
    translate_errors: bool = True,
) -> LLMResponse:
    """Execute one native SDK turn and return its lossless response envelope.

    Agent retries are deliberately outside the provider SDK so only failures
    normalized as retryable :class:`LLMError` values are repeated. Temporal
    passes ``explicit_max_retries=0`` and owns activity-level retry itself.
    """

    if chat_unifier is None:
        raise RuntimeError("ChatUnifier is required for native agent execution")

    definitions = [_tool_definition(tool) for tool in (tools or ())]
    attempts = max(0, int(explicit_max_retries)) + 1
    for attempt in range(attempts):
        try:
            return await chat_unifier.chat(
                provider=provider,
                api_key=api_key,
                messages=filter_empty_messages(messages),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                tools=definitions or None,
                sdk_max_retries=max(0, int(sdk_max_retries)),
                # Preserve structured metadata until this agent-step boundary.
                translate_errors=False,
            )
        except LLMError as error:
            if not error.retryable or attempt + 1 >= attempts:
                if not translate_errors:
                    raise
                from services.plugin import NodeUserError

                raise NodeUserError(error.user_message) from error
            delay = (
                error.retry_after
                if error.retry_after is not None
                else 0.25 * (2**attempt)
            )
            await asyncio.sleep(max(0.0, min(float(delay), 5.0)))

    raise AssertionError("native LLM retry loop exhausted unexpectedly")


def _validated_tool_args(
    call: ToolCall,
    specs: Dict[str, AgentToolSpec],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if call.parse_error:
        return None, call.parse_error

    spec = specs.get(call.name)
    if spec is None or spec.args_schema is None:
        return dict(call.args or {}), None

    try:
        value = spec.args_schema.model_validate(call.args or {})
        return value.model_dump(mode="json"), None
    except ValidationError as exc:
        return None, str(exc)


async def run_native_agent_loop(
    chat_unifier,
    *,
    provider: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    initial_messages: Sequence[Message],
    thinking: Optional[ThinkingConfig] = None,
    tools: Optional[Sequence[AgentToolSpec]] = None,
    tool_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
    max_iterations: int = 500,
    progress_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
    rebind_from_operations: Optional[
        Callable[[List[Dict[str, Any]]], Awaitable[List[AgentToolSpec]]]
    ] = None,
) -> Dict[str, Any]:
    """Run the shared buffered native tool-agent loop.

    The complete assistant message returned by a provider is appended verbatim
    before tool execution.  This is essential for Gemini thought signatures,
    Anthropic signed thinking blocks, and OpenAI reasoning continuation state.
    """

    current_tools: List[AgentToolSpec] = list(tools or ())
    messages: List[Message] = list(initial_messages)
    usage = Usage()
    thinking_parts: List[str] = []
    last_response: Optional[LLMResponse] = None
    iteration = 0

    for iteration in range(1, max_iterations + 1):
        if progress_callback is not None:
            try:
                await progress_callback(iteration)
            except Exception as exc:  # progress is observational
                logger.debug("[Agent loop] progress callback failed: %s", exc)

        response = await run_native_llm_step(
            chat_unifier,
            provider=provider,
            api_key=api_key,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            tools=current_tools,
        )
        last_response = response
        usage = add_usage(usage, response.usage)

        assistant = response.assistant_message
        if assistant is None:
            assistant = Message(
                role="assistant",
                content=response.content,
                tool_calls=list(response.tool_calls),
            )
        messages.append(assistant)

        if response.thinking:
            thinking_parts.append(
                (
                    response.thinking
                    if not thinking_parts
                    else f"--- Iteration {iteration} ---\n{response.thinking}"
                )
            )

        if str(response.finish_reason).upper() == "SAFETY":
            logger.warning("[Agent loop] provider response blocked by safety filters")

        calls = list(response.tool_calls or assistant.tool_calls or ())
        if not calls:
            return {
                "messages": messages,
                "iteration": iteration,
                "thinking_content": "\n\n".join(thinking_parts) or None,
                "truncated": False,
                "usage": usage,
                "response": response,
            }

        if tool_executor is None:
            logger.warning(
                "[Agent loop] model emitted %d tool call(s) without an executor",
                len(calls),
            )
            return {
                "messages": messages,
                "iteration": iteration,
                "thinking_content": "\n\n".join(thinking_parts) or None,
                "truncated": False,
                "usage": usage,
                "response": response,
            }

        specs = _tool_specs_by_name(current_tools)
        iteration_new_tools: List[AgentToolSpec] = []
        for call in calls:
            if call.name not in specs:
                result: Any = {
                    "error": "Unknown tool",
                    "details": (
                        f"Tool {call.name!r} is not connected to this agent."
                    ),
                }
            else:
                args, validation_error = _validated_tool_args(call, specs)
                if validation_error:
                    result = {
                        "error": "Invalid tool arguments",
                        "details": validation_error,
                    }
                    if call.raw_arguments is not None:
                        result["raw_arguments"] = call.raw_arguments
                else:
                    try:
                        result = await tool_executor(call.name, args or {})
                    except Exception as exc:
                        # Tool failures are fed back to the model.
                        logger.error(
                            "[Agent loop] tool %r failed: %s",
                            call.name,
                            exc,
                        )
                        result = {"error": str(exc)}

            if (
                rebind_from_operations is not None
                and isinstance(result, dict)
                and result.get("operations")
            ):
                try:
                    added = await rebind_from_operations(result["operations"])
                    if added:
                        iteration_new_tools.extend(added)
                except DuplicateToolNameError as exc:
                    logger.warning(
                        "[Agent loop] rejected ambiguous tool rebind: %s", exc
                    )
                    result = dict(result)
                    result["rebind_error"] = exc.as_dict()
                except Exception as exc:
                    logger.warning(
                        "[Agent loop] tool rebind failed: %s", exc, exc_info=True
                    )

            messages.append(
                Message(
                    role="tool",
                    content=json.dumps(result, default=str),
                    tool_call_id=call.id,
                    name=call.name,
                )
            )

        if iteration_new_tools:
            current_tools.extend(iteration_new_tools)
            logger.info(
                "[Agent loop] rebound %d tool(s) (total=%d)",
                len(iteration_new_tools),
                len(current_tools),
            )

    terminal = Message(
        role="assistant",
        content=(
            f"[Recursion limit reached: {max_iterations} iterations. "
            "Adjust agent.recursion_limit in llm_defaults.json or simplify the task.]"
        ),
    )
    messages.append(terminal)
    return {
        "messages": messages,
        "iteration": iteration,
        "thinking_content": "\n\n".join(thinking_parts) or None,
        "truncated": True,
        "usage": usage,
        "response": last_response,
    }


__all__ = [
    "AgentToolSpec",
    "add_usage",
    "run_native_llm_step",
    "run_native_agent_loop",
]
