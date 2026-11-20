"""Thread-safe admission fencing for one logical agent run.

Revocation stops new requests, not operations that have already been admitted.
The serializable state is carried by the durable run owner across retries.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Mapping
from weakref import WeakValueDictionary

INVALID_CODES = frozenset({"invalid_tool_input", "invalid_arguments", "invalid_target", "invalid_transition"})


def is_invalid_request(value: Any) -> bool:
    if isinstance(value, Mapping):
        return str(value.get("error_type") or value.get("error_code") or value.get("code") or "") in INVALID_CODES
    return str(value) in INVALID_CODES


class AdmissionHalted(RuntimeError):
    code = "agent_turn_halted"


class AdmissionGate:
    def __init__(self, state: Mapping[str, Any] | None = None, *, max_invalid: int = 3):
        state = state or {}
        self._lock = RLock()
        self.max_invalid = int(state.get("max_invalid", max_invalid))
        self.invalid_count = int(state.get("invalid_count", 0))
        self.admitted = int(state.get("admitted", 0))
        self.reason = str(state.get("reason") or "")
        self._invalid_ids = set(state.get("invalid_ids") or [])
        self.on_halt = None
        if self.invalid_count >= self.max_invalid and not self.reason:
            self.reason = "invalid_request_limit"

    @property
    def halted(self) -> bool:
        with self._lock:
            return bool(self.reason)

    def check(self) -> None:
        with self._lock:
            if self.reason:
                raise AdmissionHalted(self.reason)

    def admit(self, kind: str = "request") -> int:
        with self._lock:
            self.check()
            self.admitted += 1
            return self.admitted

    def record_invalid(self, error_code: str, request_id: str | None = None) -> bool:
        with self._lock:
            if not is_invalid_request(error_code):
                return bool(self.reason)
            if request_id and request_id in self._invalid_ids:
                return bool(self.reason)
            if request_id:
                self._invalid_ids.add(request_id)
            self.invalid_count += 1
            if self.invalid_count >= self.max_invalid:
                first_halt = not self.reason
                self.reason = self.reason or "invalid_request_limit"
                if first_halt and self.on_halt is not None:
                    self.on_halt(self.reason)
            return bool(self.reason)

    def revoke(self, reason: str = "interrupted") -> None:
        with self._lock:
            first_halt = not self.reason
            self.reason = self.reason or reason
            if first_halt and self.on_halt is not None:
                self.on_halt(self.reason)

    def serialize_state(self) -> dict[str, Any]:
        with self._lock:
            return {"invalid_count": self.invalid_count, "max_invalid": self.max_invalid,
                    "admitted": self.admitted, "reason": self.reason, "invalid_ids": sorted(self._invalid_ids)}

    def restore(self, state: Mapping[str, Any]) -> None:
        """Merge durable admission state without reopening a revoked gate."""
        with self._lock:
            self.invalid_count = max(self.invalid_count, int(state.get("invalid_count", 0)))
            self._invalid_ids.update(state.get("invalid_ids") or [])
            reason = str(state.get("reason") or "")
            if reason or self.invalid_count >= self.max_invalid:
                self.revoke(reason or "invalid_request_limit")


_gates: WeakValueDictionary[str, AdmissionGate] = WeakValueDictionary()
_registry_lock = RLock()


def get_gate(context: Mapping[str, Any] | None, *, create: bool = False) -> AdmissionGate | None:
    context = context or {}
    runtime = context.get("agent_runtime") or context
    identifier = str(runtime.get("agent_run_id") or context.get("agent_run_id") or "")
    if not identifier:
        return None
    with _registry_lock:
        gate = _gates.get(identifier)
        if gate is None and create:
            gate = AdmissionGate(runtime.get("agent_admission_state") or context.get("agent_admission_state"))
            _gates[identifier] = gate
        return gate


async def restore_gate(context: Mapping[str, Any] | None, database: Any = None) -> AdmissionGate | None:
    """Read the durable budget before any opaque provider request is admitted."""
    context = context or {}
    runtime = context.get("agent_runtime") or context
    instance_id = runtime.get("agent_instance_id") or context.get("agent_instance_id")
    run_id = runtime.get("agent_run_id") or context.get("agent_run_id")
    if not instance_id or not run_id:
        return None
    from services.agent_instances.service import AgentInstanceService
    if database is None:
        from services.plugin.deps import get_database
        database = get_database()
    state = await AgentInstanceService(database).admission_state(str(instance_id), str(run_id))
    gate = get_gate(context, create=True)
    gate.restore(state)
    gate.check()
    return gate
