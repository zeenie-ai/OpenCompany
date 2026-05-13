"""WorkflowEvent — CloudEvents v1.0 envelope (in-house, no external dep).

Field set mirrors https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md
verbatim so future interop with EventBridge / Knative is a JSON-schema swap.
The MachinaOs extensions (``workflow_id``, ``trigger_node_id``,
``correlation_id``) ride as CloudEvents extension attributes. Spec §3.1
SHOULDs lowercase-alphanumeric extension names; we deliberately keep
Python snake_case for codebase consistency — the rest of the project
uses snake_case as a strict naming convention and the readability win
outweighs the spec recommendation. Internal interop only; if we ever
publish events to an external CloudEvents broker, an alias layer can
translate at the producer boundary.

Type strings carry the reverse-DNS prefix ``com.machinaos.`` per
Primer guidance. ``dataschema`` is auto-populated from ``type`` so
every typed factory produces a schema URI the consumer can validate
against.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


_TYPE_PREFIX = "com.machinaos."
_DATASCHEMA_BASE = "machinaos://schemas/events/"


def _dataschema_for(event_type: str) -> str:
    """Compute the ``dataschema`` URI for a given event ``type``.

    Strips the ``com.machinaos.`` prefix when present so the URI segment
    matches the un-prefixed type (e.g. ``credential.api_key.saved``).
    External-producer events (e.g. ``stripe.charge.succeeded`` from a
    Stripe webhook) keep their type verbatim in the URI segment.
    """
    seg = event_type.removeprefix(_TYPE_PREFIX) if event_type else event_type
    return f"{_DATASCHEMA_BASE}{seg}.json"


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

    # CloudEvents extension attributes. Snake-case (Python convention)
    # rather than lowercase-alphanumeric (CloudEvents §3.1 SHOULD) —
    # see module docstring.
    workflow_id: Optional[str] = None
    trigger_node_id: Optional[str] = None
    correlation_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _auto_dataschema(self) -> "WorkflowEvent":
        """Auto-populate ``dataschema`` from ``type`` so every envelope
        carries the URI a consumer can use to fetch its payload schema.
        Explicit ``dataschema`` values are preserved.
        """
        if self.dataschema is None and self.type:
            self.dataschema = _dataschema_for(self.type)
        return self

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
            type=f"{_TYPE_PREFIX}credential.{action}",
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
            type=f"{_TYPE_PREFIX}{plugin}.connection.{'opened' if connected else 'closed'}",
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
            type=f"{_TYPE_PREFIX}{provider}.oauth.completed",
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
            type=f"{_TYPE_PREFIX}{plugin}.message.{direction}",
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
            type=f"{_TYPE_PREFIX}team.{kind}",
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
            type=f"{_TYPE_PREFIX}workflow.{stage}",
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
            type=f"{_TYPE_PREFIX}agent.progress",
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
    def deployment_snapshot(
        cls,
        running_workflow_ids: list[str],
    ) -> "WorkflowEvent":
        """Push-on-connect snapshot of every currently-running deployment.

        Emitted by ``broadcaster.broadcast_deployment_snapshot()`` to a
        single WebSocket target right after ``initial_status``. Lets the
        FE reconcile its local ``deploymentStatus`` / ``runningWorkflows``
        cache against the backend's source of truth on every (re)connect,
        instead of carrying stale "isRunning=true" forward through a
        backend restart that wiped the in-memory deployment dict.

        Distinct from ``workflow_lifecycle("deployment.started")`` —
        that fires when a deployment STARTS (one-shot edge event).
        ``deployment.snapshot`` is an idempotent state dump tied to
        client connect, not to a state transition. Empty list is
        meaningful: "no deployments are running, drop your stale
        local state."
        """
        return cls(
            source="machinaos://services/workflow",
            type=f"{_TYPE_PREFIX}workflow.deployment.snapshot",
            data={"running_workflow_ids": list(running_workflow_ids)},
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
            type=f"{_TYPE_PREFIX}agent.task.{'succeeded' if status == 'completed' else 'failed'}",
            subject=task_id,
            data=dict(data) if data else {"task_id": task_id, "agent": agent, "status": status},
        )

    def matches_type(self, pattern: str) -> bool:
        """Glob-style match on event type. ``"all"``/empty matches any.

        Patterns are matched against the type with the
        ``com.machinaos.`` reverse-DNS prefix stripped, so callers can
        write ``"credential.api_key.*"`` and still hit
        ``com.machinaos.credential.api_key.saved``. External-producer
        types (e.g. ``stripe.charge.succeeded`` from a Stripe webhook)
        have no prefix and match directly.

        Examples:
            "stripe.charge.succeeded" matches itself
            "credential.api_key.*"    matches "com.machinaos.credential.api_key.saved"
            "agent.*"                 matches "com.machinaos.agent.progress"
            "all" or ""               matches everything
        """
        if not pattern or pattern == "all":
            return True
        normalized = (self.type or "").removeprefix(_TYPE_PREFIX)
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return normalized.startswith(prefix + ".") or normalized == prefix
        return normalized == pattern
