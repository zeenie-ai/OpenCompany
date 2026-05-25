"""``@Operation`` decorator — the unit of work inside a multi-op node.

A multi-op ``ActionNode`` (gmail send/search/read, whatsappDb's 18 ops,
calendar CRUD) marks each method with ``@Operation("name")``. The base
class dispatches on the ``operation`` Params field automatically.

For single-op nodes, decorate the one method with
``@Operation("default")`` (or ``@Operation("search")``, etc.) — the
node's :class:`Params` doesn't need an ``operation`` field, and the
lone op runs on every call.

Declarative REST: pass ``routing=Routing(...)``, leave the method body
empty (``pass``), and :func:`execute_routing` takes over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from services.plugin.routing import Routing


@dataclass
class OperationSpec:
    """Metadata attached to a decorated method by :class:`Operation`.

    ``method`` is the original async callable (bound later to the node
    instance). Registry lookup: ``node_cls._operations[name] -> OperationSpec``.
    """

    name: str
    method: Callable[..., Awaitable[Any]]
    routing: Optional[Routing] = None
    cost: Optional[Dict[str, Any]] = None  # {"usage": "gmail_send", "count": 1}
    annotations: Dict[str, Any] = field(default_factory=dict)


class Operation:
    """Decorator form — attaches an :class:`OperationSpec` to the method.

    Usage::

        @Operation("send", cost={"usage": "gmail_send", "count": 1})
        async def send(self, ctx, params): ...

        @Operation("search", routing=Routing(request=..., output=...))
        async def search(self, ctx, params): pass
    """

    def __init__(
        self,
        name: str,
        *,
        routing: Optional[Routing] = None,
        cost: Optional[Dict[str, Any]] = None,
        annotations: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.routing = routing
        self.cost = cost
        self.annotations = annotations or {}

    def __call__(self, method):
        spec = OperationSpec(
            name=self.name,
            method=method,
            routing=self.routing,
            cost=self.cost,
            annotations=self.annotations,
        )
        # Tag the function; BaseNode's metaclass/__init_subclass__ collects.
        method.__operation_spec__ = spec
        return method


def collect_operations(node_cls: type) -> Dict[str, OperationSpec]:
    """Walk the class MRO, collect every method tagged with
    ``__operation_spec__``. Subclass overrides win. Returns ``{name: spec}``.
    """
    ops: Dict[str, OperationSpec] = {}
    # MRO reverse so subclass wins — iterate ancestors first, then subclass.
    for klass in reversed(node_cls.__mro__):
        for attr in klass.__dict__.values():
            spec = getattr(attr, "__operation_spec__", None)
            if spec is not None:
                ops[spec.name] = spec
    return ops
