"""Durable spillway for controller queues across bounded Temporal payloads."""
import hashlib
import json
from temporalio import activity
from sqlmodel import select
from models.employees import WorkflowQueuedEvent


@activity.defn(name="controller.queue.spill")
async def spill_controller_events(payload: dict) -> dict:
    from core.container import container
    database = container.database()
    async with database.reserved_session() as session:
        if "chunk_id" in payload:
            chunk_controller = payload["controller_id"] + ":chunks:" + payload["chunk_id"]
            if "chunk" in payload:
                key = hashlib.sha256(f'{chunk_controller}:{payload["index"]}'.encode()).hexdigest()
                if await session.get(WorkflowQueuedEvent, key) is None:
                    session.add(WorkflowQueuedEvent(id=key, controller_id=chunk_controller, sequence=payload["index"], item={"chunk": payload["chunk"]}))
                await session.commit()
                return {"stored": True}
            chunks = (await session.execute(select(WorkflowQueuedEvent).where(WorkflowQueuedEvent.controller_id == chunk_controller).order_by(WorkflowQueuedEvent.sequence))).scalars().all()
            if len(chunks) != payload["count"]:
                # A finalized retry has no chunks but already has its event.
                if await session.get(WorkflowQueuedEvent, payload["chunk_id"]) is not None:
                    return {"spilled": 1}
                raise ValueError("Incomplete controller event chunks")
            payload = {**payload, "events": [json.loads("".join(row.item["chunk"] for row in chunks))]}
            for row in chunks:
                await session.delete(row)
        rows = (await session.execute(select(WorkflowQueuedEvent).where(WorkflowQueuedEvent.controller_id == payload["controller_id"])) ).scalars().all()
        prepend = bool(payload.get("prepend"))
        sequence = (min((row.sequence or 0 for row in rows), default=0) - len(payload["events"]) - 1) if prepend else max((row.sequence or 0 for row in rows), default=0)
        for listener_id, event in payload["events"]:
            key = hashlib.sha256(f'{payload["controller_id"]}:{listener_id}:{event["id"]}'.encode()).hexdigest()
            if await session.get(WorkflowQueuedEvent, key) is None:
                sequence += 1
                session.add(WorkflowQueuedEvent(id=key, controller_id=payload["controller_id"], sequence=sequence, item={"listener_id": listener_id, "event": event}))
        await session.commit()
    return {"spilled": len(payload["events"])}


@activity.defn(name="controller.queue.read")
async def read_controller_events(payload: dict) -> dict:
    from core.container import container
    async def mutate(session):
        query = select(WorkflowQueuedEvent).where(WorkflowQueuedEvent.controller_id == payload["controller_id"]).order_by(WorkflowQueuedEvent.sequence).limit(100)
        listener_ids = payload.get("review_listener_ids")
        if listener_ids is not None:
            # Filtering by listener identity happens below because item is JSON;
            # do not stop before a review event buried behind ordinary events.
            query = select(WorkflowQueuedEvent).where(WorkflowQueuedEvent.controller_id == payload["controller_id"]).order_by(WorkflowQueuedEvent.sequence)
        rows = (await session.execute(query)).scalars().all()
        selected, events, size = [], [], 0
        for row in rows:
            if listener_ids is not None and row.item["listener_id"] not in listener_ids:
                continue
            item = [row.item["listener_id"], row.item["event"]]
            item_size = len(json.dumps(item, separators=(",", ":"), ensure_ascii=True).encode())
            if events and (size + item_size > 512_000 or len(events) >= 100):
                break
            events.append(item)
            selected.append(row)
            size += item_size
        for row in selected:
            await session.delete(row)
        await session.flush()
        remaining = (await session.execute(select(WorkflowQueuedEvent.id).where(WorkflowQueuedEvent.controller_id == payload["controller_id"]).limit(1))).first()
        return {"events": events, "more": remaining is not None}
    result, _ = await container.database().run_runtime_mutation(resource_type="controller_queue", resource_id=payload["controller_id"], operation="read", mutation_id=payload["request_id"], mutate=mutate)
    return result


def event_batches(events, *, max_bytes=512_000, max_events=100):
    """Bound every activity and carry payload by serialized bytes and count."""
    batch, size = [], 0
    for item in events:
        item_size = len(json.dumps(item, separators=(",", ":"), ensure_ascii=True).encode())
        if batch and (size + item_size > max_bytes or len(batch) >= max_events):
            yield batch
            batch, size = [], 0
        batch.append(list(item))
        size += item_size
    if batch:
        yield batch
