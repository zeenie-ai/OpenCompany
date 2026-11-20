"""Capabilities frozen into a template snapshot when an instance is admitted."""
from __future__ import annotations


def runtime_capabilities(node_type: str) -> dict[str, str]:
    if node_type in {"claude_code_agent", "codex_agent"}:
        return {"followup_delivery": "next_turn", "continuation": "provider_session",
                "stop_behavior": "terminate_process", "completion_observation": "process_result"}
    if node_type == "rlm_agent":
        return {"followup_delivery": "next_turn", "continuation": "conversation_replay",
                "stop_behavior": "effects_fence_and_drain", "completion_observation": "activity_result",
                "repl_state": "ephemeral"}
    return {"followup_delivery": "model_boundary", "continuation": "canonical_transcript",
            "stop_behavior": "cooperative_checkpoint", "completion_observation": "durable_event"}
