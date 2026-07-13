"""Adapters bridging OpenCompany systems to RLM interfaces.

- BackendAdapter: OpenCompany provider config -> RLM backend + backend_kwargs
- ChatModelExtractor: Connected chat model nodes -> RLM other_backends
- ToolBridgeAdapter: Connected tool nodes -> RLM custom_tools dict
"""

import asyncio
import re
from typing import Dict, Any, List, Tuple, Optional

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
        from constants import AI_CHAT_MODEL_TYPES

        backends, kwargs_list = [], []

        if not tool_data:
            return backends, kwargs_list

        for tool_info in tool_data:
            node_type = tool_info.get("node_type", "")
            if node_type not in AI_CHAT_MODEL_TYPES:
                continue

            params = tool_info.get("parameters", {})
            provider = params.get("provider", "")
            model = params.get("model", "")
            api_key = params.get("api_key")

            if not api_key and auth:
                api_key = await auth.get_api_key(provider)
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
        tool_data: Optional[List[Dict[str, Any]]], context: Optional[Dict] = None, loop: Optional[asyncio.AbstractEventLoop] = None
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

            def _make_sync_wrapper(t_type, t_id, t_params, t_label):
                def wrapper(**kwargs):
                    config = {
                        "node_type": t_type,
                        "node_id": t_id,
                        "parameters": t_params,
                        "label": t_label,
                    }
                    if context:
                        config["nodes"] = context.get("nodes", [])
                        config["edges"] = context.get("edges", [])
                    future = asyncio.run_coroutine_threadsafe(
                        execute_tool(t_label, kwargs, config),
                        main_loop,
                    )
                    return future.result(timeout=60)

                return wrapper

            # Clean tool name (same pattern as _build_tool_from_node ai.py:2567)
            tool_name = re.sub(r"[^a-zA-Z0-9_]", "_", label.lower().replace(" ", "_"))
            description = ToolBridgeAdapter.TOOL_DESCRIPTIONS.get(node_type, f"Execute {label} ({node_type})")

            tools[tool_name] = {
                "tool": _make_sync_wrapper(node_type, node_id, params, label),
                "description": description,
            }
            logger.info(f"[RLM] Bridged tool: {tool_name} ({node_type})")

        return tools
