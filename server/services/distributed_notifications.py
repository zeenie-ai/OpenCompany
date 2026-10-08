"""PostgreSQL identifier-only invalidation for Browser metadata on replicas."""
from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text

CHANNEL = "opencompany_browser_invalidation"
_instance = None


def notification(message: dict, origin: str) -> dict | None:
    kind = message.get("type")
    if kind not in {"browser_updated", "browser_profiles_updated"}:
        return None
    event = message.get("data") or {}
    data = event.get("data") or {}
    result = {"origin": origin, "kind": kind}
    if kind == "browser_updated":
        for key in ("workflow_id", "node_id", "session_id"):
            value = data.get(key)
            if not isinstance(value, str) or not value or len(value) > 512:
                return None
            result[key] = value
    return result


class BrowserNotifications:
    def __init__(self, database, broadcaster):
        self.database, self.broadcaster = database, broadcaster
        self.origin = uuid.uuid4().hex
        self.task = None
        self.connection = None
        self.deliveries = set()
        self.stopped = False

    async def start(self):
        await self._connect()
        self.task = asyncio.create_task(self._listen(), name="browser-postgresql-invalidation")

    async def _connect(self):
        import asyncpg
        self.disconnected = asyncio.Event()
        url = self.database.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        self.connection = await asyncpg.connect(url, timeout=10)
        self.connection.add_termination_listener(lambda _: self.disconnected.set())
        try:
            await self.connection.add_listener(CHANNEL, self._receive)
        except BaseException:
            await self.connection.close()
            raise

    async def _listen(self):
        while not self.stopped:
            await self.disconnected.wait()
            if self.stopped:
                return
            try:
                await self._connect()
                # NOTIFY is deliberately transient; reconnect requires a refresh.
                await self.broadcaster.broadcast({"type": "browser_profiles_updated", "data": {}}, shared=False)
            except Exception:
                # Metadata can be refreshed through the authorized APIs while
                # the dedicated listener reconnects; no task work is replayed.
                await asyncio.sleep(5)
                self.disconnected.set()

    def _receive(self, connection, pid, channel, payload):
        try:
            data = json.loads(payload)
            kind = data.get("kind")
            expected = {"origin", "kind"} | ({"workflow_id", "node_id", "session_id"} if kind == "browser_updated" else set())
            if kind not in {"browser_updated", "browser_profiles_updated"} or set(data) != expected:
                return
            if any(not isinstance(value, str) or not value or len(value) > 512 for value in data.values()) or data["origin"] == self.origin:
                return
            message = ({"type": "browser_invalidated", "data": {key: data[key] for key in ("workflow_id", "node_id", "session_id")}}
                       if kind == "browser_updated" else {"type": "browser_profiles_updated", "data": {}})
            task = asyncio.create_task(self.broadcaster.broadcast(message, shared=False))
            self.deliveries.add(task)
            task.add_done_callback(self.deliveries.discard)
        except (ValueError, TypeError, AttributeError):
            return

    async def publish(self, message):
        data = notification(message, self.origin)
        if data is not None:
            async with self.database.engine.begin() as connection:
                await connection.execute(text("SELECT pg_notify(:channel, :payload)"),
                                         {"channel": CHANNEL, "payload": json.dumps(data)})

    async def stop(self):
        self.stopped = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.connection and not self.connection.is_closed():
            await self.connection.close()
        if self.deliveries:
            await asyncio.gather(*self.deliveries, return_exceptions=True)


async def start(database, broadcaster):
    global _instance
    if database.settings.distributed_mode:
        _instance = BrowserNotifications(database, broadcaster)
        await _instance.start()


async def publish(message):
    if _instance:
        await _instance.publish(message)


async def stop():
    global _instance
    if _instance:
        await _instance.stop()
        _instance = None
