"""Versioned, deterministic Pi bridge contracts, shared by activities/workflows."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

from temporalio.exceptions import ApplicationError

PROTOCOL_VERSION = 1
SDK_VERSION = "1.1.0"
DRIVER_QUEUE = "coding-pi-driver"
INLINE_LIMIT = 256 * 1024
FRAME_LIMIT = 16 * 1024 * 1024


def canonical_json(value: Any) -> str:
    # Match JSON.stringify's finite-number spelling (1.0, -0.0 and exponents
    # otherwise hash differently across Python and JavaScript).
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(value[k])
            for k in sorted(value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, float):
        if not math.isfinite(value):
            # A tool result can carry NaN or Infinity (payload JSON round-trips
            # them): bad data ends the turn, it is no bug in workflow code.
            raise conflict("Non-finite values are not valid Pi protocol JSON")
        if value == 0:
            return "0"
        absolute = abs(value)
        if 1e-6 <= absolute < 1e21:
            return format(Decimal(repr(value)), "f").rstrip("0").rstrip(".") if "." in format(Decimal(repr(value)), "f") else format(Decimal(repr(value)), "f")
        mantissa, exponent = repr(value).lower().split("e")
        return mantissa.removesuffix(".0") + "e" + ("+" if int(exponent) >= 0 else "-") + str(abs(int(exponent)))
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def conflict(message: str) -> ApplicationError:
    return ApplicationError(message, type="PiProtocolConflict", non_retryable=True)


def tool_operation_id(turn: int, index: int, call: dict) -> str:
    return f"turn:{turn}:tool:{index}:{call.get('id') or ''}"


def semantic_fingerprint(kind: str, payload: dict) -> str:
    # Attempt epochs are fences, never part of the logical operation identity.
    return canonical_digest({"kind": kind, "payload": {k: v for k, v in payload.items()
        if k != "epoch"}})


class OperationLedger:
    """History-resident receipts. Only matching retries can attach to an effect."""

    def __init__(self) -> None:
        self.epoch = 0
        self.turn = -1
        self.operations: dict[str, dict] = {}
        self.expected: dict[str, dict] = {}
        self.parked = False

    def begin(self, turn: int, expected: dict[str, dict]) -> None:
        self.turn = turn
        self.expected = expected
        self.operations = {}
        self.parked = False

    def register(self, turn: int) -> int:
        if turn != self.turn:
            raise conflict("Pi driver belongs to a different turn")
        self.epoch += 1
        return self.epoch

    def submit(self, request: dict) -> dict:
        if not isinstance(request, dict) or not isinstance(request.get("id"), str) or not isinstance(request.get("payload"), dict):
            raise conflict("Pi operation must contain a typed identity and payload")
        identity = request.get("id")
        expected = self.expected.get(identity)
        if not expected or request.get("kind") != expected["kind"]:
            raise conflict("Pi operation does not match an admitted model/tool position")
        payload = request.get("payload") or {}
        if not isinstance(payload.get("epoch"), int) or isinstance(payload.get("epoch"), bool):
            raise conflict("Pi driver epoch must be an integer")
        if "receipt_only" in payload and not isinstance(payload["receipt_only"], bool):
            raise conflict("Pi receipt verdict must be a boolean")
        for key, value in expected["payload"].items():
            if payload.get(key) != value:
                raise conflict(f"Pi operation {key} does not match its recorded scope")
        fingerprint = semantic_fingerprint(request["kind"], payload)
        # Do not trust a JavaScript-supplied digest: calculate both sides.
        prior = self.operations.get(identity)
        if prior and prior["fingerprint"] is not None and prior["fingerprint"] != fingerprint:
            raise conflict("Pi operation identity was reused with different content")
        if payload["epoch"] != self.epoch:
            raise conflict("Pi driver epoch has been fenced")
        if not prior:
            self.operations[identity] = {"fingerprint": fingerprint, "status": "pending",
                "receipt_only": bool(payload.get("receipt_only"))}
        elif prior["fingerprint"] is None:
            prior["fingerprint"] = fingerprint
        return {"id": identity, "status": self.operations[identity]["status"], "epoch": self.epoch}

    def complete(self, identity: str, result: Any) -> None:
        if not isinstance(identity, str) or identity not in self.expected:
            raise conflict("Pi receipt does not match an admitted operation")
        prior = self.operations.setdefault(identity, {"fingerprint": None, "status": "pending"})
        if prior.get("status") == "completed":
            if canonical_digest(prior["result"]) != canonical_digest(result):
                raise conflict("A completed Pi receipt cannot be overwritten")
            return
        prior.update(status="completed", result=result)

    def status(self, identity: str, epoch: int) -> dict:
        if epoch != self.epoch or self.parked:
            return {"status": "park", "epoch": self.epoch}
        item = self.operations.get(identity)
        if not item or item["status"] != "completed":
            return {"status": "pending", "epoch": self.epoch}
        return {"status": "completed", "result": item["result"], "epoch": self.epoch}
