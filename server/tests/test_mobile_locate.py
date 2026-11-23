"""The Mobile element finder (nodes/mobile/_locate.py): when the engine's tap
by text finds nothing in the UI tree, the worker asks the broker to locate
the element on a screenshot, and a sure box from the judgment service
becomes the bounds the engine taps. Anything short of that is "Element not
found", as before, and the phone never goes into recovery for it."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import aiohttp
import pytest
import structlog

import nodes.mobile._runtime as runtime_module
from nodes.mobile._locate import JUDGE_TIMEOUT_SECONDS, locate_on_screen
from nodes.mobile._runtime import MobileRuntime
from services.judgment import Answer, Judgment
from tests.test_mobile_runtime import FakeProcess, run_args

#: The screenshot is 100 pixels wide and 200 high.
WIDTH, HEIGHT = 100, 200
#: Whose the judgment is, as MobileRuntime.run keeps it.
RUN = {"principal": "owner", "workflow_id": "workflow", "node_id": "agent",
       "log": {"chat_run_id": "chat-1", "employee_job_id": None}}


def screenshot() -> str:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "white").save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode()


def answers(seen=0.95, left=0.1, top=0.2, right=0.3, bottom=0.4):
    return {"on_screen": Answer(type="noul", probability=seen),
            "where": Answer(type="box", left=left, top=top, right=right, bottom=bottom, confidence=0.9)}


@pytest.fixture
def judge(monkeypatch):
    """The judgment service, replaced: it answers ``judge.answers`` (None: no
    judgment; an exception: raised) and keeps each call with the log context
    it ran in."""
    fake = SimpleNamespace(answers=answers(), calls=[])

    async def answer(state, questions, **kwargs):
        fake.calls.append(SimpleNamespace(state=state, questions=questions, kwargs=kwargs,
                                          bound=structlog.contextvars.get_contextvars()))
        if isinstance(fake.answers, Exception):
            raise fake.answers
        if fake.answers is None:
            return Judgment(available=False, reason="not_connected")
        return Judgment(available=True, answers=fake.answers, provider="typesafe", model="jev-1.14.0", calibrated=True)

    monkeypatch.setattr("services.judgment.judge", answer)
    return fake


# The finder


async def test_a_sure_box_becomes_pixels_of_the_screenshot(judge):
    shot = screenshot()

    assert await locate_on_screen(shot, "Settings", RUN) == {
        "found": True, "bounds": {"x1": 10, "y1": 40, "x2": 30, "y2": 80}}

    (call,) = judge.calls
    assert call.state == "A phone screen."
    assert call.questions == {
        "on_screen": {"type": "noul", "description": 'An element showing "Settings" is visible on this screen.'},
        "where": {"type": "box", "description": 'The element showing "Settings".'},
    }
    (image,) = call.kwargs["images"]
    assert (image.format, image.data) == ("png", base64.b64decode(shot))
    assert call.kwargs["timeout"] == JUDGE_TIMEOUT_SECONDS
    assert (call.kwargs["node_id"], call.kwargs["workflow_id"], call.kwargs["session_id"], call.kwargs["principal"]) == (
        "agent", "workflow", "workflow", "owner")
    assert call.bound["chat_run_id"] == "chat-1"


@pytest.mark.parametrize(
    "judged, expected",
    [
        pytest.param(answers(seen=0.79), {"found": False}, id="not sure it is there"),
        pytest.param(answers(left=0.3, right=0.1), {"found": False}, id="edges out of order"),
        pytest.param(answers(bottom=1.2), {"found": False}, id="outside the image"),
        pytest.param(answers(left=0.501, right=0.504), {"found": False}, id="narrower than a pixel"),
        pytest.param(None, {"found": False, "reason": "not_connected"}, id="no judgment"),
    ],
)
async def test_anything_short_of_a_sure_box_is_not_found(judge, judged, expected):
    judge.answers = judged

    assert await locate_on_screen(screenshot(), "Settings", RUN) == expected


# The broker


@pytest.fixture
async def broker(monkeypatch, judge):
    """The active run's engine, talking to the real loopback broker over a
    fake device. The broker starts in an earlier run's log context, as it does
    when that run's task was the first to need it."""
    runtime = MobileRuntime()
    driver = AsyncMock(return_value={"base64": screenshot()})
    monkeypatch.setattr(runtime, "driver_call", driver)
    step = AsyncMock()
    monkeypatch.setattr(runtime, "_record_task_step", step)
    log = Mock()
    monkeypatch.setattr(runtime_module, "event", log)
    lease = await runtime.control.claim("run:run")
    runtime.capabilities["capability"] = ("run", lease)
    runtime.active = {"run_id": "run", "workflow_id": "workflow", "node_id": "agent", "status": "running"}
    runtime._run_attribution["run"] = RUN
    with structlog.contextvars.bound_contextvars(chat_run_id="earlier-chat", employee_job_id="earlier-job"):
        url = await runtime.ensure_broker()
    try:
        async with aiohttp.ClientSession() as client:

            async def post(operation="locate", parameters=None, operation_id="one"):
                body = {"epoch": lease.epoch, "operation": operation, "operation_id": operation_id,
                        "parameters": {"text": "Settings"} if parameters is None else parameters}
                async with client.post(url, headers={"Authorization": "Bearer capability"}, json=body) as reply:
                    return reply.status, await reply.json()

            yield SimpleNamespace(runtime=runtime, post=post, driver=driver, step=step, log=log)
    finally:
        await runtime.broker_runner.cleanup()


