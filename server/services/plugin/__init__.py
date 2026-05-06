"""Wave 11 plugin-first node system.

One class per node. Inherit from :class:`ActionNode`,
:class:`TriggerNode`, or :class:`ToolNode`, declare metadata + Pydantic
``Params`` / ``Output``, and decorate operation methods with
``@Operation``. :func:`services.node_registry.register_node` accepts the
class directly and writes into the existing four registries.

Public API (re-exported here):

- :class:`BaseNode` — invariants + lifecycle (broadcast, track, wrap)
- :class:`ActionNode` / :class:`TriggerNode` / :class:`ToolNode` — kinds
- :class:`Operation` — decorator; declarative routing optional
- :class:`Routing` — pure-declarative REST DSL
- :class:`Credential` / :class:`OAuth2Credential` / :class:`ApiKeyCredential`
- :class:`Connection` — auth-aware HTTP facade (Nango pattern)
- :class:`NodeContext` — typed execution context
- :class:`Interceptor` — cross-cutting concerns (Temporal pattern)
- :class:`RetryPolicy` — per-node Temporal retry knobs

Temporal integration (11.F): every subclass exposes
:meth:`BaseNode.as_activity` to produce an ``@activity.defn`` wrapper.
``task_queue`` class attribute routes to a specialised worker pool.
"""

from __future__ import annotations

from services.plugin.base import BaseNode, NodeUserError
from services.plugin.action import ActionNode
from services.plugin.trigger import TriggerNode
from services.plugin.polling import PollingTriggerNode
from services.plugin.tool import ToolNode
from services.plugin.operation import Operation, OperationSpec
from services.plugin.routing import Routing, RoutingRequest, RoutingOutput, execute_routing
from services.plugin.credential import (
    Credential,
    OAuth2Credential,
    ApiKeyCredential,
    CREDENTIAL_REGISTRY,
)
from services.plugin.connection import Connection
from services.plugin.context import NodeContext
from services.plugin.scaling import (
    RetryPolicy,
    TaskQueue,
    DEFAULT_START_TO_CLOSE,
    DEFAULT_HEARTBEAT,
)
from services.plugin.interceptor import Interceptor, InterceptorChain

__all__ = [
    "BaseNode",
    "NodeUserError",
    "ActionNode",
    "TriggerNode",
    "PollingTriggerNode",
    "ToolNode",
    "Operation",
    "OperationSpec",
    "Routing",
    "RoutingRequest",
    "RoutingOutput",
    "execute_routing",
    "Credential",
    "OAuth2Credential",
    "ApiKeyCredential",
    "CREDENTIAL_REGISTRY",
    "Connection",
    "NodeContext",
    "RetryPolicy",
    "TaskQueue",
    "DEFAULT_START_TO_CLOSE",
    "DEFAULT_HEARTBEAT",
    "Interceptor",
    "InterceptorChain",
]
