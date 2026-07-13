"""WorkflowEvent — CloudEvents v1.0 envelope (in-house, no external dep).

Field set mirrors https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md
verbatim so future interop with EventBridge / Knative is a JSON-schema swap.
The OpenCompany extensions (``workflow_id``, ``trigger_node_id``,
``correlation_id``) ride as CloudEvents extension attributes. Spec §3.1
SHOULDs lowercase-alphanumeric extension names; we deliberately keep
Python snake_case for codebase consistency — the rest of the project
uses snake_case as a strict naming convention and the readability win
outweighs the spec recommendation. Internal interop only; if we ever
publish events to an external CloudEvents broker, an alias layer can
translate at the producer boundary.

Type strings carry the reverse-DNS prefix ``com.opencompany.`` per
Primer guidance. ``dataschema`` is auto-populated from ``type`` so
every typed factory produces a schema URI the consumer can validate
against.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


_TYPE_PREFIX = "com.opencompany."
_LEGACY_TYPE_PREFIX = "com.machinaos."
_DATASCHEMA_BASE = "opencompany://schemas/events/"


def _unprefixed_event_type(event_type: str) -> str:
    """Strip either the canonical or legacy product namespace."""

    for prefix in (_TYPE_PREFIX, _LEGACY_TYPE_PREFIX):
        if event_type.startswith(prefix):
            return event_type.removeprefix(prefix)
    return event_type


def equivalent_event_types(event_type: str) -> tuple[str, ...]:
    """Return canonical/legacy equivalents for Temporal replay routing.

    Running listener workflows may still have ``com.machinaos.*`` stored in
    their ``EventType`` Search Attribute. New producers emit
    ``com.opencompany.*``; dispatch queries both during the transition.
    """

    if event_type.startswith(_TYPE_PREFIX):
        suffix = event_type.removeprefix(_TYPE_PREFIX)
        return event_type, f"{_LEGACY_TYPE_PREFIX}{suffix}"
    if event_type.startswith(_LEGACY_TYPE_PREFIX):
        suffix = event_type.removeprefix(_LEGACY_TYPE_PREFIX)
        return f"{_TYPE_PREFIX}{suffix}", event_type
    return (event_type,)


def _dataschema_for(event_type: str) -> str:
    """Compute the ``dataschema`` URI for a given event ``type``.

    Strips the ``com.opencompany.`` prefix when present so the URI segment
    matches the un-prefixed type (e.g. ``credential.api_key.saved``).
    External-producer events (e.g. ``stripe.charge.succeeded`` from a
    Stripe webhook) keep their type verbatim in the URI segment.
    """
    seg = _unprefixed_event_type(event_type) if event_type else event_type
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
            source="opencompany://services/credentials",
            type=f"{_TYPE_PREFIX}credential.{action}",
            subject=provider,
            data={"provider": provider, **extra} if extra else {"provider": provider},
        )

    # ``WorkflowEvent.connection_status(...)`` previously lived here as a
    # parameterized factory but had zero callers after Wave-12 B1-B3 gave
    # each plugin its own typed factory (``android_connection_status`` /
    # ``whatsapp_connection_status`` / ``telegram_connection_status`` in
    # ``server/nodes/<plugin>/_events.py``). Removed in Wave 15.1.

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
            source=f"opencompany://nodes/{provider}",
            type=f"{_TYPE_PREFIX}{provider}.oauth.completed",
            subject=identifier,
            data=dict(data) if data else {"identifier": identifier},
        )

    # ``WorkflowEvent.message(...)`` previously lived here as a
    # parameterized factory but had zero callers. Plugin-specific
    # message events belong in ``server/nodes/<plugin>/_events.py``
    # (see RFC §6.4) since each plugin's message payload diverges
    # (telegram: chat_id; whatsapp: phone_number + group_jid; ...).

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
            source="opencompany://services/agent_team",
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
            "imported",
            "renamed",
        ],
        *,
        workflow_id: str,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "WorkflowEvent":
        """Workflow lifecycle event. Carries ``workflow_id`` both as
        ``subject`` (CloudEvents convention) and as the
        ``workflow_id`` extension attribute (existing reader
        contract).

        ``imported`` fires from ``services.workflow_import.import_workflow``
        after the new workflow is persisted. Frontend listeners invalidate
        the workflows query so the sidebar picks up the new entry across
        all connected clients (browser tabs).

        ``renamed`` fires from the ``rename_workflow`` WebSocket
        handler. ``data`` carries ``{name, slug, old_slug}`` so the
        frontend updates the open workflow's display + invalidates the
        workflows query for the sidebar. The ``workflow_id`` (UUID) is
        unchanged across the rename — only the human-readable fields
        change.
        """
        return cls(
            source="opencompany://services/workflow",
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
        """Live agent-loop progress for one agent node.

        Emitted from inside the agent loop in
        ``services/ai.py:execute_agent`` and ``execute_chat_agent`` after
        each LLM turn. ``iteration`` advances on every model invocation;
        ``max_iterations`` mirrors the loop's hard cap (sourced from
        ``llm_defaults.json:agent.recursion_limit``).

        ``subject`` carries the executing node id so the FE routes the
        update straight to ``nodeStatusStore`` for that node. The
        ``workflow_id`` extension attribute scopes per-workflow displays
        the same way ``node_status`` broadcasts do.
        """
        return cls(
            source="opencompany://services/agent",
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
    def node_parameters_updated(
        cls,
        node_id: str,
        *,
        parameters: Mapping[str, Any],
        workflow_id: Optional[str] = None,
        version: int = 1,
        source_hint: str = "user",
    ) -> "WorkflowEvent":
        """Server-pushed notification that a node's parameters changed.

        Three emission sites today:
          - ``routers/websocket.py:handle_save_node_parameters`` — user
            edited the parameter panel.
          - ``services/cli_agent/service.py:_persist_memory`` — Claude
            Code CLI memory bridge appended a turn to ``simpleMemory``.
          - ``services/temporal/agent_activities.py:persist_agent_turn``
            — F4.B AgentWorkflow's per-turn memory append.

        ``subject`` is the node_id so the FE routes the refetch to the
        right parameter panel. ``source_hint`` distinguishes user edits
        (``"user"``) from autonomous writes (``"agent"`` / ``"cli"``)
        so the panel can choose whether to confirm a re-render against
        the user's in-progress local edits.
        """
        return cls(
            source="opencompany://services/parameters",
            type=f"{_TYPE_PREFIX}node.parameters.updated",
            subject=node_id,
            workflow_id=workflow_id,
            data={
                "node_id": node_id,
                "parameters": dict(parameters),
                "version": version,
                "source": source_hint,
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
            source="opencompany://services/workflow",
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
        """Delegated child-agent completion.

        Both success and failure share one ``type``
        (``com.opencompany.agent.task.completed``) so the
        ``TriggerListenerWorkflow`` for ``taskTrigger`` can register a
        single ``EventType`` Search Attribute and match every
        completion event via :func:`services.events.dispatch.emit`'s
        Visibility query. Status discrimination lives in ``data.status``
        (``"completed"`` or ``"error"``) — :class:`TaskTriggerNode`'s
        filter already keys off this field.

        Pre-fix (commit ?) the type split into ``.succeeded`` /
        ``.failed`` which left the listener's SA value matching only
        one branch and silently lost the other half of events.
        """
        return cls(
            source="opencompany://services/agent",
            type=f"{_TYPE_PREFIX}agent.task.completed",
            subject=task_id,
            data=dict(data) if data else {"task_id": task_id, "agent": agent, "status": status},
        )

    @classmethod
    def claude_session_spawned(
        cls,
        memory_node_id: str,
        *,
        session_uuid: str,
        pid: int,
        workflow_id: Optional[str] = None,
    ) -> "WorkflowEvent":
        """Pool-managed claude session spawned fresh.

        Fired once per cold-start of a pooled session — the warm-reuse
        path emits ``claude.session.cleared`` instead. ``subject`` is
        the simpleMemory node so the FE can wire a per-memory-node
        status badge.
        """
        return cls(
            source="opencompany://services/cli_agent",
            type=f"{_TYPE_PREFIX}claude.session.spawned",
            subject=memory_node_id,
            workflow_id=workflow_id,
            data={
                "memory_node_id": memory_node_id,
                "session_uuid": session_uuid,
                "pid": pid,
            },
        )

    @classmethod
    def claude_session_cleared(
        cls,
        memory_node_id: str,
        *,
        old_session_uuid: str,
        new_session_uuid: str,
        workflow_id: Optional[str] = None,
    ) -> "WorkflowEvent":
        """Pool sent ``/clear``; claude minted a fresh session UUID.

        Per issue `claude-code#32871
        <https://github.com/anthropics/claude-code/issues/32871>`_,
        ``/clear`` creates a NEW session UUID with a NEW JSONL file
        rather than clearing in-place. The pool captures the new UUID
        via :class:`JsonlDirWatcher` and emits this event so the memory
        bridge + UI can track the rotation.
        """
        return cls(
            source="opencompany://services/cli_agent",
            type=f"{_TYPE_PREFIX}claude.session.cleared",
            subject=memory_node_id,
            workflow_id=workflow_id,
            data={
                "memory_node_id": memory_node_id,
                "old_session_uuid": old_session_uuid,
                "new_session_uuid": new_session_uuid,
            },
        )

    @classmethod
    def claude_session_terminated(
        cls,
        memory_node_id: str,
        *,
        reason: Literal["idle", "crashed", "evicted", "shutdown", "explicit"],
        session_uuid: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> "WorkflowEvent":
        """Pool-managed claude session ended.

        ``reason`` discriminates the cause: ``idle`` (reaper exceeded
        TTL), ``crashed`` (process died unexpectedly), ``evicted`` (LRU
        eviction at max pool size), ``shutdown`` (FastAPI lifespan stop),
        ``explicit`` (caller-driven terminate).
        """
        return cls(
            source="opencompany://services/cli_agent",
            type=f"{_TYPE_PREFIX}claude.session.terminated",
            subject=memory_node_id,
            workflow_id=workflow_id,
            data={
                "memory_node_id": memory_node_id,
                "reason": reason,
                **({"session_uuid": session_uuid} if session_uuid else {}),
            },
        )

    @classmethod
    def claude_session_usage(
        cls,
        memory_node_id: str,
        *,
        session_uuid: str,
        total_cost_usd: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        duration_ms: Optional[int] = None,
        num_turns: Optional[int] = None,
        workflow_id: Optional[str] = None,
    ) -> "WorkflowEvent":
        """Per-turn cost / token usage from a claude ``result`` event.

        Sourced directly from the JSONL ``result.usage`` block —
        same data ``/usage`` displays in the TUI, but structured. The
        FE wires a usage panel onto simpleMemory by listening for this
        type (rather than scraping the TUI's ``/usage`` output, which
        is plain text per Anthropic's docs and not parseable).
        """
        return cls(
            source="opencompany://services/cli_agent",
            type=f"{_TYPE_PREFIX}claude.session.usage",
            subject=memory_node_id,
            workflow_id=workflow_id,
            data={
                "memory_node_id": memory_node_id,
                "session_uuid": session_uuid,
                "total_cost_usd": total_cost_usd,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read_input_tokens,
                    "cache_creation_input_tokens": cache_creation_input_tokens,
                },
                **({"duration_ms": duration_ms} if duration_ms is not None else {}),
                **({"num_turns": num_turns} if num_turns is not None else {}),
            },
        )

    def matches_type(self, pattern: str) -> bool:
        """Glob-style match on event type. ``"all"``/empty matches any.

        Patterns are matched against the type with the
        ``com.opencompany.`` reverse-DNS prefix stripped, so callers can
        write ``"credential.api_key.*"`` and still hit
        ``com.opencompany.credential.api_key.saved``. External-producer
        types (e.g. ``stripe.charge.succeeded`` from a Stripe webhook)
        have no prefix and match directly.

        Examples:
            "stripe.charge.succeeded" matches itself
            "credential.api_key.*"    matches "com.opencompany.credential.api_key.saved"
            "agent.*"                 matches "com.opencompany.agent.progress"
            "all" or ""               matches everything
        """
        if not pattern or pattern == "all":
            return True
        normalized = _unprefixed_event_type(self.type or "")
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return normalized.startswith(prefix + ".") or normalized == prefix
        return normalized == pattern
