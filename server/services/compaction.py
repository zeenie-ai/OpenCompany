"""Memory compaction service using native provider APIs.

Anthropic: tool_runner with compaction_control parameter
OpenAI: context_management with compact_threshold
Others: Client-side summarization fallback

Threshold strategy: per-session custom_threshold > model-aware threshold > global default.
Model-aware threshold = 50% of model's context window (e.g., 100K for a 200K model).
"""

from dataclasses import asdict
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Dict, Any, Optional, TYPE_CHECKING

from core.logging import get_logger
from services.pricing import get_pricing_service

if TYPE_CHECKING:
    from core.database import Database
    from core.config import Settings

logger = get_logger(__name__)

# Compaction ratio is sourced from server/config/llm_defaults.json
# (``agent.compaction.ratio``). Per-user override in ``user_settings`` still
# wins via :meth:`CompactionService._get_compaction_ratio`.


def _extract_text_from_response(content) -> str:
    """Extract text content from various LLM response formats.

    Handles:
    - String content (OpenAI, Anthropic)
    - List of content blocks (Gemini format: [{"type": "text", "text": "..."}])
    - Other complex formats
    """
    # Handle list content (Gemini format)
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
                elif "text" in block:
                    text_parts.append(str(block["text"]))
            elif isinstance(block, str):
                text_parts.append(block)
        return "\n".join(text_parts)

    # Handle string content
    if isinstance(content, str):
        return content

    # Fallback
    return str(content) if content else ""


class CompactionConfig(BaseModel):
    """Provider-agnostic compaction configuration."""

    enabled: bool = True


