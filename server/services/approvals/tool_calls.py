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

**In a v1 generation** (``approval_wait_version`` 1, RFC-0007 §4.3) the
workflow's mode and the call's class (services/approvals/classes.py) decide,
for every tool an agent calls, spec or not:

- A read runs. So does a call whose class the mode does not ask; one that
  reaches out (send, money) and answers the owner in the chat is recorded.
- An asked call is held, on a generic card when its tool has no spec, and
  Stripe is held instead of refused. A restricted tool (the Browser) is held
  only when it fetches a URL, and runs restricted otherwise.
- A call its turn waits on (``approval_wait``) is a turn row (``runtime:
  turn``); any other is a draft.
- A rule that cannot be read asks before everything.

**Agents outside AgentWorkflow** (rlm, claude_code, or every agent
with AgentWorkflow off) reach plugins through ``services/handlers/tools.py``,
which calls :func:`check_in_process`. Such a call cannot wait as a draft, so
while the workflow asks first a call that sends is refused (a restricted
tool runs restricted); no row is written.

**At most once.** Temporal runs an activity again only when an attempt
broke off (the worker stopped, a timeout): a failure the node reports comes
back as a result and is not retried. The attempt that broke off may have done
what it was asked, so a later attempt runs only a call that is safe to repeat
(services/approvals/repeats.py); any other call answers that it may already
have happened (:func:`redo_refusal`), and the owner gets a card
(:func:`record_unknown`, ``outcome: unknown``): "May have sent" for a send
the card can show, with Send again behind a confirmation, else "May have
happened" (an ``effect`` row) with Dismiss. A graph node's answer needs the
owner (``requires_user_action``), so its employee pauses at once; a graph
node's send keeps what it sent, without secrets, for Send again. The owner's
approved sends run with no retry
(services/temporal/approved_tool_call_workflow.py), and the workflow that
claimed one records how it went.

What a node sends as (its locked ``server_controlled_fields``: the account,
the mailbox) never comes from the model, so the row keeps the call's
arguments without them; the node's own settings supply them when it runs.
For the same reason a call is judged on what it runs with
(:func:`as_it_runs`): a locked field the model set is put back to the node's
setting first, so it can never talk a call out of being held.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

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
#: The same in a v1 generation, where the card's button is Allow.
WAITING_MESSAGE = (
    "Not done yet: it waits for the owner's OK on a card in the chat, and runs only if they allow it. "
    "Do not call this again for it; tell them it is ready for them to check."
)
REFUSED_MESSAGE = (
    "Not run: {channel} cannot wait for the owner's OK, and they ask to approve everything first. "
    "Tell them what you would do; they can turn Ask first off to let you."
)
RESEND_MESSAGE = (
    "Not sent again: the first try at this broke off, and it may already have gone out. "
    "Do not send it again unless the owner asks; tell them it may or may not have gone out."
)
REDO_MESSAGE = (
    "Not run again: the first try at this broke off midway, and it may already have happened. "
    "Do not run it again unless the owner asks; tell them it may or may not have happened."
)
#: What a graph node's answer adds: no model reads it, the owner does.
REDO_HINT = "The first try broke off midway, so it may already have happened. Check before you resume the employee."
#: The most of a graph node's send a card keeps for Send again.
MAX_KEPT_SEND = 64 * 1024


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


async def as_it_runs(context: Mapping[str, Any], node_cls: Any) -> Dict[str, Any]:
    """The call's ``node_data`` as the node will run with it: each locked
    field the model set put back to the node's saved setting (NodeExecutor's
    rule). :func:`check` judges the call on this."""
    from services.plugin.base import restore_locked_fields, saved_settings

    node_data = dict(context.get("node_data") or {})
    tool_args = context.get("tool_args")
    if not isinstance(tool_args, Mapping) or not (_locked(node_cls) & tool_args.keys()):
        return node_data
    saved = await saved_settings(_database(), str(context.get("node_id") or ""), context.get("parameter_snapshot"))
    return restore_locked_fields(node_cls, node_data, tool_args, saved)