async def test_the_active_run_finds_an_element_on_a_fresh_screenshot(broker, judge):
    status, body = await broker.post(parameters={"text": "  Settings  "})

    assert (status, body) == (200, {"success": True, "result": {
        "found": True, "bounds": {"x1": 10, "y1": 40, "x2": 30, "y2": 80}}})
    broker.driver.assert_awaited_once_with("screenshot")
    assert broker.runtime.control.results == {}  # no screenshot is kept
    broker.step.assert_not_awaited()  # not a step of the task
    broker.log.assert_called_once_with("element_located", run_id="run", code=None, duration_ms=ANY)
    (call,) = judge.calls
    assert call.questions["where"]["description"] == 'The element showing "Settings".'
    assert call.kwargs["principal"] == "owner"
    # This run's chat run and no job, never those of the run the broker started in.
    assert call.bound["chat_run_id"] == "chat-1"
    assert call.bound["employee_job_id"] is None


@pytest.mark.parametrize(
    "active, parameters",
    [
        pytest.param(None, {"text": "Settings"}, id="no task on the phone"),
        pytest.param({"run_id": "other", "status": "running"}, {"text": "Settings"}, id="another run"),
        pytest.param("run", {"text": "   "}, id="blank text"),
        pytest.param("run", {"text": 5}, id="not text"),
        pytest.param("run", {}, id="nothing named"),
    ],
)
async def test_locate_is_refused_unless_the_active_run_names_some_text(broker, judge, active, parameters):
    if active != "run":
        broker.runtime.active = active

    status, body = await broker.post(parameters=parameters)

    assert (status, body["code"]) == (409, "invalid_action")
    broker.driver.assert_not_awaited()
    assert judge.calls == []


@pytest.mark.parametrize(
    "judged",
    [
        pytest.param(None, id="no judgment"),
        pytest.param(RuntimeError("the judgment broke"), id="the judgment fails"),
    ],
)
async def test_a_judgment_that_finds_nothing_never_takes_the_phone_away(broker, judge, judged):
    judge.answers = judged

    status, body = await broker.post()

    if judged is None:
        assert (status, body["result"]) == (200, {"found": False, "reason": "not_connected"})
        broker.log.assert_called_once_with("element_not_located", run_id="run", code="not_connected", duration_ms=ANY)
    else:
        assert (status, body["success"]) == (503, False)
    assert broker.runtime.control.state == "agent"
    assert (await broker.post("observe", parameters={}, operation_id="two"))[0] == 200


