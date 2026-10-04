"""A tool call that sends, held for the owner when the workflow asks first.

``BaseNode.as_activity`` calls :func:`check` before it runs a tool call an
agent made (``tool_call_id`` and ``parent_node_id`` in its context) of a
plugin that declares an ``approval`` spec (services/plugin/approval.py):

- **No rule** (a workflow built in the editor): the call runs.
- **Ask first on**, and the call sends: it does not run. A ``tool_call``
  row keeps what would run and what the card shows, and the model reads
  that it waits for the owner. A ``refuse_while_asking`` tool is refused
  instead; a ``restrict_while_asking`` tool runs with those settings.
- **Ask first off**: the call runs. When it answers the owner in the chat
  (the call carries a ``chat_stream``), a row records it (``approved_by:
  auto``) with how it went, so the owner sees what went out.

A call that comes back to run because the owner pressed Send carries
``approval_execution`` (services/approvals/execution.py): it runs only when
its row is the one being sent, and is never held again.

**Agents outside AgentWorkflow** (rlm, claude_code, vertex, or every agent
with AgentWorkflow off) reach plugins through ``services/handlers/tools.py``,
which calls :func:`check_in_process`. Such a call cannot wait as a draft, so
while the workflow asks first a call that sends is refused (a restricted
tool runs restricted); no row is written.

**At most once.** Temporal runs a tool activity again only when an attempt
broke off (the worker stopped, a timeout): a failure the node reports comes
back as a result and is not retried. The attempt that broke off may have
sent the message, so a later attempt of a call that sends does not send it
again (:func:`resend_refusal`): the model reads that it may have gone out,
and with Ask first off the chat's record says so (``outcome: unknown``). The
owner's approved sends already run with no retry
(services/temporal/approved_tool_call_workflow.py). A tool that Ask first
restricts instead of holding (the browser) keeps its own retries: its later
attempt resumes a wait the worker cut short (``NodeWaitInterrupted``).

What a node sends as (its locked ``server_controlled_fields``: the account,
the mailbox) never comes from the model, so the row keeps the call's
arguments without them; the node's own settings supply them when it runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from core.logging import get_logger
from services.approvals import rules, store
from services.approvals.contract import DEFAULT_TIMEOUT_HOURS
from services.approvals.listeners import change_of, notify_approval_changed

logger = get_logger(__name__)

MAX_DRAFT = 20000

HELD_MESSAGE = (
    "Not sent yet: it waits for the owner's OK on a card in the chat. It goes out only if they press Send, "
    "so do not call this again for it. Tell them it is ready for them to check."
)
REFUSED_MESSAGE = (
    "Not run: {channel} cannot wait for the owner's OK, and they ask to approve everything first. "
    "Tell them what you would do; they can turn Ask first off to let you."
)
RESEND_MESSAGE = (
    "Not sent again: the first try at this broke off, and it may already have gone out. "
    "Do not send it again unless the owner asks; tell them it may or may not have gone out."
)


@dataclass
class Checked:
    #: Answer with this instead of running.
    result: Optional[Dict[str, Any]] = None
    #: Run with these instead (a tool restricted while Ask first is on).
    node_data: Optional[Dict[str, Any]] = None
    tool_args: Optional[Dict[str, Any]] = None
    #: After running: record how it went (``await record(result)``).
    record: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None


RUN = Checked()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _database() -> Any:
    from core.container import container

    return container.database()


def is_agent_tool_call(context: Mapping[str, Any]) -> bool:
    return bool(context.get("tool_call_id")) and bool(context.get("parent_node_id"))


def call_key(context: Mapping[str, Any]) -> str:
    return f"c:{context.get('workflow_id') or ''}:{context.get('execution_id') or ''}:{context.get('tool_call_id')}"


def _locked(node_cls: Any) -> frozenset:
    from services.plugin.base import locked_tool_fields

    return locked_tool_fields(node_cls)


def _clean_args(node_cls: Any, args: Mapping[str, Any]) -> Dict[str, Any]:
    locked = _locked(node_cls)
    return {key: value for key, value in dict(args or {}).items() if key not in locked}


def _clean_node_data(node_cls: Any, node_data: Mapping[str, Any], args: Mapping[str, Any]) -> Dict[str, Any]:
    """The node's settings with the call's arguments over them, minus the
    locked fields the model set (the node's own value comes back when it
    runs)."""
    locked = _locked(node_cls)
    return {key: value for key, value in dict(node_data or {}).items() if not (key in locked and key in (args or {}))}


def _held(row: Any, node_cls: Any, context: Mapping[str, Any]) -> Dict[str, Any]:
    from datetime import datetime as _dt

    return {
        "success": True,
        "node_id": context.get("node_id"),
        "node_type": getattr(node_cls, "type", ""),
        "result": {
            "status": "waiting_for_owner",
            "approval_id": row.id,
            "channel": row.channel,
            "to": row.recipient_label or row.recipient,
            "message": HELD_MESSAGE,
        },
        "approval_id": row.id,
        "execution_id": context.get("execution_id"),
        "timestamp": _dt.now().isoformat(),
    }


async def _execution_allowed(database: Any, context: Mapping[str, Any]) -> bool:
    execution = context.get("approval_execution")
    if not isinstance(execution, Mapping):
        return False
    row = await store.get(database, str(execution.get("approval_id") or ""))
    return bool(row is not None and row.status == "sending" and row.claim_token and row.claim_token == execution.get("claim_token"))


async def _asks_first(database: Any, workflow_id: Any) -> Optional[bool]:
    """The workflow's Ask first rule (None: no rule). True when it cannot be
    read: hold or refuse the call rather than send it unasked."""
    try:
        return await rules.ask_first(database, workflow_id)
    except Exception:
        logger.warning("Could not read the Ask first rule; acting as if it is on", workflow_id=workflow_id, exc_info=True)
        return True


async def check(context: Mapping[str, Any], node_cls: Any) -> Checked:
    """What to do with a tool call before it runs (see the module docstring)."""
    from services.plugin.approval import approval_spec

    spec = approval_spec(node_cls)
    if context.get("approval_execution") is not None:
        database = _database()
        if await _execution_allowed(database, context):
            return RUN
        return Checked(
            result={
                "success": False,
                "node_id": context.get("node_id"),
                "node_type": getattr(node_cls, "type", ""),
                "error": "This send is no longer the one approved.",
                "error_type": "ApprovalNotClaimed",
            }
        )
    if spec is None or not is_agent_tool_call(context):
        return RUN
    node_data = dict(context.get("node_data") or {})
    if not spec.sends(node_data):
        return RUN
    database = _database()
    asking = await _asks_first(database, context.get("workflow_id"))
    if asking is None:
        return RUN
    if not asking:
        if not isinstance(context.get("chat_stream"), Mapping):
            return RUN
        return Checked(record=_auto_recorder(database, context, node_cls, spec, node_data))
    if spec.refuse_while_asking:
        return Checked(
            result={
                "success": True,
                "node_id": context.get("node_id"),
                "node_type": getattr(node_cls, "type", ""),
                "result": {"status": "not_run", "message": REFUSED_MESSAGE.format(channel=spec.channel)},
                "execution_id": context.get("execution_id"),
            }
        )
    if spec.restrict_while_asking:
        restricted = {**node_data, **dict(spec.restrict_while_asking)}
        tool_args = {k: v for k, v in dict(context.get("tool_args") or {}).items() if k not in spec.restrict_while_asking}
        return Checked(node_data=restricted, tool_args=tool_args)
    row = await hold(database, context, node_cls, spec, node_data)
    return Checked(result=_held(row, node_cls, context))


async def check_in_process(
    context: Mapping[str, Any],
    node_cls: Any,
    params: Mapping[str, Any],
    tool_args: Mapping[str, Any],
) -> Checked:
    """:func:`check` for an agent's tool call that reaches the plugin in
    process (see the module docstring). ``result`` is the flat answer the
    model reads, as ``execute_as_tool`` gives one."""
    from services.plugin.approval import approval_spec

    spec = approval_spec(node_cls)
    if spec is None or not context.get("parent_node_id"):
        return RUN
    if not spec.sends({**dict(params or {}), **dict(tool_args or {})}):
        return RUN
    if not await _asks_first(_database(), context.get("workflow_id")):
        return RUN
    if spec.restrict_while_asking:
        restricted = {**dict(params or {}), **dict(spec.restrict_while_asking)}
        args = {k: v for k, v in dict(tool_args or {}).items() if k not in spec.restrict_while_asking}
        return Checked(node_data=restricted, tool_args=args)
    return Checked(result={"status": "not_run", "message": REFUSED_MESSAGE.format(channel=spec.channel)})


def _row_fields(context: Mapping[str, Any], node_cls: Any, spec: Any, node_data: Mapping[str, Any]) -> Dict[str, Any]:
    args = _clean_args(node_cls, context.get("tool_args") or {})
    data = _clean_node_data(node_cls, node_data, context.get("tool_args") or {})
    preview = spec.preview(data)
    stream = context.get("chat_stream") if isinstance(context.get("chat_stream"), Mapping) else {}
    run_id = context.get("chat_run_id") or (stream or {}).get("run_id")
    return {
        "owner_id": str(context.get("user_id") or "owner"),
        "workflow_id": str(context.get("workflow_id") or ""),
        "node_id": str(context.get("node_id") or ""),
        "generation": int(context.get("generation") or 0),
        "execution_id": context.get("execution_id"),
        "runtime": "temporal",
        "kind": "tool_call",
        "channel": preview["channel"][:60],
        "action": preview["action"][:200],
        "recipient": str(preview.get("recipient") or "")[:500],
        "recipient_label": str(preview.get("recipient_label") or "")[:200],
        "subject": (preview.get("subject") or None),
        "draft_text": str(preview.get("body") or "")[:MAX_DRAFT],
        "max_length": int(preview.get("max_length") or MAX_DRAFT),
        "details": list(preview.get("details") or []),
        "node_type": getattr(node_cls, "type", None),
        "tool_node_id": str(context.get("node_id") or ""),
        "tool_call_id": str(context.get("tool_call_id") or ""),
        "agent_node_id": str(context.get("parent_node_id") or ""),
        "run_id": run_id if isinstance(run_id, str) and run_id else None,
        "node_data": data,
        "args": args,
        "original_args": dict(context.get("tool_args") or {}),
        "body_field": preview.get("body_field"),
        "subject_field": preview.get("subject_field"),
    }


async def hold(database: Any, context: Mapping[str, Any], node_cls: Any, spec: Any, node_data: Mapping[str, Any]) -> Any:
    """The held call's row: created once, found again on a retry."""
    fields = _row_fields(context, node_cls, spec, node_data)
    fields["expires_at"] = _utcnow() + timedelta(hours=DEFAULT_TIMEOUT_HOURS)
    fields["ui_part_id"] = await _ui_part(database, fields.get("run_id"))
    row, created = await store.get_or_create(database, idempotency_key=call_key(context), fields=fields)
    if created:
        logger.info("Held a call for the owner", approval_id=row.id, workflow_id=row.workflow_id, channel=row.channel)
        await notify_approval_changed(change_of(row, "requested"))
    await _show_in_chat(database, context, row)
    return row


