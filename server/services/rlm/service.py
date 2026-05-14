"""RLM execution service.

Follows the same interface as AIService.execute_agent() / execute_chat_agent()
but replaces the agent loop with RLM's REPL-based execution.
"""

import asyncio
import time
from typing import Dict, Any, List, Optional

from core.logging import get_logger
from .adapters import BackendAdapter, ChatModelExtractor, ToolBridgeAdapter
from .constants import DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_DEPTH, DEFAULT_VERBOSE

logger = get_logger(__name__)


class RLMService:
    """Dedicated service for RLM agent execution.

    Injected into AIService via dependency injection,
    following the same pattern as MapsService, TextService, etc.
    """

    def __init__(self, auth=None):
        self.auth = auth

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        memory_data: Optional[Dict[str, Any]] = None,
        skill_data: Optional[List[Dict[str, Any]]] = None,
        tool_data: Optional[List[Dict[str, Any]]] = None,
        broadcaster=None,
        workflow_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        database=None,
    ) -> Dict[str, Any]:
        """Execute RLM agent completion.

        Same interface as AIService.execute_agent() / execute_chat_agent().
        Reuses existing MachinaOS utilities for shared concerns.
        """
        start_time = time.time()

        async def broadcast_status(phase: str, details: Dict[str, Any] = None):
            if broadcaster:
                await broadcaster.update_node_status(node_id, "executing", {
                    "phase": phase, "agent_type": "rlm", **(details or {})
                }, workflow_id=workflow_id)

        try:
            # === Parameter extraction (same pattern as execute_chat_agent) ===
            prompt = parameters.get('prompt', '')
            system_message = parameters.get('system_message', '')

            # Skill injection (reuse ai.py:833 _build_skill_system_prompt)
            # Collected as supplementary context -- appended to RLM prompt, never replaces it
            from services.ai import _build_skill_system_prompt
            supplementary_context = ""
            skill_prompt, has_personality = _build_skill_system_prompt(
                skill_data, log_prefix="[RLM]"
            )
            if skill_prompt:
                supplementary_context += f"\n\n## Skill Instructions\n{skill_prompt}"

            # Options flattening (same as execute_chat_agent line 1995)
            options = parameters.get('options', {})
            flattened = {**parameters, **options}

            # Provider/model/API key resolution (same as lines 1998-2014)
            from services.ai import is_model_valid_for_provider, get_default_model_async
            api_key = flattened.get('api_key')
            provider = parameters.get('provider', 'openai')
            model = parameters.get('model', '')

            if not model or not is_model_valid_for_provider(model, provider):
                model = await get_default_model_async(provider, database)

            if not api_key and self.auth:
                api_key = await self.auth.get_api_key(provider)
            if not api_key:
                raise ValueError(f"API key required for RLM Agent (provider: {provider})")

            await broadcast_status("initializing", {
                "message": f"Initializing RLM with {provider}/{model}",
                "provider": provider, "model": model,
            })

            # === Memory injection (appended to supplementary context, not system prompt) ===
            if memory_data and memory_data.get('memory_content'):
                memory_text = memory_data['memory_content']
                supplementary_context += f"\n\n## Conversation History\n{memory_text}"

            # === Adapter: Map provider to RLM backend ===
            backend, backend_kwargs = BackendAdapter.adapt(provider, model, api_key)

            # === Adapter: Extract chat model nodes as other_backends ===
            other_backends, other_backend_kwargs = await ChatModelExtractor.extract(
                tool_data, self.auth
            )

            # === Adapter: Bridge tool nodes as RLM custom_tools ===
            await broadcast_status("building_tools", {
                "message": "Bridging connected tools..."
            })
            running_loop = asyncio.get_running_loop()
            custom_tools = ToolBridgeAdapter.bridge(tool_data, context, loop=running_loop)

            # === RLM-specific parameters ===
            max_iterations = int(flattened.get('max_iterations', DEFAULT_MAX_ITERATIONS))
            max_depth = int(flattened.get('max_depth', DEFAULT_MAX_DEPTH))
            max_budget = flattened.get('max_budget')
            max_timeout = flattened.get('max_timeout')
            max_tokens = flattened.get('max_tokens')
            verbose = flattened.get('verbose', DEFAULT_VERBOSE)

            # === Run RLM in thread pool (RLM is synchronous) ===
            await broadcast_status("executing", {
                "message": f"RLM executing (max {max_iterations} iterations)...",
                "max_iterations": max_iterations, "max_depth": max_depth,
                "tool_count": len(custom_tools),
            })

            # Build augmented prompt: MachinaOS context prepended to user prompt.
            # We do NOT pass custom_system_prompt to RLM because RLM_SYSTEM_PROMPT
            # uses Python .format() with {custom_tools_section} placeholder and
            # double-escaped braces {{chunk}} in examples. Appending to the template
            # and letting build_rlm_system_prompt() .format() it would break on any
            # curly braces in the appended text (skills, memory, system message).
            # Instead, let RLM use its default system prompt (with proper tool
            # formatting) and inject MachinaOS context into the user prompt.
            augmented_prompt = prompt
            if system_message:
                augmented_prompt = f"## Instructions\n{system_message}\n\n## Task\n{augmented_prompt}"
            if supplementary_context:
                augmented_prompt = f"{supplementary_context}\n\n{augmented_prompt}"

            def _run_rlm():
                from rlm import RLM
                from rlm.logger import RLMLogger
                rlm_instance = RLM(
                    backend=backend, backend_kwargs=backend_kwargs,
                    environment="local",
                    other_backends=other_backends if other_backends else None,
                    other_backend_kwargs=other_backend_kwargs if other_backend_kwargs else None,
                    max_iterations=max_iterations, max_depth=max_depth,
                    max_budget=float(max_budget) if max_budget else None,
                    max_timeout=float(max_timeout) if max_timeout else None,
                    max_tokens=int(max_tokens) if max_tokens else None,
                    custom_tools=custom_tools if custom_tools else None,
                    logger=RLMLogger(), verbose=verbose,
                )
                return rlm_instance.completion(augmented_prompt)

            result = await asyncio.to_thread(_run_rlm)

            # === Memory save (same pattern as execute_chat_agent) ===
            if memory_data and memory_data.get('node_id'):
                from services.memory_store import add_message
                session_id = memory_data.get('session_id', 'default')
                add_message(session_id, "user", prompt)
                add_message(session_id, "assistant", result.response or "")

            execution_time = time.time() - start_time
            iterations = len(result.metadata.get("iterations", [])) if result.metadata else 0

            await broadcast_status("completed", {
                "message": f"RLM completed in {execution_time:.1f}s",
                "iterations": iterations,
            })

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "rlm_agent",
                "result": {
                    "response": result.response,
                    "model": model,
                    "provider": provider,
                    "total_cost": result.usage_summary.total_cost,
                    "total_input_tokens": result.usage_summary.total_input_tokens,
                    "total_output_tokens": result.usage_summary.total_output_tokens,
                    "execution_time": execution_time,
                    "iterations": iterations,
                },
                "execution_time": execution_time,
            }

        except Exception as e:
            logger.error(f"[RLM] Execution failed: {e}", exc_info=True)
            execution_time = time.time() - start_time
            await broadcast_status("error", {"message": str(e)})
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "rlm_agent",
                "error": str(e),
                "execution_time": execution_time,
            }
