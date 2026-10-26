"""Always-allow rules (RFC-0007 §4.4): the owner's standing OK for one kind
of call of one employee. In a v1 generation a call a rule matches runs
without asking (services/approvals/tool_calls.py), recorded as
``approved_by: auto`` when it answers the owner in the chat.

A rule names the node type, the operation the call names (the class
table's field, services/approvals/classes.py; "" for every operation) and
one scope:

- ``host``: a call that fetches a URL on that host. A URL request keeps its
  method as the operation, so a GET rule never lets a DELETE through;
- ``recipient``: a spec'd send to that recipient, as its card shows it;
- ``operation``: every call with that operation;
- ``tool``: every call of the tool.

A card offers the rule its call would make only when the call's turn waits
on it (no other row is known to be v1, where rules apply), when the rule
can name all it covers, and never for money: the employee's list switches
money on, per money tool or operation (D19). A binding whose own Ask first
switch is off (an MCP tool's ``mcp_ask``) counts as a rule.

Reset keeps rules, since they are the owner's settings; deleting the
employee deletes them. Never imports ``nodes/``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from models.approvals import ApprovalAllow

#: A node whose binding carries its own Ask first switch: off counts as a
#: rule.
ASK_SWITCHES: Dict[str, str] = {"mcpConnector": "mcp_ask"}


@dataclass(frozen=True)
class Scope:
    """What one rule covers, and how the owner reads it."""

    node_type: str
    operation: str
    kind: str
    value: str
    label: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _name(node_cls: Any, spec: Any) -> str:
    """The tool as its cards name it."""
    if spec is not None:
        return spec.channel
    return str(getattr(node_cls, "display_name", None) or getattr(node_cls, "type", ""))


def _recipient(spec: Any, data: Mapping[str, Any]) -> str:
    """Who a spec'd send goes to, as its card shows it; "" for none."""
    if spec is None or spec.restrict_while_asking or not spec.recipient or not spec.sends(data):
        return ""
    return " ".join(str(spec.preview(data).get("recipient") or "").lower().split())


def scope_of(node_cls: Any, node_data: Mapping[str, Any], args: Any) -> Optional[Scope]:
    """The rule an "Always allow" would make for this call, or None when it
    cannot name all it covers (a URL with no host, an operation that is not
    one value)."""
    from services.approvals.classes import CLASSES_WHEN, call_data, fetched_url, operation_of
    from services.plugin.approval import approval_spec

    node_type = getattr(node_cls, "type", "")
    spec = approval_spec(node_cls)
    own = call_data(node_cls, node_data, args)
    field = CLASSES_WHEN.get(node_type, ("",))[0]
    operation = operation_of(node_cls, own, tool_call=True)
    if field and operation is None:
        return None
    operation = operation or ""
    name = _name(node_cls, spec)
    url = fetched_url(node_cls, own)
    if url:
        host = _host(url)
        if not host:
            return None
        if field == "method":
            return Scope(node_type, operation, "host", host, f"{name}: {operation} requests to {host}")
        return Scope(node_type, "", "host", host, f"{name} on {host}")
    recipient = _recipient(spec, node_data)
    if recipient:
        action = f"{spec.action} ({operation})" if operation else spec.action
        return Scope(node_type, operation, "recipient", recipient, f"{action} to {recipient}")
    if operation:
        return Scope(node_type, operation, "operation", "", f"{name}: {operation}")
    return Scope(node_type, "", "tool", "", name)


def offer(row: Any, approval_class: Optional[str]) -> Optional[Scope]:
    """The rule a card offers for its call (see the module docstring)."""
    from services.node_registry import get_node_class

    if approval_class in (None, "read", "money") or row.status != "pending" or row.runtime != "turn":
        return None
    node_cls = get_node_class(row.node_type) if row.node_type else None
    return scope_of(node_cls, row.node_data or {}, row.args) if node_cls is not None else None