async def _ui_part(database: Any, run_id: Any) -> Any:
    """The interface whose button started the run, for "Linked to the form
    above" on the card."""
    if not isinstance(run_id, str) or not run_id:
        return None
    try:
        from services.chat.ledger import ui_part_of_run

        return await ui_part_of_run(database, run_id)
    except Exception:  # noqa: BLE001 - the card shows without the link
        logger.warning("Could not read which interface started a run", run_id=run_id, exc_info=True)
        return None


async def _show_in_chat(database: Any, context: Mapping[str, Any], row: Any) -> None:
    """A call made answering the owner shows its card in that answer."""
    stream = context.get("chat_stream")
    if not isinstance(stream, Mapping) or not isinstance(stream.get("run_id"), str):
        return
    try:
        from services.chat.parts import show_approval

        await show_approval(database, stream, approval_id=row.id, tool_call_id=row.tool_call_id)
    except Exception:  # noqa: BLE001 - the draft waits either way
        logger.warning("Could not show a draft in the chat", approval_id=row.id, exc_info=True)


def _auto_recorder(database: Any, context: Mapping[str, Any], node_cls: Any, spec: Any, node_data: Mapping[str, Any]):
    async def record(result: Dict[str, Any], *, unknown: bool = False) -> None:
        """Record how the call went; ``unknown``: it may have gone out
        (an attempt broke off, see :func:`resend_refusal`)."""
        try:
            success = not unknown and bool(isinstance(result, dict) and result.get("success", True) is not False)
            now = _utcnow()
            fields = _row_fields(context, node_cls, spec, node_data)
            fields.update(
                status="sent" if success else "failed",
                approved_by="auto",
                decided_at=now,
                consumed_at=now,
                outcome="sent" if success else ("unknown" if unknown else "not_sent"),
                outcome_error=None if success else str((result or {}).get("error") or "It did not go out.")[:1000],
                outcome_at=now,
                final_text=fields.get("draft_text"),
            )
            row, created = await store.get_or_create(database, idempotency_key=call_key(context), fields=fields)
            if created:
                await notify_approval_changed(change_of(row, "sent" if success else "failed"))
            await _show_in_chat(database, context, row)
        except Exception:
            logger.warning("Could not record a send made without asking", tool_call_id=context.get("tool_call_id"), exc_info=True)

    return record


def _attempt() -> int:
    """This activity attempt's number (1 outside an activity)."""
    try:
        from temporalio import activity

        return int(activity.info().attempt)
    except Exception:  # noqa: BLE001 - not in an activity
        return 1


def resend_refusal(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """What a later attempt of an agent's call that sends answers instead
    of sending (see the module docstring), or None to run it."""
    from services.plugin.approval import approval_spec

    spec = approval_spec(node_cls)
    if spec is None or _attempt() <= 1:
        return None
    if context.get("approval_execution") is None and not is_agent_tool_call(context):
        return None
    if spec.restrict_while_asking or not spec.sends(node_data):
        return None
    return {"success": False, "error": RESEND_MESSAGE, "error_type": "SendOutcomeUnknown"}


def claim_token() -> str:
    return uuid.uuid4().hex


__all__ = [
    "Checked",
    "HELD_MESSAGE",
    "RESEND_MESSAGE",
    "RUN",
    "call_key",
    "check",
    "check_in_process",
    "claim_token",
    "hold",
    "is_agent_tool_call",
    "resend_refusal",
]