class CompactionService:
    """Minimal compaction service using native provider APIs."""

    def __init__(self, database: "Database", settings: "Settings"):
        self._db = database
        self._config = CompactionConfig(enabled=settings.compaction_enabled)
        self._ai_service = None

    def set_ai_service(self, ai_service) -> None:
        """Wire AI service for generating compaction summaries."""
        self._ai_service = ai_service

    async def _get_compaction_ratio(self) -> float:
        """Per-user override from ``user_settings.compaction_ratio``, falling
        back to ``agent.compaction.ratio`` in ``llm_defaults.json``."""
        from services.model_registry import get_model_registry

        try:
            settings = await self._db.get_user_settings("default")
            if settings and "compaction_ratio" in settings:
                ratio = float(settings["compaction_ratio"])
                if 0.1 <= ratio <= 0.9:
                    return ratio
        except Exception:
            pass
        return float(get_model_registry().get_agent_defaults()["compaction"]["ratio"])

    def get_model_threshold(self, model: str, provider: str, ratio: Optional[float] = None) -> int:
        """Compute compaction threshold based on model's context window.

        ``ratio`` defaults to ``agent.compaction.ratio`` from
        ``llm_defaults.json``; pass an explicit value to override per call.
        Per-session ``custom_threshold`` (checked in :meth:`track`) still
        wins over the value returned here.
        """
        from services.model_registry import get_model_registry

        registry = get_model_registry()
        if ratio is None:
            ratio = float(registry.get_agent_defaults()["compaction"]["ratio"])
        context_length = registry.get_context_length(model, provider)
        model_threshold = int(context_length * ratio)
        # Never go below the configured minimum (ge=10000 in Settings)
        return max(model_threshold, 10000)

    async def anthropic_config(self, threshold: Optional[int] = None, model: str = "", provider: str = "anthropic") -> Dict[str, Any]:
        """Anthropic SDK compaction_control for tool_runner."""
        if not threshold:
            ratio = await self._get_compaction_ratio()
            threshold = self.get_model_threshold(model, provider, ratio=ratio)
        return {"enabled": self._config.enabled, "context_token_threshold": threshold}

    async def anthropic_api_config(self, threshold: Optional[int] = None, model: str = "", provider: str = "anthropic") -> Dict[str, Any]:
        """Anthropic Messages API context_management config."""
        if not threshold:
            ratio = await self._get_compaction_ratio()
            threshold = self.get_model_threshold(model, provider, ratio=ratio)
        return {
            "betas": ["compact-2026-01-12"],
            "context_management": {"edits": [{"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": threshold}}]},
        }

    async def openai_config(self, threshold: Optional[int] = None, model: str = "", provider: str = "openai") -> Dict[str, Any]:
        """OpenAI context_management config."""
        if not threshold:
            ratio = await self._get_compaction_ratio()
            threshold = self.get_model_threshold(model, provider, ratio=ratio)
        return {"context_management": {"compact_threshold": threshold}}

    async def track(self, session_id: str, node_id: str, provider: str, model: str, usage: Dict[str, int]) -> Dict[str, Any]:
        """Track token usage and cost, return compaction status."""
        cost = await self._record_usage_metric(
            session_id=session_id,
            node_id=node_id,
            provider=provider,
            model=model,
            usage=usage,
        )

        state = await self._db.get_or_create_session_token_state(session_id)
        new_total = state["cumulative_total"] + usage.get("total_tokens", 0)
        new_total_cost = state.get("cumulative_total_cost", 0.0) + cost["total_cost"]

        # Update cumulative state with cost
        await self._db.update_session_token_state(
            session_id,
            {
                "cumulative_input_tokens": state["cumulative_input_tokens"] + usage.get("input_tokens", 0),
                "cumulative_output_tokens": state["cumulative_output_tokens"] + usage.get("output_tokens", 0),
                "cumulative_total": new_total,
                "cumulative_input_cost": state.get("cumulative_input_cost", 0.0) + cost["input_cost"],
                "cumulative_output_cost": state.get("cumulative_output_cost", 0.0) + cost["output_cost"],
                "cumulative_total_cost": new_total_cost,
            },
        )

        # Priority: per-session custom > model-aware (with user ratio) > global default
        custom = state.get("custom_threshold")
        if custom:
            threshold = custom
        else:
            ratio = await self._get_compaction_ratio()
            threshold = self.get_model_threshold(model, provider, ratio=ratio)

        # Get the model's full context window size for frontend display
        from services.model_registry import get_model_registry

        registry = get_model_registry()
        context_length = registry.get_context_length(model, provider)

        return {
            "total": new_total,
            "total_cost": new_total_cost,
            "cost": cost,
            "threshold": threshold,
            "context_length": context_length,
            "needs_compaction": self._config.enabled and new_total >= threshold,
        }

    async def _record_usage_metric(
        self,
        *,
        session_id: str,
        node_id: str,
        provider: str,
        model: str,
        usage: Dict[str, int],
        iteration: int = 1,
    ) -> Dict[str, float]:
        """Persist billed usage without changing active-context counters."""

        pricing_service = get_pricing_service()
        cost = pricing_service.calculate_cost(
            provider=provider,
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
        )

        # Save token metric with cost fields
        await self._db.save_token_metric(
            {
                "session_id": session_id,
                "node_id": node_id,
                "provider": provider,
                "model": model,
                "iteration": iteration,
                **usage,
                "input_cost": cost["input_cost"],
                "output_cost": cost["output_cost"],
                "cache_cost": cost["cache_cost"],
                "total_cost": cost["total_cost"],
            }
        )
        return cost

    async def record(
        self, session_id: str, node_id: str, provider: str, model: str, tokens_before: int, tokens_after: int, summary: Optional[str] = None
    ) -> None:
        """Record compaction event after native API handles it."""
        state = await self._db.get_or_create_session_token_state(session_id)
        await self._db.save_compaction_event(
            {
                "session_id": session_id,
                "node_id": node_id,
                "trigger_reason": "native",
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "summary_model": model,
                "summary_provider": provider,
                "success": True,
                "summary_content": summary,
            }
        )
        await self._db.update_session_token_state(
            session_id,
            {
                "cumulative_total": tokens_after,
                "last_compaction_at": datetime.now(timezone.utc),
                "compaction_count": state["compaction_count"] + 1,
            },
        )

    async def stats(self, session_id: str, model: str = "", provider: str = "") -> Dict[str, Any]:
        """Get session statistics.

        When model/provider are provided, the threshold reflects the model's
        context window.  Otherwise falls back to the global default.
        """
        state = await self._db.get_or_create_session_token_state(session_id)
        custom = state.get("custom_threshold")
        if custom:
            threshold = custom
        else:
            ratio = await self._get_compaction_ratio()
            threshold = self.get_model_threshold(model, provider, ratio=ratio)
        # Get context window size for frontend display
        context_length = 0
        if model and provider:
            from services.model_registry import get_model_registry

            registry = get_model_registry()
            context_length = registry.get_context_length(model, provider)

        return {
            "session_id": session_id,
            "total": state["cumulative_total"],
            "threshold": threshold,
            "context_length": context_length,
            "count": state.get("compaction_count", 0),
        }

    async def configure(self, session_id: str, threshold: Optional[int] = None, enabled: Optional[bool] = None) -> bool:
        """Configure session settings."""
        updates = {}
        if threshold is not None:
            updates["custom_threshold"] = threshold
        if enabled is not None:
            updates["compaction_enabled"] = enabled
        return await self._db.update_session_token_state(session_id, updates) if updates else True

    async def compact_context(
        self,
        session_id: str,
        node_id: str,
        memory_content: str,
        provider: str,
        api_key: str,
        model: str,
        *,
        explicit_max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Perform compaction by summarizing conversation history.

        Uses the AI service to generate a structured summary following Claude Code pattern.
        """
        if not self._ai_service:
            logger.warning("[Compaction] AI service not wired")
            return {"success": False, "error": "AI service not available"}

        if not memory_content or len(memory_content.strip()) < 100:
            return {"success": False, "error": "Memory content too short"}

        state = await self._db.get_or_create_session_token_state(session_id)
        tokens_before = state["cumulative_total"]
        compaction_usage: Dict[str, int] = {}

        try:
            prompt = f"""Summarize this conversation into a structured format:

## Task Overview
What the user is trying to accomplish.

## Current State
What's been completed and what's in progress.

## Important Discoveries
Key findings, decisions, or problems encountered.

## Next Steps
What needs to happen next.

## Context to Preserve
Details that must be retained for continuity.

---
CONVERSATION:
{memory_content}
---

Provide a concise but complete summary."""

            # Use a reasonable summary size: min(4096, model's max output)
            from services.model_registry import get_model_registry

            model_max = get_model_registry().get_max_output_tokens(model, provider)
            summary_tokens = min(4096, model_max)

            if self._ai_service.chat_unifier is None:
                raise RuntimeError("ChatUnifier is required for compaction")

            from services.agent_runtime import run_native_llm_step
            from services.llm.protocol import Message

            response = await run_native_llm_step(
                self._ai_service.chat_unifier,
                provider=provider,
                api_key=api_key,
                messages=[Message(role="user", content=prompt)],
                model=model,
                temperature=0.3,
                max_tokens=summary_tokens,
                sdk_max_retries=0,
                explicit_max_retries=explicit_max_retries,
            )
            compaction_usage = asdict(response.usage)
            await self._record_usage_metric(
                session_id=session_id,
                node_id=node_id,
                provider=provider,
                model=model,
                usage=compaction_usage,
                # Iteration zero distinguishes the summarizer call in the
                # existing metric schema without adding a public field.
                iteration=0,
            )
            summary = _extract_text_from_response(response.content)

            new_memory = f"# Conversation Summary (Compacted)\n*Generated: {datetime.now(timezone.utc).isoformat()}*\n\n{summary}"

            await self.record(session_id, node_id, provider, model, tokens_before, 0, new_memory)
            logger.info(f"[Compaction] Session {session_id}: {tokens_before} -> 0 tokens")

            return {
                "success": True,
                "summary": new_memory,
                "tokens_before": tokens_before,
                "tokens_after": 0,
                "usage": compaction_usage,
            }

        except Exception as e:
            logger.error(f"[Compaction] Failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "usage": compaction_usage,
            }


_service: Optional[CompactionService] = None


def get_compaction_service() -> Optional[CompactionService]:
    return _service


def init_compaction_service(database: "Database", settings: "Settings") -> CompactionService:
    global _service
    _service = CompactionService(database, settings)
    logger.info("[Compaction] Initialized")
    return _service
