"""Typed execution context passed to every plugin method.

Replaces the raw ``context: Dict[str, Any]`` that every legacy handler
receives. Keeps the dict for backwards-compat (``ctx.raw``) while
exposing typed accessors for the common cases and a Connection factory
for credential-backed calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from constants import DEFAULT_CREDENTIAL_CUSTOMER_ID

if TYPE_CHECKING:
    from services.plugin.connection import Connection


@dataclass
class NodeContext:
    """Execution context passed to every :meth:`BaseNode.execute` call.

    Construct via :meth:`from_legacy` to wrap the existing ``Dict``
    shape used by ``node_executor.dispatch``.
    """

    node_id: str
    node_type: str
    workflow_id: Optional[str] = None
    session_id: str = "default"
    execution_id: Optional[str] = None
    user_id: str = "owner"
    #: Namespace for stored credentials. Distinct from ``user_id``: that is
    #: the tenancy principal, this is which installation's OAuth tokens and
    #: API keys to read. Keeping them separate is what lets ``user_id``
    #: become a real authenticated subject without re-pointing every
    #: credential lookup. See constants.DEFAULT_CREDENTIAL_CUSTOMER_ID.
    credential_customer_id: str = "owner"
    workspace_dir: Optional[str] = None
    #: Temporal activity attempt number (1 on the first run and on every
    #: non-Temporal path). Attempt 2+ means Temporal re-dispatched this
    #: node after a retryable failure, a timeout, or a worker crash.
    attempt: int = 1
    #: Stable key for one logical activity execution, identical across
    #: its retry attempts: ``f"{workflow_run_id}-{activity_id}"`` per the
    #: Temporal Python docs. ``None`` outside Temporal. Nodes that opt
    #: into retries key their side effects on it so attempt 2 can detect
    #: and skip work attempt 1 already completed.
    idempotency_key: Optional[str] = None
    # Original outputs/nodes/edges for graph-aware nodes (console, pythonExecutor, …)
    outputs: Dict[str, Any] = field(default_factory=dict)
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    # Raw context dict — escape hatch for nodes that need extra fields
    # we haven't typed yet. Prefer typed access when possible.
    raw: Dict[str, Any] = field(default_factory=dict)
    # Connection factory injected by the executor; default stub so
    # pure-Pydantic tests work without auth_service wiring.
    _connection_factory: Optional[Callable[[str], "Connection"]] = None

    @classmethod
    def from_legacy(
        cls,
        node_id: str,
        node_type: str,
        context: Dict[str, Any],
        *,
        connection_factory: Optional[Callable[[str], "Connection"]] = None,
    ) -> "NodeContext":
        return cls(
            node_id=node_id,
            node_type=node_type,
            workflow_id=context.get("workflow_id"),
            session_id=context.get("session_id", "default"),
            execution_id=context.get("execution_id"),
            user_id=context.get("user_id", "owner"),
            # Falls back to the credential default, NOT to user_id — a real
            # authenticated subject must not silently become a credential
            # namespace nobody has stored tokens under.
            credential_customer_id=context.get(
                "credential_customer_id", DEFAULT_CREDENTIAL_CUSTOMER_ID
            ),
            workspace_dir=context.get("workspace_dir"),
            attempt=int(context.get("activity_attempt") or 1),
            idempotency_key=context.get("activity_idempotency_key"),
            outputs=context.get("outputs", {}) or {},
            nodes=context.get("nodes", []) or [],
            edges=context.get("edges", []) or [],
            raw=context,
            _connection_factory=connection_factory,
        )

    def connection(self, credential_id: str) -> "Connection":
        """Return an authed :class:`Connection` for ``credential_id``.

        The factory is injected by the executor; raising here (instead
        of returning a stub) surfaces missing-credential errors at the
        first call site rather than deep inside an API request.
        """
        if self._connection_factory is None:
            raise RuntimeError(
                f"No connection factory wired for credential '{credential_id}'. "
                "NodeContext must be constructed via the executor, not directly."
            )
        return self._connection_factory(credential_id)
