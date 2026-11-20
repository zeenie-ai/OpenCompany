"""Instance-local RLM client admission; no process-wide vendor monkeypatches.

The pinned RLM creates clients in three places. Rebinding the globals of those
methods on this instance preserves its budget/cleanup behavior while ensuring
every constructed client and recursively constructed RLM shares the same gate.
Fail closed if a vendor upgrade changes these construction seams.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import FunctionType
from typing import Any

from services.agent_instances.admission import AdmissionGate


async def run_fenced_completion(completion_fn, gate: AdmissionGate | None):
    """Cancellation fences new requests and waits for the actual worker thread."""
    import asyncio
    completion = asyncio.create_task(asyncio.to_thread(completion_fn))
    try:
        return await asyncio.shield(completion)
    except asyncio.CancelledError:
        if gate is not None:
            gate.revoke("interrupted")
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if completion.done() and not completion.cancelled():
            completion.exception()
        raise


class GuardedClient:
    def __init__(self, client: Any, gate: AdmissionGate):
        self._client, self._gate = client, gate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def completion(self, *args, **kwargs):
        self._gate.admit("model")
        return self._client.completion(*args, **kwargs)

    async def acompletion(self, *args, **kwargs):
        self._gate.admit("model")
        return await self._client.acompletion(*args, **kwargs)


def guarded_rlm_class(gate: AdmissionGate):
    from rlm import RLM

    spawn = getattr(RLM._spawn_completion_context, "__wrapped__", None)
    methods = (spawn, RLM._fallback_answer, RLM._subcall)
    if any(not isinstance(method, FunctionType) or "get_client" not in method.__code__.co_names for method in methods):
        raise RuntimeError("This RLM version cannot enforce agent request admission")
    if "RLM" not in RLM._subcall.__code__.co_names:
        raise RuntimeError("This RLM recursion version cannot enforce agent request admission")

    def bind(method):
        original_get_client = method.__globals__["get_client"]

        def get_client(*args, **kwargs):
            gate.check()
            return GuardedClient(original_get_client(*args, **kwargs), gate)

        namespace = {**method.__globals__, "get_client": get_client, "RLM": GuardedRLM}
        clone = FunctionType(method.__code__, namespace, method.__name__, method.__defaults__, method.__closure__)
        clone.__kwdefaults__ = method.__kwdefaults__
        return clone

    class GuardedRLM(RLM):
        def completion(self, *args, **kwargs):
            gate.check()
            result = super().completion(*args, **kwargs)
            gate.check()  # A REPL may catch a denied request; that is not success.
            return result

        @contextmanager
        def _spawn_completion_context(self, prompt):
            yield from bind(spawn)(self, prompt)

        def _fallback_answer(self, message):
            return bind(RLM._fallback_answer)(self, message)

        def _subcall(self, prompt, model=None):
            gate.check()
            result = bind(RLM._subcall)(self, prompt, model)
            gate.check()
            return result

    return GuardedRLM
