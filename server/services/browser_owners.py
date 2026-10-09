"""Durable profile ownership. A missing owner is never replaced automatically.

The shared application database holds routing and fencing metadata only.
Browser sockets, daemon state and process IDs remain on the owning machine.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from services.plugin.base import NodeUserError

RUNTIME_EPOCH = uuid.uuid4().hex
_HEARTBEATS: dict[int, asyncio.Task] = {}
_PROCESS_LOCK: Any = None


from models.browser_owners import BrowserOwner, BrowserProfileOwner, BrowserTransientRoute as BrowserTransientRoute


def settings() -> Any:
    from core.container import container
    return container.settings()


def replica_id() -> str:
    cfg = settings()
    if getattr(cfg, "distributed_mode", False) is not True:
        return "local"
    value = str(getattr(cfg, "browser_replica_id", "") or "")
    if getattr(cfg, "distributed_mode", False) is True and not value:
        raise NodeUserError("Distributed browser execution requires BROWSER_REPLICA_ID.")
    return value or "local"


def owner_queue(owner_id: str) -> str:
    return "browser-owner-" + hashlib.sha256(owner_id.encode()).hexdigest()[:24]


def machine_id() -> str:
    import socket
    return str(getattr(settings(), "browser_machine_id", "") or socket.gethostname())


def _persistent(database: Any) -> bool:
    from sqlalchemy.ext.asyncio import AsyncEngine
    return isinstance(getattr(database, "engine", None), AsyncEngine)


def _binding(profile: BrowserProfileOwner, owner: BrowserOwner) -> dict:
    return {"profile_id": profile.profile_id, "principal_id": profile.principal_id,
            "owner_id": owner.owner_id, "runtime_epoch": owner.runtime_epoch,
            "task_queue": owner_queue(owner.owner_id), "base_url": owner.base_url,
            "profile_task_id": profile.task_id,
            "sensitive_login": profile.sensitive_login, "needs_observation": profile.needs_observation,
            "challenge_required": profile.challenge_required,
            "assistance": {"reason": profile.assistance_reason, "message": profile.assistance_message, "deadline": profile.assistance_deadline} if profile.assistance_reason else None,
            "available": owner.heartbeat_at > time.time() - 35}


async def register_browser_owner(database: Any) -> None:
    """Register this runtime and keep its fencing lease alive.

    A second process using a live replica ID is rejected. A recovered process
    may advance its own epoch only after the previous heartbeat has expired.
    """
    if getattr(settings(), "distributed_mode", False) is not True:
        return
    global _PROCESS_LOCK
    identity = replica_id()
    base = str(getattr(settings(), "browser_replica_url", "") or "").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NodeUserError("BROWSER_REPLICA_URL must be a trusted backend http(s) URL.")
    if _PROCESS_LOCK is None:
        from nodes.browser._chrome import ProfileLock
        from nodes.browser._profiles import profiles_root
        lock_dir = profiles_root().parent / "owners"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock = ProfileLock(lock_dir / (hashlib.sha256(identity.encode()).hexdigest() + ".lock"))
        if not lock.acquire():
            raise NodeUserError("The browser owner process is still running. Wait for its recovery; it cannot be replaced.")
        _PROCESS_LOCK = lock
    async with database.get_session() as session:
        row = await session.get(BrowserOwner, identity, with_for_update=True)
        now = time.time()
        if row is not None and row.machine_id != machine_id():
            raise NodeUserError("Browser owner recovery is waiting for its original machine. A different machine requires explicit operator fencing; automatic profile transfer is disabled.")
        if row is not None and row.runtime_epoch != RUNTIME_EPOCH and row.heartbeat_at > now - 35:
            raise NodeUserError("Another live browser runtime uses BROWSER_REPLICA_ID; choose a unique replica ID.")
        if row is None:
            row = BrowserOwner(owner_id=identity, runtime_epoch=RUNTIME_EPOCH, machine_id=machine_id(), base_url=base, heartbeat_at=now)
        elif row.runtime_epoch != RUNTIME_EPOCH:
            await session.execute(update(BrowserProfileOwner).where(BrowserProfileOwner.owner_id == identity).values(needs_observation=True))
        row.runtime_epoch, row.base_url, row.heartbeat_at = RUNTIME_EPOCH, base, now
        session.add(row)
        await session.commit()
    key = id(database)
    if key not in _HEARTBEATS or _HEARTBEATS[key].done():
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10)
                async with database.get_session() as session:
                    result = await session.execute(update(BrowserOwner).where(
                        BrowserOwner.owner_id == identity, BrowserOwner.runtime_epoch == RUNTIME_EPOCH
                    ).values(heartbeat_at=time.time()))
                    await session.commit()
                    if result.rowcount != 1:
                        return
        _HEARTBEATS[key] = asyncio.create_task(heartbeat(), name="browser-owner-heartbeat")


async def unregister_browser_owner() -> None:
    """Called only after every owned Chrome has stopped."""
    global _PROCESS_LOCK
    tasks = list(_HEARTBEATS.values())
    _HEARTBEATS.clear()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if _PROCESS_LOCK is not None:
        _PROCESS_LOCK.release()
        _PROCESS_LOCK = None


async def bind_profile(database: Any, profile_id: str, principal: str) -> dict:
    if getattr(settings(), "distributed_mode", False) is not True:
        result = {"profile_id": profile_id, "principal_id": principal, "owner_id": "local", "runtime_epoch": RUNTIME_EPOCH,
                  "task_queue": None, "base_url": "", "available": True, "sensitive_login": False, "needs_observation": False}
        if _persistent(database):
            async with database.get_session() as session:
                row = await session.get(BrowserProfileOwner, profile_id)
                if row is None:
                    row = BrowserProfileOwner(profile_id=profile_id, principal_id=principal, owner_id="local", runtime_epoch=RUNTIME_EPOCH)
                    session.add(row)
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                        row = await session.get(BrowserProfileOwner, profile_id)
                        if row is None:
                            raise
                elif row.runtime_epoch != RUNTIME_EPOCH:
                    row.runtime_epoch = RUNTIME_EPOCH
                    row.needs_observation = True
                    await session.commit()
                if row.principal_id != principal:
                    raise NodeUserError("Browser profile access denied.")
                result.update(sensitive_login=row.sensitive_login, needs_observation=row.needs_observation,
                              profile_task_id=row.task_id,
                              challenge_required=row.challenge_required,
                              assistance={"reason": row.assistance_reason, "message": row.assistance_message, "deadline": row.assistance_deadline} if row.assistance_reason else None)
        return result
    async with database.get_session() as session:
        row = await session.get(BrowserProfileOwner, profile_id)
        if row is not None:
            if row.principal_id != principal:
                raise NodeUserError("Browser profile access denied.")
            owner = await session.get(BrowserOwner, row.owner_id)
            if owner is None:
                raise NodeUserError("Browser unavailable — waiting for its owner.")
            return _binding(row, owner)
    try:
        async with database.get_session() as session:
            # Preparing metadata may run in an orchestration worker. Only
            # backend lifecycle owns runtime registration and its process lock.
            # Retain an unavailable registered owner rather than transferring.
            configured_owner = replica_id()
            if await session.get(BrowserOwner, configured_owner) is None:
                raise NodeUserError("Browser owner is not registered. Start its backend before submitting browser tasks.")
            row = BrowserProfileOwner(profile_id=profile_id, principal_id=principal, owner_id=configured_owner)
            session.add(row)
            await session.commit()
    except IntegrityError:
        pass  # The winner remains the permanent owner.
    return await bind_profile(database, profile_id, principal)


async def routing_for_node(database: Any, workflow_id: str, node_id: str, principal: str) -> dict:
    from nodes.browser._handlers import resolve_browser_node
    from nodes.browser._profiles import ProfileStore
    from nodes.browser.browser import BrowserParams
    await resolve_browser_node(principal, workflow_id, node_id)
    cfg = BrowserParams.model_validate(await database.get_node_parameters(node_id) or {})
    store = ProfileStore(database)
    active = []
    if _persistent(database):
        async with database.get_session() as session:
            active = (await session.execute(select(BrowserProfileOwner).where(
                BrowserProfileOwner.principal_id == principal, BrowserProfileOwner.workflow_id == workflow_id,
                BrowserProfileOwner.browser_node_id == node_id, BrowserProfileOwner.task_id.is_not(None)
            ))).scalars().all()
    if len(active) > 1:
        raise NodeUserError("Browser routing is ambiguous. Wait for active tasks to finish before opening this browser.")
    selected_profile = active[0].profile_id if active else cfg.profile_id
    if selected_profile:
        profile = await store.get(principal, selected_profile)
    else:
        workflow = await database.get_workflow(workflow_id)
        name = getattr(workflow, "name", None) or "Browser"
        profile = await store.default_for_workflow(principal, workflow_id, str(name))
    return {**await bind_profile(database, profile.id, principal), "workflow_id": workflow_id, "node_id": node_id,
            "browser_policy": {"allowed_domains": cfg.allowed_domains, "allow_private_network": cfg.allow_private_network}}


async def assert_owner(database: Any, binding: dict, principal: str, task_id: Optional[str] = None) -> dict:
    current = await bind_profile(database, str(binding["profile_id"]), principal)
    if current["owner_id"] != replica_id():
        raise NodeUserError("Browser unavailable — waiting for its owner.")
    if _persistent(database):
        async with database.get_session() as session:
            if getattr(settings(), "distributed_mode", False) is True:
                owner = await session.get(BrowserOwner, current["owner_id"])
                if owner is None or owner.runtime_epoch != RUNTIME_EPOCH:
                    raise NodeUserError("This browser runtime has been fenced.")
            row = await session.get(BrowserProfileOwner, current["profile_id"])
            if row.task_id is not None and row.task_id != task_id:
                raise NodeUserError("BrowserBusy: this profile is assigned to another task.")
    return current


async def assert_runtime_epoch(database: Any) -> None:
    if getattr(settings(), "distributed_mode", False) is not True:
        return
    async with database.get_session() as session:
        owner = await session.get(BrowserOwner, replica_id())
        if owner is None or owner.runtime_epoch != RUNTIME_EPOCH:
            raise NodeUserError("This browser runtime has been fenced.")


async def claim_browser_task(database: Any, binding: dict, principal: str, task_id: str) -> dict:
    current = {**binding, **await assert_owner(database, binding, principal, task_id)}
    if _persistent(database):
        async with database.get_session() as session:
            result = await session.execute(update(BrowserProfileOwner).where(
                BrowserProfileOwner.profile_id == binding["profile_id"],
                (BrowserProfileOwner.task_id.is_(None)) | (BrowserProfileOwner.task_id == task_id)
            ).values(task_id=task_id, workflow_id=binding.get("workflow_id"), browser_node_id=binding.get("node_id"),
                     updated_at=datetime.now(timezone.utc)))
            await session.commit()
            if result.rowcount != 1:
                raise NodeUserError("BrowserBusy: this profile is assigned to another task.")
    from nodes.browser._profiles import ProfileStore
    from nodes.browser._runtime import get_browser_runtime
    runtime = get_browser_runtime()
    controller = None
    try:
        profile = await ProfileStore(database).get(principal, binding["profile_id"])
        controller = runtime.controller_for(profile)
        if controller.task_id is None and controller._op_lock.locked():
            raise NodeUserError("BrowserBusy: this profile is executing another browser step.")
        frozen_policy = current.get("browser_policy")
        session = None
        if isinstance(frozen_policy, dict) and binding.get("workflow_id") and binding.get("node_id"):
            from nodes.browser._session import BrowserSession, SessionKey
            from services.netpolicy import parse_allowed_domains
            policy = runtime.base_policy(allow_private_network=bool(frozen_policy.get("allow_private_network")),
                allowed_domains=parse_allowed_domains(frozen_policy.get("allowed_domains", "")))
            session = BrowserSession(SessionKey(principal, binding["workflow_id"], binding["node_id"]), profile.id,
                label="Browser AI Agent", policy=policy)
            if not controller._lease_free_for(session):
                raise NodeUserError("BrowserBusy: this profile is in use by another browser session.")
            if controller.lease_session is not None and controller.lease_session.session_id != session.session_id:
                await controller.release_lease(controller.lease_session.session_id)
        controller.claim_task(task_id)
        current["profile_task_id"] = task_id
        controller.sensitive_login = bool(current.get("sensitive_login"))
        controller.needs_observation |= bool(current.get("needs_observation"))
        controller.challenge_required = bool(current.get("challenge_required"))
        controller._challenge_message = str((current.get("assistance") or {}).get("message") or "Please finish the browser challenge and hand control back.")
        controller.recovered_assistance = current.get("assistance")
        if session is not None:
            session = runtime.register_session(session)
            await controller.acquire_lease(session)
    except BaseException:
        if controller is not None and controller.task_id == task_id:
            await controller.release_task(task_id)
        if _persistent(database):
            async with database.get_session() as session:
                await session.execute(update(BrowserProfileOwner).where(BrowserProfileOwner.profile_id == binding["profile_id"],
                    BrowserProfileOwner.task_id == task_id, BrowserProfileOwner.owner_id == replica_id()).values(task_id=None, workflow_id=None, browser_node_id=None))
                await session.commit()
        raise
    return {"claimed": True, "runtime_epoch": RUNTIME_EPOCH, "binding": current}


async def persist_control(database: Any, profile_id: str, task_id: Optional[str], *, challenge: bool, needs_observation: bool, assistance: Optional[dict]) -> None:
    if not _persistent(database):
        return
    async with database.get_session() as session:
        await session.execute(update(BrowserProfileOwner).where(
            BrowserProfileOwner.profile_id == profile_id, BrowserProfileOwner.owner_id == replica_id(),
            BrowserProfileOwner.task_id == task_id
        ).values(challenge_required=challenge, needs_observation=needs_observation,
                 assistance_reason=(assistance or {}).get("reason"), assistance_message=(assistance or {}).get("message"),
                 assistance_deadline=(assistance or {}).get("deadline"), updated_at=datetime.now(timezone.utc)))
        await session.commit()


async def release_browser_task(database: Any, binding: dict, task_id: str) -> dict:
    await assert_runtime_epoch(database)
    from nodes.browser._runtime import get_browser_runtime
    controller = get_browser_runtime().controller(binding["profile_id"])
    if controller is not None:
        await controller.release_task(task_id)
    if _persistent(database):
        async with database.get_session() as session:
            result = await session.execute(update(BrowserProfileOwner).where(
                BrowserProfileOwner.profile_id == binding["profile_id"], BrowserProfileOwner.task_id == task_id,
                BrowserProfileOwner.owner_id == replica_id()
            ).values(task_id=None, workflow_id=None, browser_node_id=None, updated_at=datetime.now(timezone.utc)))
            await session.commit()
            return {"released": result.rowcount == 1}
    return {"released": True}


async def cleanup_browser_task(database: Any, binding: dict, task_id: str) -> dict:
    if getattr(settings(), "distributed_mode", False) is True and binding["owner_id"] != replica_id():
        from nodes.browser._routing import forward_command
        return await forward_command(binding, str(binding["principal_id"]), "cleanup_task", {"profile_id": binding["profile_id"], "task_id": task_id})
    await assert_runtime_epoch(database)
    from nodes.browser._runtime import get_browser_runtime
    runtime = get_browser_runtime()
    controller = runtime.controller(binding["profile_id"])
    if controller is not None and controller.task_id not in {None, task_id}:
        return {"released": False}
    running = runtime.running(binding["profile_id"]) if hasattr(runtime, "running") else None
    if running is not None and controller is not None and controller.task_id == task_id:
        # Wait for an admitted step to settle before releasing its lifetime claim.
        # Suspension confirms the daemon cannot finish an old command later.
        async with controller._op_lock:
            if not await running.cli.suspend_for_credentials():
                await runtime.stop_profile(binding["profile_id"], reason="task cleanup")
                if running.chrome.is_running():
                    raise NodeUserError("Browser cleanup is waiting for the owner to stop its runtime.")
    return await release_browser_task(database, binding, task_id)


async def set_sensitive(database: Any, profile_id: str, task_id: Optional[str], enabled: bool) -> None:
    await assert_runtime_epoch(database)
    if not _persistent(database):
        return
    async with database.get_session() as session:
        result = await session.execute(update(BrowserProfileOwner).where(
            BrowserProfileOwner.profile_id == profile_id, BrowserProfileOwner.owner_id == replica_id(),
            BrowserProfileOwner.task_id == task_id
        ).values(sensitive_login=enabled, updated_at=datetime.now(timezone.utc)))
        await session.commit()
        if result.rowcount != 1:
            raise NodeUserError("The browser task no longer owns this profile.")


def sign_forward(principal: str, method: str, path: str, body: bytes) -> str:
    payload = json.dumps({"principal": principal, "method": method, "path": path,
                          "body": hashlib.sha256(body).hexdigest(), "expires": int(time.time()) + 30}, separators=(",", ":"))
    import base64
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    secret = str(getattr(settings(), "browser_router_secret", "") or settings().secret_key)
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def verify_forward(token: str, method: str, path: str, body: bytes) -> str:
    import base64
    try:
        encoded, signature = token.split(".", 1)
        secret = str(getattr(settings(), "browser_router_secret", "") or settings().secret_key)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        if payload["expires"] < time.time() or payload["expires"] > time.time() + 35 or payload["method"] != method or payload["path"] != path or payload["body"] != hashlib.sha256(body).hexdigest():
            raise ValueError()
        return str(payload["principal"])
    except (ValueError, KeyError, TypeError):
        raise NodeUserError("Invalid browser owner authorization.") from None
