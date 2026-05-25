"""Interceptor chain for cross-cutting concerns (Temporal pattern).

Wrap every :meth:`BaseNode.execute` call in an ordered chain — logging,
cost tracking, heartbeats, retry. Each interceptor calls ``next(input)``
like middleware. Registered globally in ``services/plugin/base.py``;
plugins don't need to know they exist.

Keep the interface tiny on purpose — this is an extension point, not a
framework. If you find yourself adding seven interceptors, something
else is wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class InterceptorInput:
    node_id: str
    node_type: str
    parameters: Dict[str, Any]
    context: Any  # NodeContext (avoid cycle in type hint)


class Interceptor(ABC):
    """Interceptor ABC — subclass and implement :meth:`intercept`."""

    @abstractmethod
    async def intercept(
        self,
        input: InterceptorInput,
        next: Callable[[InterceptorInput], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Wrap the call. Must call ``next(input)`` exactly once (unless
        short-circuiting, e.g. for a cache hit)."""
        ...


class InterceptorChain:
    """Compose interceptors into a single callable matching the
    :meth:`BaseNode.execute` signature. Order matters — registered first
    runs outermost.
    """

    def __init__(self, interceptors: Optional[List[Interceptor]] = None):
        self._interceptors: List[Interceptor] = list(interceptors or [])

    def add(self, interceptor: Interceptor) -> None:
        self._interceptors.append(interceptor)

    async def run(
        self,
        input: InterceptorInput,
        terminal: Callable[[InterceptorInput], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Invoke the chain, ending with ``terminal`` (the actual
        plugin call). Each interceptor sees the same input."""
        # Build nested call chain in reverse so interceptors[0] wraps outermost.
        call = terminal
        for interceptor in reversed(self._interceptors):
            current_call = call

            async def wrapped(inp, _intr=interceptor, _nxt=current_call):
                return await _intr.intercept(inp, _nxt)

            call = wrapped
        return await call(input)
