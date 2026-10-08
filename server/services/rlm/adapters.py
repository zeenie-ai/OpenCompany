"""Adapters bridging OpenCompany systems to RLM interfaces.

- BackendAdapter: OpenCompany provider config -> RLM backend + backend_kwargs
- ChatModelExtractor: Connected chat model nodes -> RLM other_backends
- ToolBridgeAdapter: Connected tool nodes -> RLM custom_tools dict
"""

import asyncio
import inspect
import re
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

from core.logging import get_logger
from .constants import PROVIDER_TO_BACKEND, PROVIDER_BASE_URLS

logger = get_logger(__name__)


class BackendAdapter:
    """Maps OpenCompany provider/model/api_key to RLM backend constructor args."""

    @staticmethod
    def adapt(provider: str, model: str, api_key: str) -> Tuple[str, Dict[str, Any]]:
        backend = PROVIDER_TO_BACKEND.get(provider, "openai")
        kwargs = {"model_name": model, "api_key": api_key}
        if provider in PROVIDER_BASE_URLS:
            kwargs["base_url"] = PROVIDER_BASE_URLS[provider]
        return backend, kwargs


class ChatModelExtractor:
    """Extracts connected AI_CHAT_MODEL_TYPES nodes as RLM other_backends.

    Chat model nodes connected to input-tools provide small LMs for
    llm_query()/rlm_query() at depth>=1.
    """

    @staticmethod
    async def extract(tool_data: Optional[List[Dict[str, Any]]], auth) -> Tuple[List[str], List[Dict]]:
        from constants import AI_CHAT_MODEL_TYPES, detect_ai_provider

        backends, kwargs_list = [], []

        if not tool_data:
            return backends, kwargs_list

        for tool_info in tool_data:
            node_type = tool_info.get("node_type", "")
            if node_type not in AI_CHAT_MODEL_TYPES:
                continue

            params = tool_info.get("parameters", {})
            # A chat-model node has no ``provider`` field: its provider comes
            # from its type, or from its ``endpoint`` for a named endpoint.
            provider = detect_ai_provider(node_type, params)
            if provider not in PROVIDER_TO_BACKEND:
                # RLM builds its own clients and never reads a user base URL,
                # so a local server or named endpoint would reach api.openai.com.
                raise ValueError(
                    f"The RLM Agent cannot use the {provider!r} chat model connected to it. "
                    f"Connect one for: {', '.join(sorted(PROVIDER_TO_BACKEND))}."
                )
            model = params.get("model", "")
            api_key = params.get("api_key")

            if not api_key and auth:
                api_key = await auth.resolve_api_key(provider)
            if not api_key:
                logger.warning(f"[RLM] Skipping chat model node {node_type}: no API key")
                continue

            backend, kwargs = BackendAdapter.adapt(provider, model, api_key)
            backends.append(backend)
            kwargs_list.append(kwargs)
            logger.info(f"[RLM] Extracted small LM: {provider}/{model} -> {backend}")
            break  # RLM currently supports one other_backend

        return backends, kwargs_list