async def allowed(database: Any, workflow_id: Any, node_cls: Any, node_data: Mapping[str, Any], args: Any) -> bool:
    """Whether a rule, or the binding's own switch, lets this call run
    without asking."""
    from services.approvals.classes import call_data, fetched_url, operation_of
    from services.plugin.approval import approval_spec

    node_type = getattr(node_cls, "type", "")
    switch = ASK_SWITCHES.get(node_type)
    if switch and node_data.get(switch) is False:
        return True
    async with database.get_session() as session:
        result = await session.execute(
            select(ApprovalAllow).where(ApprovalAllow.workflow_id == str(workflow_id or ""), ApprovalAllow.node_type == node_type)
        )
        found = list(result.scalars().all())
    if not found:
        return False
    own = call_data(node_cls, node_data, args)
    operation = operation_of(node_cls, own, tool_call=True) or ""
    host = _host(fetched_url(node_cls, own))
    recipient = _recipient(approval_spec(node_cls), node_data)
    for rule in found:
        if rule.operation and rule.operation != operation:
            continue
        if rule.scope_kind in ("tool", "operation"):
            return True
        if (rule.scope_kind, rule.scope_value) in (("host", host), ("recipient", recipient)):
            return True
    return False


async def add(database: Any, workflow_id: str, scope: Scope, *, created_by: str = "owner") -> ApprovalAllow:
    """Save a rule; the same rule again returns the one saved."""
    row = ApprovalAllow(
        id=uuid.uuid4().hex,
        workflow_id=workflow_id,
        node_type=scope.node_type,
        operation=scope.operation,
        scope_kind=scope.kind,
        scope_value=scope.value,
        label=scope.label,
        created_at=_utcnow(),
        created_by=created_by,
    )
    try:
        async with database.get_session() as session:
            session.add(row)
            await session.commit()
        return row
    except IntegrityError:
        async with database.get_session() as session:
            result = await session.execute(
                select(ApprovalAllow).where(
                    ApprovalAllow.workflow_id == workflow_id,
                    ApprovalAllow.node_type == scope.node_type,
                    ApprovalAllow.operation == scope.operation,
                    ApprovalAllow.scope_kind == scope.kind,
                    ApprovalAllow.scope_value == scope.value,
                )
            )
            existing = result.scalars().first()
        if existing is None:
            raise
        return existing


async def list_for_workflow(database: Any, workflow_id: str) -> List[ApprovalAllow]:
    async with database.get_session() as session:
        result = await session.execute(
            select(ApprovalAllow).where(ApprovalAllow.workflow_id == workflow_id).order_by(ApprovalAllow.created_at)
        )
        return list(result.scalars().all())


async def remove(database: Any, workflow_id: str, allow_id: str) -> bool:
    """Remove a rule; it no longer applies from the next call."""
    async with database.get_session() as session:
        result = await session.execute(delete(ApprovalAllow).where(ApprovalAllow.workflow_id == workflow_id, ApprovalAllow.id == allow_id))
        await session.commit()
    return bool(result.rowcount)


async def delete_for_workflow(database: Any, workflow_id: str) -> int:
    async with database.get_session() as session:
        result = await session.execute(delete(ApprovalAllow).where(ApprovalAllow.workflow_id == workflow_id))
        await session.commit()
    return int(result.rowcount or 0)


async def money_options(database: Any, workflow_id: str) -> List[Dict[str, str]]:
    """The employee's money tools and operations, for its list's switches
    (D19): ``{node_type, operation, label}``, operation "" for a tool that
    is money whole."""
    from services.approvals.classes import CLASSES, CLASSES_WHEN, MONEY
    from services.node_registry import iter_tool_node_classes
    from services.plugin.approval import approval_spec

    tools = dict(iter_tool_node_classes())
    workflow = await database.get_workflow(workflow_id)
    graph = (workflow.data if workflow is not None else None) or {}
    options: List[Dict[str, str]] = []
    seen = set()
    for node in graph.get("nodes") or []:
        node_type = str(node.get("type") or "") if isinstance(node, Mapping) else ""
        node_cls = tools.get(node_type)
        if node_cls is None or node_type in seen:
            continue
        seen.add(node_type)
        name = _name(node_cls, approval_spec(node_cls))
        if CLASSES.get(node_type) == MONEY:
            options.append({"node_type": node_type, "operation": "", "label": name})
        for value, klass in CLASSES_WHEN.get(node_type, ("", {}))[1].items():
            if klass == MONEY:
                options.append({"node_type": node_type, "operation": value, "label": f"{name}: {value}"})
    return options


def serialize(row: ApprovalAllow) -> Dict[str, Any]:
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "node_type": row.node_type,
        "operation": row.operation,
        "scope_kind": row.scope_kind,
        "scope_value": row.scope_value,
        "label": row.label,
        "created_at": created.isoformat(),
    }


__all__ = [
    "ASK_SWITCHES",
    "Scope",
    "add",
    "allowed",
    "delete_for_workflow",
    "list_for_workflow",
    "money_options",
    "offer",
    "remove",
    "scope_of",
    "serialize",
]