async def test_a_run_keeps_whose_it_is_until_it_ends(monkeypatch, tmp_path):
    import services.status_broadcaster as status

    runtime = MobileRuntime()
    runtime.serial = "emulator-5560"
    monkeypatch.setattr(runtime_module, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(runtime_module, "event", Mock())
    monkeypatch.setattr(runtime, "driver_env", lambda: {})
    monkeypatch.setattr(runtime, "driver_call", AsyncMock(return_value={"width": 1080, "height": 1920}))
    monkeypatch.setattr(status, "get_status_broadcaster", lambda: SimpleNamespace(update_node_status=AsyncMock()))
    proc = FakeProcess()
    for line in (b'{"type":"activity","operation":"locate","state":"started"}\n',
                 b'{"type":"activity","operation":"locate","state":"completed","duration_ms":2100}\n',
                 b'{"type":"completed","result":"done"}\n'):
        proc.stdout.feed_data(line)
    proc.returncode = 0
    proc.stdout.feed_eof()
    proc.terminated.set()
    held = {}

    async def spawn(*_args, **_kwargs):
        held.update(runtime._run_attribution)
        held["snapshot"] = json.dumps(runtime.snapshot())
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with structlog.contextvars.bound_contextvars(chat_run_id="chat-1"):
        result = await runtime.run(**run_args())

    assert result["response"] == "done"
    run = held["run"]
    assert (run["principal"], run["workflow_id"], run["node_id"]) == ("owner", "workflow", "agent")
    assert (run["log"]["chat_run_id"], run["log"]["employee_job_id"]) == ("chat-1", None)
    assert "chat-1" not in held["snapshot"]  # never public
    assert runtime._run_attribution == {}
    messages = [item["message"] for item in runtime.last_task["activity"]]
    assert "Finding it on the screen" in messages
    assert "Finding it on the screen: completed (2.1s)" in messages


# The worker


@dataclass(frozen=True)
class Bounds:
    """minitap's Bounds (controllers/types.py); the server does not install minitap."""

    x1: int
    y1: int
    x2: int
    y2: int


@pytest.fixture
def controller(monkeypatch):
    """The engine's controller with the broker replaced: ``controller.answer``
    is what ``locate`` returns (an exception: raised), and ``controller.reads``
    what was asked."""
    from nodes.mobile.runtime.worker import Controller

    module = types.ModuleType("minitap.mobile_use.controllers.types")
    module.Bounds = Bounds
    monkeypatch.setitem(sys.modules, module.__name__, module)
    value = Controller({})
    value.reads = []
    value.answer = {"found": True, "bounds": {"x1": 10, "y1": 40, "x2": 30, "y2": 80}}

    def read_sync(operation, **params):
        value.reads.append((operation, params))
        if isinstance(value.answer, Exception):
            raise value.answer
        return value.answer

    value.read_sync = read_sync
    return value


def test_an_element_missing_from_the_tree_is_found_on_the_screen(controller):
    element, bounds, error = controller.find_element([{"text": "Other", "bounds": "[0,0][5,5]"}], text="Settings")

    assert controller.reads == [("locate", {"text": "Settings"})]
    assert element == {"text": "Settings", "bounds": "[10,40][30,80]", "located": True}
    assert (bounds, error) == (Bounds(10, 40, 30, 80), None)


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"found": False}, id="not on the screen"),
        pytest.param({"found": False, "reason": "not_connected"}, id="no judgment"),
        pytest.param(RuntimeError("Device command failed"), id="refused"),
        pytest.param({"found": True}, id="no bounds"),
        pytest.param({"found": True, "bounds": {"x1": 30, "y1": 40, "x2": 10, "y2": 80}}, id="edges out of order"),
        pytest.param({"found": True, "bounds": {"x1": 10.5, "y1": 40, "x2": 30, "y2": 80}}, id="not whole pixels"),
    ],
)
def test_anything_short_of_a_box_is_not_found_as_before(controller, answer):
    controller.answer = answer

    assert controller.find_element([], text="Settings") == (None, None, "Element not found")


@pytest.mark.parametrize(
    "ui, lookup",
    [
        pytest.param([], {"resource_id": "com.example:id/ok"}, id="by resource id alone"),
        pytest.param([], {"text": "Settings", "index": 1}, id="a later match"),
        pytest.param([{"text": "Settings", "bounds": "[0,0][10,10]"}], {"text": "Settings"}, id="in the tree"),
    ],
)
def test_only_a_text_lookup_that_matches_nothing_looks_at_the_screen(controller, ui, lookup):
    controller.find_element(ui, **lookup)

    assert controller.reads == []