def _clean_node_data(node_cls: Any, node_data: Mapping[str, Any], args: Mapping[str, Any]) -> Dict[str, Any]:
    """The node's settings with the call's arguments over them, minus the
    locked fields the model set (the node's own value comes back when it
    runs)."""
    locked = _locked(node_cls)
    return {key: value for key, value in dict(node_data or {}).items() if not (key in locked and key in (args or {}))}


def _held(row: Any, node_cls: Any, context: Mapping[str, Any], message: str = HELD_MESSAGE) -> Dict[str, Any]:
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
            "message": message,
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


async def _ask_mode(database: Any, workflow_id: Any) -> Optional[str]:
    """The workflow's mode (None: no rule). The strictest when it cannot be
    read: hold the call rather than run it unasked."""
    try:
        return await rules.ask_mode(database, workflow_id)
    except Exception:
        logger.warning("Could not read the Ask first mode; asking before everything", workflow_id=workflow_id, exc_info=True)
        return "everything"


def _restricted(spec: Any, node_data: Mapping[str, Any], context: Mapping[str, Any]) -> Checked:
    """Run with the spec's restrictions instead of holding (the Browser,
    read-only); the model's arguments cannot lift them."""
    restricted = {**node_data, **dict(spec.restrict_while_asking)}
    tool_args = {k: v for k, v in dict(context.get("tool_args") or {}).items() if k not in spec.restrict_while_asking}
    return Checked(node_data=restricted, tool_args=tool_args)


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
    if context.get("approval_wait_version") == 1:
        return await _check_by_class(context, node_cls, spec)
    if spec is None or not is_agent_tool_call(context):
        return RUN
    node_data = await as_it_runs(context, node_cls)
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
        return _restricted(spec, node_data, context)
    row = await hold(database, context, node_cls, spec, node_data)
    return Checked(result=_held(row, node_cls, context))


async def _check_by_class(context: Mapping[str, Any], node_cls: Any, spec: Any) -> Checked:
    """:func:`check` in a v1 generation (see the module docstring)."""
    from services.approvals.classes import MONEY, READ, SEND, call_data, class_of, fetched_url

    if not is_agent_tool_call(context):
        return RUN
    node_data = await as_it_runs(context, node_cls)
    judged = call_data(node_cls, node_data, context.get("tool_args"))
    klass = class_of(node_cls, judged, tool_call=True)
    if klass == READ:
        return RUN
    database = _database()
    mode = await _ask_mode(database, context.get("workflow_id"))
    if mode is None:
        return RUN
    if klass not in rules.ASKS[mode]:
        if klass in (SEND, MONEY) and isinstance(context.get("chat_stream"), Mapping):
            return Checked(record=_auto_recorder(database, context, node_cls, spec, node_data))
        return RUN
    if spec is not None and spec.restrict_while_asking and not fetched_url(node_cls, judged):
        return _restricted(spec, node_data, context)
    row = await hold(database, context, node_cls, spec, node_data)
    return Checked(result=_held(row, node_cls, context, WAITING_MESSAGE))


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
    from services.plugin.base import restore_locked_fields

    spec = approval_spec(node_cls)
    if spec is None or not context.get("parent_node_id"):
        return RUN
    # Judged on what runs: a locked field comes from the node's settings.
    if not spec.sends(restore_locked_fields(node_cls, {**dict(params or {}), **dict(tool_args or {})}, tool_args, params or {})):
        return RUN
    if not await _asks_first(_database(), context.get("workflow_id")):
        return RUN
    if spec.restrict_while_asking:
        restricted = {**dict(params or {}), **dict(spec.restrict_while_asking)}
        args = {k: v for k, v in dict(tool_args or {}).items() if k not in spec.restrict_while_asking}
        return Checked(node_data=restricted, tool_args=args)
    return Checked(result={"status": "not_run", "message": REFUSED_MESSAGE.format(channel=spec.channel)})


