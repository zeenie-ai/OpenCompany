"""WorkflowEvent — CloudEvents v1.0 envelope (in-house, no external dep).

Field set mirrors https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md
verbatim so future interop with EventBridge / Knative is a JSON-schema swap.
The MachinaOs extensions (workflow_id, trigger_node_id, correlation_id)
ride as CloudEvents extension attributes — fully compliant with the spec.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class WorkflowEvent(BaseModel):
    """Unified event envelope used by every EventSource."""

    specversion: str = "1.0"
    id: str = Field(default_factory=lambda: uuid4().hex)
    source: str
    type: str
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    subject: Optional[str] = None
    datacontenttype: str = "application/json"
    dataschema: Optional[str] = None
    data: Any = None

    workflow_id: Optional[str] = None
    trigger_node_id: Optional[str] = None
    correlation_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_legacy(cls, event_type: str, payload: dict) -> "WorkflowEvent":
        """Wrap a legacy Dict payload from the pre-framework dispatch path.

        Used by the back-compat shim in event_waiter.dispatch so existing
        plugins (telegram, whatsapp, gmail, etc.) keep working until they
        migrate to native WorkflowEvent emission.
        """
        return cls(
            source=f"legacy://{event_type}",
            type=event_type,
            data=payload,
        )

    # ---- Typed factory classmethods (Wave 11.I, milestone Q) -----------
    #
    # Mirrors the official `cloudevents` Python SDK's `from_http` /
    # `to_http` convenience pattern. Each factory enforces the same
    # source/type/subject conventions across the codebase so callers
    # don't hand-construct envelopes with drifting URI shapes.

    @classmethod
    def credential(
        cls,
        provider: str,
        action: Literal[
            "api_key.saved",
            "api_key.deleted",
            "api_key.validated",
            "oauth.connected",
            "oauth.disconnected",
            "oauth.validated",
        ],
        **extra: Any,
    ) -> "WorkflowEvent":
        """Credential mutation event (matches the existing
        ``broadcast_credential_event`` contract locked by
        ``test_credential_broadcasts.py``)."""
        return cls(
            source="machinaos://services/credentials",
            type=f"credential.{action}",
            subject=provider,
            data={"provider": provider, **extra} if extra else {"provider": provider},
        )

    @classmethod
    def connection_status(
        cls,
        plugin: str,
        *,
        connected: bool,
        subject: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "WorkflowEvent":
        """Plugin connection-state event (whatsapp / telegram / android-relay
        / twitter / google connect-disconnect)."""
        return cls(
            source=f"machinaos://nodes/{plugin}",
            type=f"{plugin}.connection.{'opened' if connected else 'closed'}",
            subject=subject,
            data=dict(data) if data else {},
        )

    @classmethod
    def oauth_completed(
        cls,
        provider: str,
        *,
        identifier: str,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "WorkflowEvent":
        """OAuth callback completion. ``identifier`` is the user-facing
        handle (email / username) used as ``subject``."""
        return cls(
            source=f"machinaos://nodes/{provider}",
            type=f"{provider}.oauth.completed",
            subject=identifier,
            data=dict(data) if data else {"identifier": identifier},
        )

    @classmethod
    def message(
        cls,
        plugin: str,
        direction: Literal["sent", "received"],
        data: Mapping[str, Any],
    ) -> "WorkflowEvent":
        """Plugin-emitted message event (whatsapp / telegram / email
        send + receive). ``subject`` defaults to the chat / sender id
        in the payload (cast to str — Telegram uses numeric chat ids),
        falling back to None."""
        payload = dict(data)
        raw_subject = (
            payload.get("chat_id") or payload.get("from_id") or payload.get("sender")
        )
        return cls(
            source=f"machinaos://nodes/{plugin}",
            type=f"{plugin}.message.{direction}",
            subject=str(raw_subject) if raw_subject is not None else None,
            data=payload,
        )

    @classmethod
    def team_event(
        cls,
        team_id: str,
        kind: str,
        data: Mapping[str, Any],
    ) -> "WorkflowEvent":
        """Agent-team lifecycle / task event (created / dissolved /
        task.added / task.claimed / task.completed / task.failed /
        message.sent)."""
        return cls(
            source="machinaos://services/agent_team",
            type=f"team.{kind}",
            subject=team_id,
            data=dict(data),
        )

    @classmethod
    def workflow_lifecycle(
        cls,
        stage: Literal[
            "deployment.started",
            "deployment.stopped",
            "lock.acquired",
            "lock.released",
            "execution.started",
            "execution.stopped",
        ],
        *,
        workflow_id: str,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "WorkflowEvent":
        """Workflow lifecycle event. Carries ``workflow_id`` both as
        ``subject`` (CloudEvents convention) and as the
        ``workflow_id`` extension attribute (existing reader
        contract)."""
        return cls(
            source="machinaos://services/workflow",
            type=f"workflow.{stage}",
            subject=workflow_id,
            workflow_id=workflow_id,
            data=dict(data) if data else {},
        )

    @classmethod
    def agent_progress(
        cls,
        node_id: str,
        *,
        workflow_id: Optional[str],
        iteration: int,
        max_iterations: int,
        phase: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "WorkflowEvent":
        """Live LangGraph supervised-loop progress for one agent node.

        Emitted from inside the ``astream`` loop in
        ``services/ai.py:execute_agent`` and ``execute_chat_agent`` after
        each super-step. ``iteration`` advances on every ``agent_node``
        invocation; ``max_iterations`` mirrors the LangGraph
        ``recursion_limit`` (sourced from
        ``llm_defaults.json:agent.recursion_limit``).

        ``subject`` carries the executing node id so the FE routes the
        update straight to ``nodeStatusStore`` for that node. The
        ``workflow_id`` extension attribute scopes per-workflow displays
        the same way ``node_status`` broadcasts do.
        """
        return cls(
            source="machinaos://services/agent",
            type="agent.progress",
            subject=node_id,
            workflow_id=workflow_id,
            data={
                "node_id": node_id,
                "iteration": iteration,
                "max_iterations": max_iterations,
                **({"phase": phase} if phase else {}),
                **(dict(data) if data else {}),
            },
        )

    @classmethod
    def task_completed(
        cls,
        task_id: str,
        *,
        status: Literal["completed", "error"],
        agent: str,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "WorkflowEvent":
        """Delegated child-agent completion. Type discriminates on
        succeeded vs failed; ``taskTrigger`` filters by both."""
        return cls(
            source="machinaos://services/agent",
            type=f"agent.task.{'succeeded' if status == 'completed' else 'failed'}",
            subject=task_id,
            data=dict(data) if data else {"task_id": task_id, "agent": agent, "status": status},
        )

    def matches_type(self, pattern: str) -> bool:
        """Glob-style match on event type. ``"all"``/empty matches any.

        Examples:
            "stripe.charge.succeeded" matches itself
            "stripe.charge.*"        matches "stripe.charge.succeeded"
            "stripe.*"               matches "stripe.charge.succeeded"
            "all" or ""              matches everything
        """
        if not pattern or pattern == "all":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return self.type.startswith(prefix + ".") or self.type == prefix
        return self.type == pattern
