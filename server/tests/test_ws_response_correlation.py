"""Business request identities must never replace WebSocket correlation IDs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from routers.websocket import _execute_handler


class Socket:
    def __init__(self):
        self.client_state = SimpleNamespace(name="CONNECTED")
        self.send_json = AsyncMock()


@pytest.mark.parametrize(
    ("operation", "success"),
    [("hire_employee", True), ("hire_employee", False), ("resolve_employee_delivery", True)],
)
async def test_business_request_id_cannot_orphan_the_pending_client_request(operation, success):
    result = {"success": success, "request_id": "durable-operation", "state": "saved"}
    original = dict(result)
    handler = AsyncMock(return_value=result)
    socket = Socket()

    await _execute_handler(handler, {"request_id": "socket-request"}, socket, operation, "socket-request")

    frame = socket.send_json.call_args.args[0]
    assert frame["request_id"] == "socket-request"
    assert frame["operation_request_id"] == "durable-operation"
    assert frame["type"] == f"{operation}_result"
    assert frame["success"] is success and frame["state"] == "saved"
    assert result == original


async def test_handler_specific_response_types_remain_compatible():
    socket = Socket()
    await _execute_handler(AsyncMock(return_value={"success": True, "type": "status"}), {}, socket, "hire_employee", "wire")
    assert socket.send_json.call_args.args[0]["type"] == "status"


@pytest.mark.parametrize("returned_id", [None, "socket-request"])
async def test_ordinary_responses_keep_their_existing_shape(returned_id):
    result = {"success": True, "employee": {"workflow_id": "1"}}
    if returned_id:
        result["request_id"] = returned_id
    socket = Socket()
    await _execute_handler(AsyncMock(return_value=result), {}, socket, "hire_employee", "socket-request")
    assert socket.send_json.call_args.args[0] == {**result, "type": "hire_employee_result", "request_id": "socket-request"}


async def test_uncorrelated_handler_results_are_unchanged():
    result = {"success": True, "request_id": "business-only", "type": "progress"}
    socket = Socket()
    await _execute_handler(AsyncMock(return_value=result), {}, socket, "progress", None)
    socket.send_json.assert_awaited_once_with(result)