def _preview(node_cls: Any, spec: Any, data: Mapping[str, Any], args: Mapping[str, Any]) -> Dict[str, Any]:
    """What the card shows: the spec's view of the call, or, for a tool with
    no spec, its name and up to six of what it runs with, the call's own
    arguments first. A call that fetches a URL shows it."""
    from services.approvals.classes import call_data, fetched_url

    own = call_data(node_cls, data, args)
    if spec is not None:
        preview = spec.preview(data)
        url = fetched_url(node_cls, own)[:300]
        if url and all(line.get("value") != url for line in preview["details"]):
            preview["details"] = [{"label": "Address", "value": url}, *preview["details"]]
        return preview
    node_type = getattr(node_cls, "type", "")
    name = _tool_name(node_cls, node_type)
    values = {**args, **{key: value for key, value in data.items() if key not in args}}
    return {"channel": name, "action": _action(name, node_type, own), "details": _argument_lines(node_cls, values)}


def _tool_name(node_cls: Any, node_type: str) -> str:
    return str(getattr(node_cls, "display_name", None) or node_type or "a tool")


def _action(name: str, node_type: str, data: Mapping[str, Any]) -> str:
    """"Use {tool}", with the operation the call names, as the class and
    repeat tables name each node's operation field."""
    from services.approvals.classes import CLASSES_WHEN
    from services.approvals.repeats import REPEATABLE_WHEN

    field = (CLASSES_WHEN.get(node_type) or REPEATABLE_WHEN.get(node_type) or ("",))[0]
    operation = data.get(field) if field else None
    return f"Use {name} ({operation})" if isinstance(operation, str) and operation else f"Use {name}"


