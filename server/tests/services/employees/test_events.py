"""``employee_lifecycle`` envelopes and the coalesced ``updated`` broadcast."""

from __future__ import annotations

import asyncio

import pytest

from services.employees import events



@pytest.fixture(autouse=True)
def captured(monkeypatch):
    frames = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

    import services.status_broadcaster as status_broadcaster

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    events.reset_for_tests()
    yield frames
    events.set_summary_builder(None)
    events.reset_for_tests()


def test_envelope_shape():
    event = events.employee_lifecycle_event("hired", workflow_id="7", revision=3, employee={"workflow_id": "7"})
    dumped = event.model_dump(mode="json", exclude_none=True)
    assert dumped["type"] == "com.opencompany.employee.hired"
    assert dumped["source"] == "opencompany://services/employees"
    assert dumped["subject"] == "7"
    assert dumped["data"] == {"workflow_id": "7", "revision": 3, "employee": {"workflow_id": "7"}}
    # New contracts keep application scope inside data.
    assert "workflow_id" not in dumped


def test_removed_carries_identity_only():
    dumped = events.employee_lifecycle_event("removed", workflow_id="7", revision=4, reason="deleted").model_dump(
        mode="json", exclude_none=True
    )
    assert dumped["data"] == {"workflow_id": "7", "revision": 4, "reason": "deleted"}


async def test_broadcast_uses_the_employee_lifecycle_wire_key(captured):
    await events.broadcast_employee_event("removed", workflow_id="7", revision=1)
    assert captured[0]["type"] == "employee_lifecycle"
    assert captured[0]["data"]["type"] == "com.opencompany.employee.removed"


async def test_updates_coalesce_to_one_per_window(captured, monkeypatch):
    monkeypatch.setattr(events, "COALESCE_SECONDS", 0.05)
    builds = []

    async def builder(workflow_id):
        builds.append(workflow_id)
        return {"workflow_id": workflow_id, "revision": len(builds)}

    events.set_summary_builder(builder)
    for _ in range(5):
        events.employee_changed("9")
    await asyncio.sleep(0.01)
    assert len(captured) == 1  # the first change goes out at once

    for _ in range(5):
        events.employee_changed("9")
    await asyncio.sleep(0.12)
    assert len(captured) == 2  # the burst collapsed into one trailing update
    assert captured[-1]["data"]["data"]["revision"] == 2
    assert builds == ["9", "9"]


async def test_employees_coalesce_independently(captured, monkeypatch):
    monkeypatch.setattr(events, "COALESCE_SECONDS", 0.05)

    async def builder(workflow_id):
        return {"workflow_id": workflow_id, "revision": 1}

    events.set_summary_builder(builder)
    events.employee_changed("1")
    events.employee_changed("2")
    await asyncio.sleep(0.01)
    assert sorted(frame["data"]["subject"] for frame in captured) == ["1", "2"]


async def test_a_vanished_employee_sends_nothing(captured):
    async def builder(workflow_id):
        return None

    events.set_summary_builder(builder)
    events.employee_changed("3")
    await asyncio.sleep(0.01)
    assert captured == []


def test_control_listeners_run_in_order_and_survive_a_failure(monkeypatch):
    from services.deployment import control as control_module

    monkeypatch.setattr(control_module, "_CONTROL_LISTENERS", [])
    calls = []

    def broken(workflow_id):
        raise RuntimeError("boom")

    control_module.register_control_listener(broken)
    control_module.register_control_listener(calls.append)
    control_module.register_control_listener(calls.append)  # a no-op second time
    control_module.notify_control_changed("w1")
    assert calls == ["w1"]


async def test_control_changes_skip_the_window(captured, monkeypatch):
    monkeypatch.setattr(events, "COALESCE_SECONDS", 10.0)
    states = iter(["pausing", "paused"])

    async def builder(workflow_id):
        return {"workflow_id": workflow_id, "revision": 1, "state": next(states)}

    events.set_summary_builder(builder)
    events.employee_changed_now("4")
    events.employee_changed_now("4")  # inside the (long) window
    await asyncio.sleep(0.01)
    assert [frame["data"]["data"]["employee"]["state"] for frame in captured] == ["pausing", "paused"]


async def test_builds_for_one_employee_do_not_overtake(captured):
    order = []
    release = asyncio.Event()

    async def builder(workflow_id):
        call = len(order)
        order.append(call)
        if call == 0:
            await release.wait()  # the first build is slow
        return {"workflow_id": workflow_id, "revision": call}

    events.set_summary_builder(builder)
    events.employee_changed_now("5")
    events.employee_changed_now("5")
    await asyncio.sleep(0.01)
    assert captured == []  # the second build waits for the first
    release.set()
    await asyncio.sleep(0.01)
    assert [frame["data"]["data"]["revision"] for frame in captured] == [0, 1]


async def test_a_control_change_counts_toward_the_window(captured, monkeypatch):
    monkeypatch.setattr(events, "COALESCE_SECONDS", 0.05)

    async def builder(workflow_id):
        return {"workflow_id": workflow_id, "revision": 1}

    events.set_summary_builder(builder)
    events.employee_changed_now("6")
    events.employee_changed("6")  # a run record right after: trailing, not immediate
    await asyncio.sleep(0.01)
    assert len(captured) == 1
    await asyncio.sleep(0.1)
    assert len(captured) == 2


def test_employees_listen_to_control_changes():
    import services.employees  # noqa: F401
    from services.deployment.control import _CONTROL_LISTENERS

    assert events.employee_changed_now in _CONTROL_LISTENERS