class ToolBridgeAdapter:
    """Bridges OpenCompany tool nodes into RLM custom_tools dict.

    Creates sync callable wrappers that route through execute_tool() dispatcher.
    Uses asyncio.run_coroutine_threadsafe() to bridge async handlers into
    RLM's synchronous exec() REPL thread.
    """

    TOOL_TIMEOUT_SECONDS = 60.0
    CANCELLATION_GRACE_SECONDS = 1.0
    CONTEXT_EVENT_TIMEOUT_SECONDS = 10.0

    # Brief descriptions for common tool types (used in RLM REPL context)
    TOOL_DESCRIPTIONS = {
        "calculatorTool": "Math operations: add, subtract, multiply, divide, power, sqrt, mod, abs. Args: operation, a, b",
        "currentTimeTool": "Get current date/time. Args: timezone (optional)",
        "duckduckgoSearch": "Web search via DuckDuckGo. Args: query, max_results (optional)",
        "pythonExecutor": "Execute Python code. Args: code (must set output variable)",
        "httpRequest": "HTTP request. Args: url, method (GET/POST/PUT/DELETE), body (optional)",
        "httpRequestTool": "HTTP request. Args: url, method (GET/POST/PUT/DELETE), body (optional)",
        "braveSearch": "Web search via Brave. Args: query",
        "serperSearch": "Web search via Google/Serper. Args: query",
        "perplexitySearch": "AI-powered web search via Perplexity. Args: query",
        "crawleeScraper": "Read/extract content from web pages. Args: url",
        "gmail": "Send/search/read emails. Args: operation, ...",
        "calendar": "Manage Google Calendar events. Args: operation, ...",
        "drive": "Manage Google Drive files. Args: operation, ...",
        "sheets": "Read/write Google Sheets. Args: operation, ...",
        "tasks": "Manage Google Tasks. Args: operation, ...",
        "contacts": "Manage Google Contacts. Args: operation, ...",
        "taskManager": "Track delegated tasks. Args: operation",
        "timer": "Wait for duration. Args: duration, unit",
    }

    @staticmethod
    def bridge(
        tool_data: Optional[List[Dict[str, Any]]], context: Optional[Dict] = None, loop: Optional[asyncio.AbstractEventLoop] = None,
        broadcaster=None, parent_node_id: Optional[str] = None, workflow_id: Optional[str] = None,
        provider: Optional[str] = None,
        ambiguous_outcome_sink: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None,
    ) -> Dict[str, Dict]:
        from constants import AI_AGENT_TYPES, AI_CHAT_MODEL_TYPES
        from services.handlers.tools import execute_tool

        if not tool_data:
            return {}

        main_loop = loop or asyncio.get_event_loop()
        tools = {}

        for tool_info in tool_data:
            node_type = tool_info.get("node_type", "")

            # Skip agents (delegation) and chat models (handled by ChatModelExtractor)
            if node_type in AI_AGENT_TYPES or node_type in AI_CHAT_MODEL_TYPES:
                continue

            node_id = tool_info.get("node_id", "")
            label = tool_info.get("label", node_type)
            params = tool_info.get("parameters", {})
            tool_name = tool_info.get("_agent_tool_name")
            input_model = tool_info.get("_agent_tool_input_model")
            if not tool_name or input_model is None:
                from services.node_registry import get_node_class

                node_cls = get_node_class(node_type)
                if node_cls is not None:
                    tool_name = tool_name or (
                        getattr(node_cls, "tool_name", "") or node_type
                    )
                    model_factory = getattr(
                        node_cls, "tool_input_model", None
                    )
                    input_model = (
                        model_factory()
                        if callable(model_factory)
                        else getattr(node_cls, "Params", None)
                    )
            if not tool_name:
                tool_name = re.sub(
                    r"[^a-zA-Z0-9_]",
                    "_",
                    label.lower().replace(" ", "_"),
                )
            if tool_name in tools:
                raise ValueError(
                    f"Duplicate canonical tool name {tool_name!r}"
                )

            def _make_sync_wrapper(
                t_type,
                t_id,
                t_params,
                t_label,
                t_name,
                t_input_model,
                t_execution,
                t_ambiguous_outcome_sink,
            ):
                def wrapper(**kwargs):
                    config = {
                        **dict(t_execution or {}),
                        "node_type": t_type,
                        "node_id": t_id,
                        "parameters": t_params,
                        "label": t_label,
                        "workflow_id": workflow_id,
                        "parent_node_id": parent_node_id,
                        "provider": provider,
                    }
                    if context:
                        config["nodes"] = context.get("nodes", [])
                        config["edges"] = context.get("edges", [])
                        config["execution_id"] = context.get("execution_id")
                        config["root_execution_id"] = context.get("root_execution_id")
                        config["user_id"] = context.get("user_id", "owner")

                    if t_input_model is not None:
                        try:
                            validated = t_input_model.model_validate(kwargs)
                            tool_args = validated.model_dump(
                                mode="json",
                                exclude_unset=True,
                            )
                        except Exception as exc:
                            return {
                                "error": "Invalid tool arguments",
                                "details": str(exc),
                            }
                    else:
                        tool_args = dict(kwargs)

                    async def execute_with_parent_status():
                        if broadcaster and parent_node_id:
                            await broadcaster.update_node_status(
                                parent_node_id,
                                "executing",
                                {"phase": "executing_tool", "agent_type": "rlm", "tool_name": t_name},
                                workflow_id=workflow_id,
                            )
                            if t_type != "_builtin_skill":
                                await broadcaster.broadcast_agent_capability(
                                    parent_node_id,
                                    capability_kind="tool",
                                    capability_name=t_name,
                                    state="started",
                                    workflow_id=workflow_id,
                                    execution_id=str((context or {}).get("execution_id") or "") or None,
                                    root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                                    target_node_id=str(t_id or "") or None,
                                    provider=provider,
                                    invocation_source="rlm",
                                )
                        try:
                            result = await execute_tool(
                                t_name,
                                tool_args,
                                config,
                            )
                            if broadcaster and parent_node_id:
                                await broadcaster.update_node_status(
                                    parent_node_id,
                                    "executing",
                                    {"phase": "tool_completed", "agent_type": "rlm", "tool_name": t_name},
                                    workflow_id=workflow_id,
                                )
                                if t_type != "_builtin_skill":
                                    await broadcaster.broadcast_agent_capability(
                                        parent_node_id,
                                        capability_kind="tool",
                                        capability_name=t_name,
                                        state="completed",
                                        workflow_id=workflow_id,
                                        execution_id=str((context or {}).get("execution_id") or "") or None,
                                        root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                                        target_node_id=str(t_id or "") or None,
                                        provider=provider,
                                        invocation_source="rlm",
                                    )
                            return result
                        except Exception as exc:
                            if broadcaster and parent_node_id:
                                await broadcaster.update_node_status(
                                    parent_node_id,
                                    "executing",
                                    {
                                        "phase": "tool_completed",
                                        "agent_type": "rlm",
                                        "tool_name": t_name,
                                        "tool_failed": True,
                                    },
                                    workflow_id=workflow_id,
                                )
                                if t_type != "_builtin_skill":
                                    await broadcaster.broadcast_agent_capability(
                                        parent_node_id,
                                        capability_kind="tool",
                                        capability_name=t_name,
                                        state="failed",
                                        workflow_id=workflow_id,
                                        execution_id=str((context or {}).get("execution_id") or "") or None,
                                        root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                                        target_node_id=str(t_id or "") or None,
                                        provider=provider,
                                        invocation_source="rlm",
                                        error_code=type(exc).__name__,
                                    )
                            raise

                    execution_finished = threading.Event()

                    async def execute_with_completion():
                        try:
                            return await execute_with_parent_status()
                        finally:
                            execution_finished.set()

                    future = asyncio.run_coroutine_threadsafe(
                        execute_with_completion(),
                        main_loop,
                    )
                    try:
                        return future.result(
                            timeout=(
                                ToolBridgeAdapter.TOOL_TIMEOUT_SECONDS
                            )
                        )
                    except FutureTimeoutError as exc:
                        # A tool may itself raise TimeoutError. In that case
                        # the concurrent future is already complete and its
                        # exception is an exact failure, not our wait limit.
                        if future.done():
                            return future.result()
                        cancel_requested = future.cancel()
                        cancellation_observed = execution_finished.wait(
                            ToolBridgeAdapter.CANCELLATION_GRACE_SECONDS
                        )
                        ambiguous = {
                            "outcome": "ambiguous",
                            "reason": "tool_timeout",
                            "tool_name": t_name,
                            "tool_node_id": t_id,
                            "tool_node_type": t_type,
                            "arguments": tool_args,
                            "timeout_seconds": (
                                ToolBridgeAdapter.TOOL_TIMEOUT_SECONDS
                            ),
                            "cancel_requested": cancel_requested,
                            "cancellation_observed": (
                                cancellation_observed
                            ),
                        }
                        if t_ambiguous_outcome_sink is not None:
                            receipt = asyncio.run_coroutine_threadsafe(
                                t_ambiguous_outcome_sink(ambiguous),
                                main_loop,
                            )
                            try:
                                receipt.result(
                                    timeout=(
                                        ToolBridgeAdapter
                                        .CONTEXT_EVENT_TIMEOUT_SECONDS
                                    )
                                )
                            except Exception as persist_exc:
                                raise RuntimeError(
                                    "RLM tool timed out and the "
                                    "ambiguous Context event could not "
                                    "be committed"
                                ) from persist_exc
                        raise TimeoutError(
                            f"RLM tool {t_name!r} timed out after "
                            f"{ToolBridgeAdapter.TOOL_TIMEOUT_SECONDS:g}s; "
                            "outcome is ambiguous"
                        ) from exc

                if t_input_model is not None:
                    from pydantic_core import PydanticUndefined

                    parameters = []
                    for field_name, field_info in (
                        t_input_model.model_fields.items()
                    ):
                        if field_info.is_required():
                            default = inspect.Parameter.empty
                        elif field_info.default_factory is not None:
                            try:
                                default = field_info.default_factory()
                            except Exception:
                                default = None
                        elif field_info.default is PydanticUndefined:
                            default = None
                        else:
                            default = field_info.default
                        parameters.append(
                            inspect.Parameter(
                                field_name,
                                inspect.Parameter.KEYWORD_ONLY,
                                annotation=field_info.annotation,
                                default=default,
                            )
                        )
                    wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
                        parameters
                    )
                wrapper.__name__ = str(t_name)
                return wrapper

            description = (
                tool_info.get("_agent_tool_description")
                or params.get("tool_description")
                or ToolBridgeAdapter.TOOL_DESCRIPTIONS.get(
                    node_type,
                    f"Execute {label} ({node_type})",
                )
            )

            tools[tool_name] = {
                "tool": _make_sync_wrapper(
                    node_type,
                    node_id,
                    params,
                    label,
                    tool_name,
                    input_model,
                    tool_info.get("_agent_tool_execution"),
                    ambiguous_outcome_sink,
                ),
                "description": description,
            }
            logger.info(f"[RLM] Bridged tool: {tool_name} ({node_type})")

        return tools
