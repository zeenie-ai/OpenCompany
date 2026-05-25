"""
JSON-RPC 2.0 Protocol for Android Services Relay

Handles message encoding/decoding and request/response tracking.

Request format:  {"jsonrpc": "2.0", "method": "...", "params": {...}, "id": 1}
Response format: {"jsonrpc": "2.0", "result": {...}, "id": 1}
Event format:    {"jsonrpc": "2.0", "method": "...", "params": {...}}

Error codes:
- -32600: Invalid request
- -32601: Method not found
- -32602: Invalid params
- -32001: Not paired
- -32002: Pairing failed
- -32003: Relay error
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from enum import Enum


class RPCErrorCode(Enum):
    """JSON-RPC 2.0 error codes"""

    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    NOT_PAIRED = -32001
    PAIRING_FAILED = -32002
    RELAY_ERROR = -32003


@dataclass
class RPCRequest:
    """JSON-RPC 2.0 request"""

    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        msg = {
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
        }
        if self.id is not None:
            msg["id"] = self.id
        return msg


@dataclass
class RPCResponse:
    """JSON-RPC 2.0 response"""

    id: int
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RPCResponse":
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class RPCEvent:
    """JSON-RPC 2.0 server event (notification without id)"""

    method: str
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RPCEvent":
        return cls(
            method=data.get("method", ""),
            params=data.get("params", {}),
        )


class RPCRequestTracker:
    """Tracks pending JSON-RPC requests and matches responses"""

    def __init__(self):
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future] = {}

    def next_id(self) -> int:
        """Get next request ID"""
        self._request_id += 1
        return self._request_id

    def create_request(self, method: str, params: Dict[str, Any] = None) -> tuple[RPCRequest, asyncio.Future]:
        """Create request and future for response"""
        request_id = self.next_id()
        request = RPCRequest(method=method, params=params or {}, id=request_id)

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[request_id] = future

        return request, future

    def resolve(self, response: RPCResponse) -> bool:
        """Resolve pending request with response. Returns True if matched."""
        if response.id not in self._pending:
            return False

        future = self._pending.pop(response.id)

        if response.is_error:
            error_msg = response.error.get("message", "RPC Error")
            future.set_exception(RPCError(error_msg, response.error.get("code")))
        else:
            future.set_result(response.result)

        return True

    def cancel(self, request_id: int) -> bool:
        """Cancel pending request. Returns True if found."""
        if request_id in self._pending:
            future = self._pending.pop(request_id)
            future.cancel()
            return True
        return False

    def cancel_all(self):
        """Cancel all pending requests"""
        for future in self._pending.values():
            future.cancel()
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)


class RPCError(Exception):
    """JSON-RPC error"""

    def __init__(self, message: str, code: int = None):
        super().__init__(message)
        self.code = code


def parse_message(data: Dict[str, Any]) -> RPCResponse | RPCEvent:
    """Parse incoming message as response or event"""
    if "id" in data and data["id"] is not None:
        return RPCResponse.from_dict(data)
    else:
        return RPCEvent.from_dict(data)


def is_response(data: Dict[str, Any]) -> bool:
    """Check if message is a response (has id)"""
    return "id" in data and data["id"] is not None


def is_event(data: Dict[str, Any]) -> bool:
    """Check if message is an event (no id, has method)"""
    return ("id" not in data or data["id"] is None) and "method" in data
