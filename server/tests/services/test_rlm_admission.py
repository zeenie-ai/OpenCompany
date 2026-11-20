from types import SimpleNamespace

import pytest

from services.agent_instances.admission import AdmissionGate, AdmissionHalted
from services.rlm.guarded import GuardedClient, guarded_rlm_class


class Client:
    model_name = "stub"

    def __init__(self):
        self.calls = 0

    def completion(self, prompt):
        self.calls += 1
        return "answer"

    async def acompletion(self, prompt):
        self.calls += 1
        return "answer"

    def get_last_usage(self):
        from rlm.core.types import ModelUsageSummary
        return ModelUsageSummary()


def halt(gate):
    for index in range(3):
        gate.record_invalid("invalid_tool_input", str(index))


@pytest.mark.asyncio
async def test_sync_and_async_client_fence_actual_dispatch():
    gate, client = AdmissionGate(), Client()
    guarded = GuardedClient(client, gate)
    assert guarded.completion("one") == "answer"
    assert await guarded.acompletion("two") == "answer"
    halt(gate)
    with pytest.raises(AdmissionHalted):
        guarded.completion("three")
    with pytest.raises(AdmissionHalted):
        await guarded.acompletion("four")
    assert client.calls == 2


def test_fallback_and_max_depth_cannot_bypass_gate(monkeypatch):
    import rlm.core.rlm as vendor
    gate, client = AdmissionGate(), Client()
    monkeypatch.setattr(vendor, "get_client", lambda *a, **kw: client)
    guarded = guarded_rlm_class(gate)(backend="openai", backend_kwargs={"model_name": "stub"}, max_depth=1)
    assert guarded._fallback_answer("one") == "answer"
    guarded._subcall("two")
    assert client.calls == 2
    halt(gate)
    for action in (lambda: guarded._fallback_answer("three"), lambda: guarded._subcall("four"), lambda: guarded.completion("five")):
        with pytest.raises(AdmissionHalted):
            action()
    assert client.calls == 2


def test_recursive_child_and_all_registered_clients_share_gate(monkeypatch):
    import rlm.core.rlm as vendor
    clients = []
    def client_factory(*args, **kwargs):
        client = Client()
        clients.append(client)
        return client
    monkeypatch.setattr(vendor, "get_client", client_factory)
    gate = AdmissionGate()
    cls = guarded_rlm_class(gate)
    engine = cls(backend="openai", backend_kwargs={"model_name": "stub"}, max_depth=3,
                 other_backends=["openai"], other_backend_kwargs=[{"model_name": "other"}])
    seen_children = []
    def child_completion(self, prompt, root_prompt=None):
        seen_children.append(type(self))
        self._fallback_answer(prompt)
        halt(gate)
        self._fallback_answer("denied")
    monkeypatch.setattr(vendor.RLM, "completion", child_completion)
    with pytest.raises(AdmissionHalted):
        engine._subcall("recursive")
    assert seen_children == [cls]
    assert sum(client.calls for client in clients) == 1


def test_spawn_context_wraps_root_other_and_registered_clients(monkeypatch):
    import rlm.core.rlm as vendor
    gate, clients = AdmissionGate(), []
    def factory(*args, **kwargs):
        client = Client()
        clients.append(client)
        return client
    class Handler:
        host, port = "localhost", 1
        def __init__(self, client, other_backend_client=None):
            self.clients = [client, other_backend_client]
        def register_client(self, name, client): self.clients.append(client)
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(vendor, "get_client", factory)
    monkeypatch.setattr(vendor, "LMHandler", Handler)
    monkeypatch.setattr(vendor, "get_environment", lambda *a: SimpleNamespace(cleanup=lambda: None))
    engine = guarded_rlm_class(gate)(backend="openai", backend_kwargs={"model_name": "stub"},
        other_backends=["openai"], other_backend_kwargs=[{"model_name": "other"}])
    with engine._spawn_completion_context("prompt") as (handler, _):
        assert all(isinstance(client, GuardedClient) for client in handler.clients)
        halt(gate)
        for client in handler.clients:
            with pytest.raises(AdmissionHalted):
                client.completion("denied")
    assert sum(client.calls for client in clients) == 0


@pytest.mark.asyncio
async def test_cancellation_fences_requests_but_waits_for_worker_thread():
    import asyncio
    import threading
    from services.rlm.guarded import run_fenced_completion

    gate = AdmissionGate()
    started, finish, drained = threading.Event(), threading.Event(), threading.Event()
    def completion():
        gate.admit("model")
        started.set()
        try:
            finish.wait(2)
            gate.admit("nested_model")
        finally:
            drained.set()
    task = asyncio.create_task(run_fenced_completion(completion, gate))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert gate.halted and not task.done() and not drained.is_set()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set() and gate.admitted == 1
    finally:
        finish.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_callable_receipts_separate_turns_and_replay_stable_source_positions(monkeypatch):
    import asyncio
    from pydantic import BaseModel
    from services.rlm.adapters import ToolBridgeAdapter

    class Input(BaseModel):
        value: str

    async def execute(name, arguments, config):
        return config["tool_call_id"]
    monkeypatch.setattr("services.handlers.tools.execute_tool", execute)
    entry = {"node_type": "contextTestTool", "node_id": "tool", "_agent_tool_name": "lookup", "_agent_tool_input_model": Input}
    def bridge(run):
        return ToolBridgeAdapter.bridge([entry], {"agent_run_id": run}, loop=asyncio.get_running_loop())["lookup"]["tool"]
    def invoke(tool):
        return tool(value="same source position")

    first_turn = bridge("first-turn")
    first = await asyncio.to_thread(invoke, first_turn)
    assert await asyncio.to_thread(invoke, first_turn) != first
    assert await asyncio.to_thread(invoke, bridge("first-turn")) == first
    assert await asyncio.to_thread(invoke, bridge("next-turn")) != first