def _row_fields(context: Mapping[str, Any], node_cls: Any, spec: Any, node_data: Mapping[str, Any]) -> Dict[str, Any]:
    args = _clean_args(node_cls, context.get("tool_args") or {})
    data = _clean_node_data(node_cls, node_data, context.get("tool_args") or {})
    preview = _preview(node_cls, spec, data, args)
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
    """The held call's row: created once, found again on a retry. ``spec``
    is None for a tool with no spec (a v1 generation's generic card)."""
    fields = _row_fields(context, node_cls, spec, node_data)
    if context.get("approval_wait"):
        # Its turn waits on it (RFC-0007 §4.6): a turn row, not a draft.
        fields["runtime"] = "turn"
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
        (an attempt broke off, see :func:`redo_refusal`). The card words
        that itself, so it keeps no error then."""
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
                outcome_error=None if success or unknown else str((result or {}).get("error") or "It did not go out.")[:1000],
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


def _sends(spec: Any, data: Mapping[str, Any]) -> bool:
    """A send the card can show, and send again. The browser restricts
    instead of holding, and a page action is not one."""
    return spec is not None and not spec.restrict_while_asking and spec.sends(data)


async def repeatable_call(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> bool:
    """Whether a call may run again after an attempt broke off
    (services/approvals/repeats.py), judged on what it runs with: the node's
    settings with the call's data over them, locked fields put back. Not
    safe when the settings cannot be read."""
    from services.approvals.repeats import REPEATABLE, REPEATABLE_WHEN, repeatable
    from services.plugin.base import restore_locked_fields, saved_settings

    node_type = getattr(node_cls, "type", "")
    if node_type in REPEATABLE or node_type not in REPEATABLE_WHEN:
        return node_type in REPEATABLE
    try:
        saved = await saved_settings(_database(), str(context.get("node_id") or ""), context.get("parameter_snapshot"))
    except Exception:
        logger.warning("Could not read a node's settings; the call will not run again", node_id=context.get("node_id"), exc_info=True)
        return False
    data = restore_locked_fields(node_cls, {**saved, **dict(node_data or {})}, context.get("tool_args"), saved)
    return repeatable(node_cls, data, tool_call=is_agent_tool_call(context))


def unknown_answer(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> Dict[str, Any]:
    """What a call that may already have happened answers instead of
    running. A graph node's answer needs the owner, so its employee pauses
    (MachinaWorkflow reads ``requires_user_action``)."""
    from services.plugin.approval import approval_spec

    sends = _sends(approval_spec(node_cls), node_data)
    answer: Dict[str, Any] = {
        "success": False,
        "error": RESEND_MESSAGE if sends else REDO_MESSAGE,
        "error_type": "SendOutcomeUnknown" if sends else "OutcomeUnknown",
    }
    if context.get("approval_execution") is None and not is_agent_tool_call(context):
        answer.update(hint=REDO_HINT, requires_user_action=True, retryable=False)
    return answer


async def redo_refusal(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """What a later attempt answers instead of running a call that is not
    safe to repeat (see the module docstring), or None to run it."""
    if _attempt() <= 1 or await repeatable_call(context, node_cls, node_data):
        return None
    return unknown_answer(context, node_cls, node_data)


async def record_unknown(
    context: Mapping[str, Any],
    node_cls: Any,
    node_data: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    checked: Optional[Checked] = None,
) -> None:
    """Leave the owner a card for a call that may already have happened,
    best-effort (see the module docstring). A send the owner approved is
    recorded by the workflow that claimed it."""
    if context.get("approval_execution") is not None:
        return
    try:
        if checked is not None and checked.record is not None:
            await checked.record(dict(result), unknown=True)
            return
        await _record_unknown(context, node_cls, node_data)
    except Exception:  # noqa: BLE001 - the answer stands without its card
        logger.warning("Could not record a call that may have happened", node_id=context.get("node_id"), exc_info=True)


async def _record_unknown(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> None:
    from services.plugin.approval import approval_spec

    database = _database()
    spec = approval_spec(node_cls)
    fields: Optional[Dict[str, Any]] = None
    key = ""
    if _sends(spec, node_data):
        if is_agent_tool_call(context):
            fields, key = _row_fields(context, node_cls, spec, node_data), call_key(context)
        else:
            sent = await _graph_send(context, node_cls, node_data)
            if sent is not None:
                fields, key = _row_fields(context, node_cls, spec, sent), _unknown_key("u", context)
    if fields is None:
        fields, key = _effect_fields(context, node_cls, node_data), _unknown_key("e", context)
    now = _utcnow()
    fields.update(
        status="failed",
        approved_by="auto",
        decided_at=now,
        consumed_at=now,
        outcome="unknown",
        outcome_at=now,
        final_text=fields.get("draft_text"),
    )
    row, created = await store.get_or_create(database, idempotency_key=key, fields=fields)
    if created:
        logger.info("A call may have happened", approval_id=row.id, workflow_id=row.workflow_id, kind=row.kind)
        await notify_approval_changed(change_of(row, "failed"))
        if not row.tool_call_id:
            # No model read the answer: a graph node's employee hears it.
            from services.approvals.execution import tell_employee

            await tell_employee(database, row)
    await _show_in_chat(database, context, row)


def _unknown_key(prefix: str, context: Mapping[str, Any]) -> str:
    """One card per call: an agent's call by its id, a graph node's by its
    activity, which every attempt shares."""
    if is_agent_tool_call(context):
        return f"{prefix}:{context.get('workflow_id') or ''}:{context.get('execution_id') or ''}:{context.get('tool_call_id')}"
    try:
        from temporalio import activity

        info = activity.info()
        return f"{prefix}:{info.workflow_id}:{info.workflow_run_id}:{info.activity_id}"
    except Exception:  # noqa: BLE001 - not in an activity
        return f"{prefix}:{context.get('workflow_id') or ''}:{context.get('execution_id') or ''}:{context.get('node_id') or ''}"


async def _graph_send(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """A graph node's send as it ran, its templates resolved (NodeExecutor's
    own steps), without secrets or locked fields: Send again resends it, and
    the node's settings put those back. None when it cannot be rebuilt or is
    too big to keep."""
    from core.container import container
    from services.workflow_sanitizer import sanitize_runtime_payload

    try:
        resolved = await container.workflow_service().prepare_node_parameters(
            node_id=str(context.get("node_id") or ""),
            node_type=node_cls.type,
            parameters=dict(node_data or {}),
            nodes=context.get("nodes", []),
            edges=context.get("edges", []),
            session_id=context.get("session_id", "default"),
            execution_id=context.get("execution_id"),
            workflow_id=context.get("workflow_id"),
            outputs=context.get("inputs", {}),
            user_id=str(context.get("user_id") or "owner"),
        )
    except Exception:  # noqa: BLE001 - the card shows what was tried instead
        logger.warning("Could not rebuild what a node sent", node_id=context.get("node_id"), exc_info=True)
        return None
    locked = _locked(node_cls)
    kept = {key: value for key, value in sanitize_runtime_payload(dict(resolved or {})).items() if key not in locked}
    try:
        size = len(json.dumps(kept, default=str))
    except (TypeError, ValueError):
        return None
    return kept if size <= MAX_KEPT_SEND else None


def _effect_fields(context: Mapping[str, Any], node_cls: Any, node_data: Mapping[str, Any]) -> Dict[str, Any]:
    """An ``effect`` row: what was tried and what it ran with (no secrets),
    for a card the owner checks and dismisses."""
    node_type = getattr(node_cls, "type", None) or str(context.get("node_type") or "")
    name = _tool_name(node_cls, node_type)
    tool_args = context.get("tool_args")
    stream = context.get("chat_stream") if isinstance(context.get("chat_stream"), Mapping) else {}
    run_id = context.get("chat_run_id") or (stream or {}).get("run_id")
    return {
        "owner_id": str(context.get("user_id") or "owner"),
        "workflow_id": str(context.get("workflow_id") or ""),
        "node_id": str(context.get("node_id") or ""),
        "generation": int(context.get("generation") or 0),
        "execution_id": context.get("execution_id"),
        "runtime": "temporal",
        "kind": "effect",
        "channel": name[:60],
        "action": _action(name, node_type, node_data)[:200],
        "details": _argument_lines(node_cls, tool_args if isinstance(tool_args, Mapping) else node_data),
        "node_type": node_type or None,
        "tool_node_id": str(context.get("node_id") or ""),
        "tool_call_id": str(context.get("tool_call_id") or ""),
        "agent_node_id": str(context.get("parent_node_id") or ""),
        "run_id": run_id if isinstance(run_id, str) and run_id else None,
    }


def _argument_lines(node_cls: Any, values: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Up to six of what a call ran with, each cut at 300 characters, with
    secrets and locked fields left out."""
    from services.workflow_sanitizer import sanitize_runtime_payload

    locked = _locked(node_cls)
    lines: List[Dict[str, str]] = []
    for key, value in sanitize_runtime_payload(dict(values or {})).items():
        if key in locked or value in (None, "", [], {}):
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        lines.append({"label": str(key)[:60], "value": text[:300]})
        if len(lines) == 6:
            break
    return lines


def claim_token() -> str:
    return uuid.uuid4().hex


__all__ = [
    "Checked",
    "HELD_MESSAGE",
    "REDO_HINT",
    "REDO_MESSAGE",
    "RESEND_MESSAGE",
    "RUN",
    "WAITING_MESSAGE",
    "as_it_runs",
    "call_key",
    "check",
    "check_in_process",
    "claim_token",
    "hold",
    "is_agent_tool_call",
    "record_unknown",
    "redo_refusal",
    "repeatable_call",
    "unknown_answer",
]
