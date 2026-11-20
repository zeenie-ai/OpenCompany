"""Plain conversation bridge for specialized agent runtimes.

Canvas Context nodes carry policy and topology only. Specialized providers
(claude_code, codex, rlm) do not run through the native agent loop,
so this module gives them the same continuity contract the loop has: load
the stored conversation for ``(workflow_id, generation, agent_node_id)`` at
run start, render it into the prompt, and save the exchange back per turn.

The bridge is intentionally absent for legacy ``input-memory`` descriptors so
graphs recorded with that edge retain their original Simple Memory behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional


class AgentPoolKey(NamedTuple):
    workflow_id: str
    agent_node_id: str
    generation: int
    instance_id: str
    principal: str
    job_id: str

from services.llm.protocol import (
    Message,
    message_from_wire,
    message_to_wire,
)


def is_context(value: Any) -> bool:
    """Return whether an edge-walker descriptor is a Context reference."""

    return isinstance(value, dict) and value.get("kind") == "context"


def _wire(role: str, content: str) -> dict[str, Any]:
    """Build one validated conversation wire from plain text."""

    return dict(message_to_wire(Message(role=role, content=content)))


@dataclass
class SpecializedAgentContextBridge:
    """One conversation binding for a specialized provider run.

    ``history`` is the stored transcript at resolve time; ``record_turn``
    appends the new exchange and saves the whole conversation back, so a
    later firing (or a different provider) continues from the same plain
    JSON messages the native agent loop uses.
    """

    database: Any
    workflow_id: str
    generation: int
    agent_node_id: str
    provider: str
    history: tuple[dict[str, Any], ...] = ()
    instance_id: str = ""
    run_id: str = ""
    transcript_revision: int = 0
    scope: dict[str, Any] | None = None
    delivery_ids: tuple[str, ...] = ()

    @property
    def pool_key(self) -> tuple:
        """Claude pool identity; a new generation is a new key, so Reset
        fences warm subprocesses without a lifecycle notification."""

        if self.instance_id:
            return AgentPoolKey(self.workflow_id, self.agent_node_id, self.generation, self.instance_id,
                                str((self.scope or {}).get("user_id") or "owner"),
                                str((self.scope or {}).get("job_id") or (self.scope or {}).get("team_id") or ""))
        return (self.workflow_id, self.agent_node_id, self.generation)

    @classmethod
    async def resolve(
        cls,
        database: Any,
        descriptor: dict[str, Any],
        *,
        provider: str,
        agent_node_id: str,
    ) -> "SpecializedAgentContextBridge":
        if not is_context(descriptor):
            raise ValueError("specialized Context bridge requires a context descriptor")
        if descriptor.get("agent_instance_id"):
            from services.agent_instances.service import AgentInstanceService
            instance_id = str(descriptor["agent_instance_id"])
            scope = dict(descriptor.get("agent_instance_scope") or {})
            service = AgentInstanceService(database)
            instance = await service.get_instance(instance_id, scope=scope or None)
            stored = await service.get_transcript(instance_id)
            run_id = str(descriptor.get("agent_run_id") or "")
            if not run_id or stored.get("owning_run_id") != run_id:
                raise ValueError("Instance conversation is not owned by this run")
            return cls(database=database, workflow_id=instance["workflow_id"],
                generation=int(instance["generation"]), agent_node_id=agent_node_id, provider=provider,
                history=tuple(stored["messages"]), instance_id=instance_id, run_id=run_id,
                transcript_revision=stored["revision"], scope=scope,
                delivery_ids=tuple(descriptor.get("agent_delivery_ids") or descriptor.get("delivery_ids") or []))
        workflow_id = str(descriptor.get("workflow_id") or "")
        generation = int(descriptor.get("generation") or 0)
        if not workflow_id:
            raise ValueError("specialized Context bridge requires workflow_id")
        if generation <= 0:
            raise ValueError(
                "specialized Context bridge requires an admitted generation"
            )

        from services.agent_context import load_conversation

        try:
            wires = await load_conversation(
                database,
                workflow_id=workflow_id,
                generation=generation,
                agent_node_id=agent_node_id,
            )
        except Exception as exc:
            # A load failure must be loud: continuing would silently run the
            # agent amnesiac and burn tokens on an incomplete prompt.
            raise ValueError(
                "Conversation load failed for workflow "
                f"{workflow_id} generation {generation} agent "
                f"{agent_node_id}: {exc}"
            ) from exc
        return cls(
            database=database,
            workflow_id=workflow_id,
            generation=generation,
            agent_node_id=agent_node_id,
            provider=provider,
            history=tuple(w for w in wires if isinstance(w, dict)),
        )

    def augment_prompt(self, prompt: str) -> str:
        """Render the stored conversation into a prompt-only adapter."""

        if not self.history:
            return prompt
        lines: list[str] = []
        for wire in self.history:
            message = message_from_wire(wire)
            content = message.content
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            if not content:
                continue
            lines.append(f"{message.role.upper()}: {content}")
        if not lines:
            return prompt
        return (
            "## Prior conversation\n"
            "The following is the stored conversation for this agent. "
            "Treat it as prior conversation state and continue from it.\n"
            + "\n\n".join(lines)
            + "\n\n## Current request\n"
            + prompt
        )

    async def record_turn(
        self,
        prompt: str,
        response: Optional[str],
    ) -> None:
        """Append one exchange and save the conversation (best-effort)."""

        turn: list[dict[str, Any]] = [_wire("user", prompt)]
        if response:
            turn.append(_wire("assistant", response))
        messages = [*self.history, *turn]

        from services.agent_context import save_conversation

        if self.instance_id:
            from services.agent_instances.service import AgentInstanceService
            checkpoint = self.turn_checkpoint(prompt, response)
            result = await AgentInstanceService(self.database).checkpoint(self.instance_id, self.run_id,
                **checkpoint)
            self.transcript_revision = result["revision"]
            self.history = tuple(checkpoint["messages"])
            return
        await save_conversation(
            self.database,
            workflow_id=self.workflow_id,
            generation=self.generation,
            agent_node_id=self.agent_node_id,
            messages=messages,
        )
        self.history = tuple(messages)

    def turn_checkpoint(self, prompt: str, response: Optional[str], *, response_recorded: bool | None = None) -> dict[str, Any]:
        """Return bookkeeping separately from paid execution for Temporal retries."""
        if response_recorded is None:
            response_recorded = response is not None
        turn = [_wire("user", prompt)]
        if response_recorded:
            turn.append(_wire("assistant", response or ""))
        return {"messages": [*self.history, *turn], "expected_revision": self.transcript_revision,
                "delivery_ids": list(self.delivery_ids) if response_recorded else [], "request_id": "opaque-turn"}

    async def _provider_state(self) -> dict[str, Any]:
        if not self.instance_id:
            return {}
        import socket
        from services.agent_instances.service import AgentInstanceService
        state = (await AgentInstanceService(self.database).get_instance(self.instance_id))["continuation"]
        if state.get("provider") and state["provider"] != self.provider:
            raise ValueError("continuation_unavailable: instance provider changed")
        if state.get("storage_owner") and state["storage_owner"] != socket.gethostname():
            raise ValueError("continuation_unavailable: provider session storage belongs to another worker")
        return state

    async def provider_session_id(self) -> str | None:
        state = await self._provider_state()
        if state.get("startup_state") == "opening" and not state.get("provider_session_id"):
            raise ValueError("continuation_unavailable: prior provider startup did not record a session identity")
        return state.get("provider_session_id")

    async def validate_provider_workspace(self, cwd: Path) -> None:
        """Never recreate a missing checkout under an existing conversation."""
        import os
        state = await self._provider_state()
        if not state:
            return
        if not state.get("cwd"):
            raise ValueError("continuation_unavailable: provider workspace identity is missing")
        expected, requested = Path(state["cwd"]).resolve(), Path(cwd).resolve()
        if os.path.normcase(str(expected)) != os.path.normcase(str(requested)):
            raise ValueError("continuation_unavailable: provider workspace changed")
        if not expected.is_dir() or not (expected / ".git").exists():
            raise ValueError("continuation_unavailable: provider workspace is missing")

    async def prepare_provider_start(self, cwd: Path, *, session_id: str | None = None) -> None:
        """Record startup before launching a process, including an unknown ID."""
        import socket
        from services.agent_instances.service import AgentInstanceService
        await self.validate_provider_workspace(cwd)
        state = await self._provider_state()
        prior = state.get("provider_session_id")
        if prior and session_id and prior != session_id:
            raise ValueError("continuation_unavailable: provider session identity changed")
        path = Path(cwd).resolve()
        if not path.is_dir() or not (path / ".git").exists():
            raise ValueError("continuation_unavailable: provider workspace is missing")
        continuation = {"provider": self.provider, "storage_owner": socket.gethostname(), "cwd": str(path),
                        "startup_state": state.get("startup_state") or "opening"}
        if session_id or prior:
            continuation["provider_session_id"] = session_id or prior
        await AgentInstanceService(self.database).set_continuation(self.instance_id, self.run_id,
            continuation=continuation, request_id="provider-start")

    async def record_event(self, event: dict[str, Any]) -> None:
        """Persist native identity before exposing its initialization event."""
        if not self.instance_id:
            return
        session_id = event.get("thread_id") if event.get("type") == "thread.started" else event.get("session_id")
        if not session_id:
            return
        import socket
        from services.agent_instances.service import AgentInstanceService
        service = AgentInstanceService(self.database)
        state = await self._provider_state()
        prior = state.get("provider_session_id")
        if prior and prior != session_id:
            raise ValueError("continuation_unavailable: provider returned a different conversation")
        if not state.get("cwd"):
            raise ValueError("continuation_unavailable: provider startup was not recorded")
        await service.set_continuation(self.instance_id, self.run_id,
            continuation={"provider": self.provider, "provider_session_id": str(session_id), "storage_owner": socket.gethostname(),
                          "cwd": state["cwd"], "startup_state": "ready"},
            request_id=f"provider-session:{session_id}")


__all__ = [
    "SpecializedAgentContextBridge",
    "is_context",
]
