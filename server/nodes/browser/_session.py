"""Who is driving each profile's Chrome: the agent, the user, or nobody.

One :class:`ProfileController` per running profile owns that profile's
Chrome, its egress proxy and the backend's CDP connection. Several Browser
nodes (and the Credentials "log in" flow) can use one profile, so control
lives on the controller, and a :class:`BrowserSession` is the stable identity
of one node's use of it: ``(owner, workflow, node)`` hashes to the same
``session_id`` on every run, which is what the live view addresses.

The control state machine::

    IDLE ──agent step──▶ AGENT ──step ends──▶ IDLE
    IDLE / AGENT ──take over──▶ USER            (a running step gets up to
    IDLE / AGENT ──request_user──▶ AWAITING_USER  10 s, then is interrupted)
    AWAITING_USER ──take over──▶ USER
    USER / AWAITING_USER ──hand back / decline / deadline──▶ IDLE
    USER ──no input for 10 min──▶ IDLE            (auto hand-back)

While the user has control, an agent step waits up to a minute for the hand
back and then tells the agent to call ``request_user``. A lease stops two
workflows using one profile at once: another workflow waits up to 30 s,
then gets an error naming the holder; the lease lapses two minutes after
the holder's last step.

Pending ``request_user`` calls live in memory: a backend restart kills the
profile's Chrome anyway, and the retried step simply asks again.

A detected challenge is a separate latch: automatic hand-back, a request
deadline, or a released workflow lease never admits the agent again. Only
the owner explicitly handing back their control clears it.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError, NodeWaitInterrupted

from services.netpolicy import NetPolicy

logger = get_logger(__name__)

_KEY_VERSION = "browser:v1"
LEASE_IDLE_SECONDS = 120.0
LEASE_WAIT_SECONDS = 30.0
USER_CONTROL_WAIT_SECONDS = 60.0
TAKE_OVER_GRACE_SECONDS = 10.0
USER_IDLE_HANDBACK_SECONDS = 600.0
VIEWER_DROP_GRACE_SECONDS = 30.0


class ControlState(str, Enum):
    IDLE = "idle"
    AGENT = "agent"
    AWAITING_USER = "awaiting_user"
    USER = "user"


@dataclass(frozen=True)
class SessionKey:
    owner_id: str
    workflow_id: str
    node_id: str

    @property
    def session_id(self) -> str:
        material = "\0".join((_KEY_VERSION, self.owner_id, self.workflow_id, self.node_id))
        return "brs_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass
class BrowserSession:
    """One node's (or one login dialog's) use of a profile."""

    key: SessionKey
    profile_id: str
    label: str = "Browser"
    policy: NetPolicy = field(default_factory=NetPolicy)
    kind: str = "node"  # node | profile_login

    @property
    def session_id(self) -> str:
        return self.key.session_id

    @property
    def workflow_id(self) -> str:
        return self.key.workflow_id

    @property
    def node_id(self) -> str:
        return self.key.node_id


@dataclass
class UserRequest:
    session_id: str
    reason: str
    message: str
    created_at: float
    deadline: float
    future: asyncio.Future

    def to_wire(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "message": self.message,
            "since": self.created_at,
            "deadline": self.deadline,
        }


Listener = Callable[[str, Dict[str, Any]], Awaitable[None]]


def worker_shutting_down() -> bool:
    try:
        from temporalio import activity

        return activity.in_activity() and activity.is_worker_shutdown()
    except Exception:  # noqa: BLE001
        return False


class ProfileController:
    """Control over one profile's Chrome."""

    def __init__(self, profile_id: str, profile_name: str) -> None:
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.state = ControlState.IDLE
        self.revision = 0
        self.lease_session: Optional[BrowserSession] = None
        self.lease_renewed = 0.0
        self.last_activity = time.monotonic()
        self.controller_viewer: Optional[str] = None
        self.user_input_deadline = 0.0
        self.pending: Optional[UserRequest] = None
        self.challenge_required = False
        self.needs_observation = False
        self.task_id: Optional[str] = None
        self.sensitive_login = False
        self.recovered_assistance: Optional[dict] = None
        self._challenge_message = ""
        self.viewers: set[str] = set()
        self.active_target_id: Optional[str] = None
        self.tabs: Dict[str, Dict[str, Any]] = {}
        #: target_id -> snapshot ref ("e12") -> backendDOMNodeId, from the
        #: last snapshot of that tab. Refs never leave the backend.
        self.refs: Dict[str, Dict[str, int]] = {}
        self._changed = asyncio.Condition()
        self._op_lock = asyncio.Lock()
        self._interrupt_op: Optional[Callable[[], Awaitable[None]]] = None
        self._user_claim = False
        self._listeners: List[Listener] = []
        self._viewer_drop_tasks: Dict[str, asyncio.Task] = {}
        self._fallback_policy = NetPolicy()

    # -- observation ----------------------------------------------------------

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def emit(self, kind: str, payload: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                await listener(kind, payload)
            except Exception:  # noqa: BLE001 - one bad viewer must not break control
                logger.debug("[browser] controller listener failed", exc_info=True)

    def current_session(self) -> Optional[BrowserSession]:
        if self.pending is not None and self.lease_session is not None:
            return self.lease_session
        return self.lease_session

    def current_policy(self) -> NetPolicy:
        """The network policy of whoever holds the profile (read by the proxy thread)."""
        session = self.lease_session
        return session.policy if session is not None else self._fallback_policy

    def set_fallback_policy(self, policy: NetPolicy) -> None:
        self._fallback_policy = policy

    def snapshot(self, viewer_id: Optional[str] = None) -> Dict[str, Any]:
        if self.controller_viewer is None:
            controller = None
        else:
            controller = "you" if viewer_id and viewer_id == self.controller_viewer else "other"
        return {
            "state": self.state.value,
            "controller": controller,
            "profile": {"id": self.profile_id, "name": self.profile_name},
            "request": self.pending.to_wire() if self.pending else None,
            "challenge_required": self.challenge_required,
            "sensitive_login": self.sensitive_login,
            "revision": self.revision,
        }

    async def _set_state(self, state: ControlState, *, reason: str = "") -> None:
        previous = self.state
        self.state = state
        self.revision += 1
        async with self._changed:
            self._changed.notify_all()
        await self.emit("state", {"state": state.value, "previous": previous.value, "reason": reason})
        await self._broadcast()

    async def _broadcast(self) -> None:
        from services.browser_owners import persist_control
        from services.plugin.deps import get_database
        assistance = None
        if self.pending:
            assistance = {"reason": self.pending.reason, "message": self.pending.message,
                          "deadline": time.time() + max(0, self.pending.deadline - time.monotonic())}
        await persist_control(get_database(), self.profile_id, self.task_id, challenge=self.challenge_required,
                              needs_observation=self.needs_observation, assistance=assistance)
        session = self.lease_session
        if session is None:
            return
        from ._events import dispatch_browser_updated

        try:
            await dispatch_browser_updated(
                workflow_id=session.workflow_id,
                node_id=session.node_id,
                session_id=session.session_id,
                state=self.state.value,
                revision=self.revision,
            )
        except Exception:  # noqa: BLE001 - UI notification is best effort
            logger.debug("[browser] browser_updated broadcast failed", exc_info=True)
        if session.kind == "node":
            try:
                from services.employees.node_signals import node_state_changed

                node_state_changed("browser", session.workflow_id)
            except Exception:  # noqa: BLE001
                pass

    async def _wait_for(self, predicate: Callable[[], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        async with self._changed:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=min(remaining, 5.0))
                except asyncio.TimeoutError:
                    pass
                if worker_shutting_down():
                    raise NodeWaitInterrupted("worker shutting down while the browser is busy")
        return True

    # -- lease ----------------------------------------------------------------

    def _lease_free_for(self, session: BrowserSession) -> bool:
        holder = self.lease_session
        if holder is None or holder.session_id == session.session_id:
            return True
        if self.task_id is not None:
            return False
        if self.state == ControlState.IDLE and self.pending is None and time.monotonic() - self.lease_renewed > LEASE_IDLE_SECONDS:
            return True
        return False

    def claim_task(self, task_id: str) -> None:
        """Hold a profile through reasoning and assistance, not only input."""
        if self.task_id is not None and self.task_id != task_id:
            raise NodeUserError("BrowserBusy: this profile is already assigned to another task.")
        self.task_id = task_id

    async def release_task(self, task_id: str) -> bool:
        if self.task_id != task_id:
            return False
        await self.release_lease()
        self.task_id = None
        return True

    async def set_sensitive_login(self, enabled: bool) -> None:
        self.sensitive_login = enabled
        if enabled:
            self.tabs = {key: {"target_id": key} for key in self.tabs}
        self.revision += 1
        await self.emit("sensitive", {"enabled": enabled})
        await self._broadcast()

    async def restore_assistance(self, session: BrowserSession) -> None:
        saved, self.recovered_assistance = self.recovered_assistance, None
        if saved is None or self.pending is not None:
            return
        remaining = float(saved.get("deadline") or 0) - time.time()
        if remaining > 0 or self.challenge_required:
            await self.acquire_lease(session)
            await self._ensure_user_request(session, reason=str(saved.get("reason") or "other"),
                                            message=str(saved.get("message") or "Please finish this step."),
                                            timeout=max(60 if self.challenge_required else 0, remaining))

    async def acquire_lease(self, session: BrowserSession, *, wait: float = LEASE_WAIT_SECONDS) -> None:
        if not self._lease_free_for(session):
            holder = self.lease_session
            ok = await self._wait_for(lambda: self._lease_free_for(session), wait)
            if not ok:
                label = holder.label if holder else "another workflow"
                raise NodeUserError(
                    f"The browser profile {self.profile_name!r} is in use by {label}. Wait for it to finish, or give this "
                    "Browser node a different profile."
                )
        if self.lease_session is None or self.lease_session.session_id != session.session_id:
            self.lease_session = session
        else:
            # Keep the latest policy (the node's settings may have changed).
            self.lease_session = session
        self.lease_renewed = time.monotonic()
        if self.challenge_required and self.pending is not None and self.pending.session_id != session.session_id:
            # A workflow may release its lease while Chrome still displays
            # the challenge. The next profile user inherits the pause.
            self.pending.session_id = session.session_id
            await self._broadcast()

    async def release_lease(self, session_id: Optional[str] = None) -> None:
        if session_id is None or (self.lease_session and self.lease_session.session_id == session_id):
            barrier = getattr(self, "live_control_barrier", None)
            if barrier is not None:
                await barrier(self.controller_viewer)
            if self.pending is not None:
                self._resolve_pending("cancelled", "")
            self.lease_session = None
            if self.state != ControlState.IDLE:
                self._cancel_viewer_drop(self.controller_viewer)
                self.controller_viewer = None
                state = ControlState.AWAITING_USER if self.challenge_required else ControlState.IDLE
                await self._set_state(state, reason="released")
            async with self._changed:
                self._changed.notify_all()

    # -- agent side -----------------------------------------------------------

    @asynccontextmanager
    async def agent_op(
        self, session: BrowserSession, *, interrupt: Optional[Callable[[], Awaitable[None]]] = None
    ) -> AsyncIterator[None]:
        """Run one agent step with the profile leased and control held."""
        await self.acquire_lease(session)
        if self.challenge_required:
            raise NodeUserError("This browser is paused for a challenge. The owner must take control, finish it, and explicitly hand it back.")
        ok = await self._wait_for(lambda: not self._user_blocks_agent(), USER_CONTROL_WAIT_SECONDS)
        if not ok:
            raise NodeUserError("The owner is controlling this browser. Call request_user to wait for them to hand it back.")
        async with self._op_lock:
            if self.challenge_required:
                raise NodeUserError("This browser is paused for a challenge. The owner must take control, finish it, and explicitly hand it back.")
            if self._user_blocks_agent():
                raise NodeUserError("The owner took control of this browser. Call request_user to wait for them.")
            self._interrupt_op = interrupt
            await self._set_state(ControlState.AGENT, reason="agent step")
            try:
                yield
            finally:
                self._interrupt_op = None
                self.lease_renewed = self.last_activity = time.monotonic()
                if self.state == ControlState.AGENT:
                    await self._set_state(ControlState.IDLE, reason="agent step finished")

    async def pause_for_challenge(self, session: BrowserSession, *, message: str, timeout: float) -> None:
        """Latch a challenge without acquiring the agent operation lock.

        Safe to call while ``agent_op`` holds that lock. The pending request
        supplies the existing workspace help UI even after its wait expires.
        """
        await self.acquire_lease(session)
        self.challenge_required = True
        self._challenge_message = message.strip()[:500] or "Please finish the challenge in the browser, then hand control back."
        # A previous ordinary request must not hide the challenge's reason.
        if self.pending is not None and self.pending.reason != "captcha":
            self._resolve_pending("superseded", "The browser needs help with a challenge.")
        await self._ensure_user_request(session, reason="captcha", message=self._challenge_message, timeout=timeout)

    async def _ensure_user_request(self, session: BrowserSession, *, reason: str, message: str, timeout: float) -> UserRequest:
        if self.challenge_required:
            reason, message = "captcha", self._challenge_message
        request = self.pending
        if request is None or request.session_id != session.session_id or request.future.done():
            now = time.monotonic()
            request = UserRequest(
                session_id=session.session_id,
                reason=reason,
                message=message.strip()[:500],
                created_at=time.time(),
                deadline=now + timeout,
                future=asyncio.get_running_loop().create_future(),
            )
            self.pending = request
            if self.state != ControlState.USER:
                await self._set_state(ControlState.AWAITING_USER, reason="agent asked for help")
            else:
                await self._broadcast()
            await self.emit("request", {"request": request.to_wire()})
        return request

    async def request_user(
        self, session: BrowserSession, *, reason: str, message: str, timeout: float, wait: float
    ) -> Dict[str, Any]:
        """Ask the owner to take over. Returns ``{"status", "note"}``.

        Waits at most ``wait`` seconds (the tool-call budget); when that runs
        out first the request stays open and ``status`` is ``still_waiting``,
        so the agent can call again and pick the same request back up.
        """
        await self.acquire_lease(session)
        request = await self._ensure_user_request(session, reason=reason, message=message, timeout=timeout)
        remaining = max(0.0, min(wait, request.deadline - time.monotonic()))
        end = time.monotonic() + remaining
        while not request.future.done():
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                await asyncio.wait_for(asyncio.shield(request.future), timeout=min(left, 5.0))
            except asyncio.TimeoutError:
                pass
            if worker_shutting_down():
                raise NodeWaitInterrupted("worker shutting down while waiting for the owner")
        if request.future.done():
            return request.future.result()
        if time.monotonic() >= request.deadline:
            if self.state in (ControlState.AWAITING_USER, ControlState.USER):
                await self.hand_back(self.controller_viewer, outcome="timeout", note="request timed out")
            else:
                self._resolve_pending("timeout", "")
            return {"status": "timeout", "note": ""}
        return {"status": "still_waiting", "note": ""}

    def _resolve_pending(self, status: str, note: str) -> None:
        request = self.pending
        # Keep the message visible until explicit owner resume, even though
        # this particular tool invocation has finished waiting.
        if not self.challenge_required:
            self.pending = None
        if request is not None and not request.future.done():
            request.future.set_result({"status": status, "note": note.strip()[:1000]})

    # -- user side --------------------------------------------------------------

    def _user_blocks_agent(self) -> bool:
        return self.challenge_required or self._user_claim or self.state in (ControlState.USER, ControlState.AWAITING_USER)

    async def take_over(self, viewer_id: str, *, force: bool = False, valid: Optional[Callable[[], bool]] = None) -> tuple[bool, str]:
        if self.sensitive_login:
            return False, "protected_login"
        if valid is not None and not valid():
            return False, "viewer_unavailable"
        if self.controller_viewer and self.controller_viewer != viewer_id and not force:
            return False, "held_by_other"
        if self.lease_session is None:
            return False, "no_session"
        if self.controller_viewer and self.controller_viewer != viewer_id:
            barrier = getattr(self, "live_control_barrier", None)
            if barrier is not None:
                await barrier(self.controller_viewer)
        # Claim first so no new agent step starts while we wait for this one.
        self._user_claim = True
        try:
            if self.state == ControlState.AGENT:
                finished = await self._wait_for(lambda: self.state != ControlState.AGENT, TAKE_OVER_GRACE_SECONDS)
                if not finished and self._interrupt_op is not None:
                    try:
                        await self._interrupt_op()
                    except Exception:  # noqa: BLE001
                        logger.debug("[browser] interrupting the agent step failed", exc_info=True)
            try:
                await asyncio.wait_for(self._op_lock.acquire(), timeout=TAKE_OVER_GRACE_SECONDS)
            except asyncio.TimeoutError:
                return False, "agent_busy"
            try:
                if valid is not None and not valid():
                    return False, "viewer_unavailable"
                previous_controller = self.controller_viewer
                self.controller_viewer = viewer_id
                self.user_input_deadline = time.monotonic() + USER_IDLE_HANDBACK_SECONDS
                self.last_activity = time.monotonic()
                await self._set_state(ControlState.USER, reason="owner took control")
            finally:
                self._op_lock.release()
        finally:
            self._user_claim = False
            async with self._changed:
                self._changed.notify_all()
        if previous_controller and previous_controller != viewer_id:
            await self.emit("control_moved", {"from": previous_controller, "to": viewer_id})
        return True, "granted"

    async def hand_back(self, viewer_id: Optional[str], *, outcome: str = "handed_back", note: str = "") -> bool:
        if self.state not in (ControlState.USER, ControlState.AWAITING_USER):
            return False
        if self.state == ControlState.USER and viewer_id is not None and viewer_id != self.controller_viewer:
            return False
        barrier = getattr(self, "live_control_barrier", None)
        if barrier is not None:
            await barrier(self.controller_viewer)
        # Another transition may have completed while the dispatched command
        # settled. Never release a newer viewer's control.
        if self.state == ControlState.USER and viewer_id is not None and viewer_id != self.controller_viewer:
            return False
        if self.challenge_required and outcome == "handed_back":
            # Only the controlling owner's explicit hand-back resumes a
            # challenge. Automatic release paths use a distinct outcome.
            if self.state == ControlState.USER and viewer_id is not None and viewer_id == self.controller_viewer:
                self.challenge_required = False
                self._challenge_message = ""
            else:
                outcome = "control_lost"
        self._resolve_pending(outcome, note)
        self._cancel_viewer_drop(self.controller_viewer)
        self.controller_viewer = None
        self.lease_renewed = self.last_activity = time.monotonic()
        state = ControlState.AWAITING_USER if self.challenge_required else ControlState.IDLE
        await self._set_state(state, reason=outcome)
        return True

    def can_inject_input(self, viewer_id: str) -> bool:
        return not self.sensitive_login and self.state == ControlState.USER and self.controller_viewer == viewer_id

    def touch_user_input(self) -> None:
        now = time.monotonic()
        self.user_input_deadline = now + USER_IDLE_HANDBACK_SECONDS
        self.last_activity = now

    def viewer_attached(self, viewer_id: str) -> None:
        self.viewers.add(viewer_id)
        self.last_activity = time.monotonic()
        task = self._viewer_drop_tasks.pop(viewer_id, None)
        if task is not None:
            task.cancel()

    def viewer_detached(self, viewer_id: str) -> None:
        self.viewers.discard(viewer_id)
        self.last_activity = time.monotonic()
        if viewer_id == self.controller_viewer:

            async def _drop() -> None:
                await asyncio.sleep(VIEWER_DROP_GRACE_SECONDS)
                if self.controller_viewer == viewer_id and viewer_id not in self.viewers:
                    await self.hand_back(viewer_id, outcome="viewer_disconnected", note="the owner closed the live view")

            self._viewer_drop_tasks[viewer_id] = asyncio.create_task(_drop())

    def _cancel_viewer_drop(self, viewer_id: Optional[str]) -> None:
        task = self._viewer_drop_tasks.pop(viewer_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def tick(self) -> None:
        """Periodic housekeeping (the fleet reaper calls this)."""
        now = time.monotonic()
        if self.state == ControlState.USER and now > self.user_input_deadline:
            await self.hand_back(self.controller_viewer, outcome="idle_timeout", note="auto: no input for 10 minutes")
        request = self.pending
        if request is not None and now >= request.deadline and not request.future.done():
            if self.state in (ControlState.AWAITING_USER, ControlState.USER):
                await self.hand_back(self.controller_viewer, outcome="timeout", note="request timed out")
            else:
                self._resolve_pending("timeout", "")

    def idle_for(self) -> float:
        busy = self.state != ControlState.IDLE or self.viewers or self.pending is not None or self.task_id is not None or self.sensitive_login
        return 0.0 if busy else time.monotonic() - self.last_activity


__all__ = [
    "BrowserSession",
    "ControlState",
    "LEASE_IDLE_SECONDS",
    "ProfileController",
    "SessionKey",
    "UserRequest",
    "worker_shutting_down",
]
