"""Attention items: something the owner should look at, one line on Home.

An item is a kind and a reference: ``stuck_retry`` and ``<Temporal workflow
id>:<activity id>`` for an activity that keeps retrying past its limit
(services/temporal/_interceptors.py). Every sighting :func:`touch`-es it, a
recovery :func:`resolve`-s it, and :func:`open_items` returns the open ones
seen in the last hour, so an item stranded by a Reset or a deletion drops
off by itself. Both writes are best effort and never raise: the work never
fails over its alert.

Home reads the items through the ``list_attention_items`` command. The
identity-only ``attention_lifecycle`` broadcast tells it to look again when
an item opens or is resolved; nothing announces the one-hour drop-off, so
Home also polls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import WebSocket
from sqlmodel import select

from core.logging import get_logger
from models.attention import AttentionItem
from services.events.envelope import WorkflowEvent
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)

STUCK_RETRY = "stuck_retry"
#: An item drops off this long after it was last seen.
WINDOW = timedelta(hours=1)
WIRE_KEY = "attention_lifecycle"
SOURCE = "opencompany://services/attention"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; they were written in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _database() -> Any:
    from core.container import container

    return container.database()


def attention_lifecycle(stage: str, kind: str) -> WorkflowEvent:
    """An item opened or was resolved: identity only, Home reads it back."""
    return WorkflowEvent(source=SOURCE, type=f"com.opencompany.attention.{stage}", subject=kind, data={"kind": kind})


async def _announce(stage: str, kind: str) -> None:
    from services.status_broadcaster import get_status_broadcaster

    event = attention_lifecycle(stage, kind)
    await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)})


async def touch(kind: str, ref: str, *, database: Any = None, now: Optional[datetime] = None) -> None:
    """Record a sighting: open the item, or count it while it stays open.
    A resolved item, or one not seen for an hour, opens again."""
    now = now or _utcnow()
    try:
        database = database or _database()
        async with database.reserved_session() as session:
            item = await session.get(AttentionItem, f"{kind}:{ref}")
            opened = item is None or item.state != "open" or _aware(item.last_seen) < now - WINDOW
            if item is None:
                item = AttentionItem(id=f"{kind}:{ref}", kind=kind, ref=ref)
            if opened:
                item.state, item.first_seen, item.count = "open", now, 0
            item.count += 1
            item.last_seen = now
            session.add(item)
            await session.commit()
        if opened:
            await _announce("opened", kind)
    except Exception:
        logger.warning("Attention item not recorded", kind=kind, exc_info=True)


async def resolve(kind: str, ref: str, *, database: Any = None) -> None:
    """Close an open item."""
    try:
        database = database or _database()
        async with database.reserved_session() as session:
            item = await session.get(AttentionItem, f"{kind}:{ref}")
            if item is None or item.state != "open":
                return
            item.state = "resolved"
            session.add(item)
            await session.commit()
        await _announce("resolved", kind)
    except Exception:
        logger.warning("Attention item not resolved", kind=kind, exc_info=True)


async def open_items(database: Any, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The open items seen in the last hour, newest first."""
    since = (now or _utcnow()) - WINDOW
    async with database.get_session() as session:
        rows = (
            (
                await session.execute(
                    select(AttentionItem)
                    .where(AttentionItem.state == "open", AttentionItem.last_seen >= since)
                    .order_by(AttentionItem.last_seen.desc())
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "severity": row.severity,
            "ref": row.ref,
            "count": row.count,
            "first_seen": _aware(row.first_seen).isoformat(),
            "last_seen": _aware(row.last_seen).isoformat(),
        }
        for row in rows
    ]


@ws_handler()
async def handle_list_attention_items(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """``{success, items: [{id, kind, severity, ref, count, first_seen,
    last_seen}]}``. The owner's sockets only: the internal worker socket
    reaches no command outside its allowlist."""
    return {"items": await open_items(_database())}


WS_HANDLERS = {"list_attention_items": handle_list_attention_items}


def _register() -> None:
    from services.ws_handler_registry import register_ws_handlers

    register_ws_handlers(WS_HANDLERS)


_register()

__all__ = [
    "STUCK_RETRY",
    "WINDOW",
    "attention_lifecycle",
    "handle_list_attention_items",
    "open_items",
    "resolve",
    "touch",
]
