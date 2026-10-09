"""Which tool calls send, and how a card shows them: a plugin's ``approval``.

A plugin whose tool reaches someone outside (a message, an email, an invite,
a share) declares an :class:`ApprovalSpec` on its class. When the workflow
asks first (services/approvals/rules.py), an agent's call of that tool is
held as a draft for the owner instead of running (services/approvals/
tool_calls.py); the spec says which calls count, what the card shows and
which arguments the owner may edit. Two other answers exist for tools that
cannot wait: ``refuse_while_asking`` (Stripe: refused while Ask first is on)
and ``restrict_while_asking`` (the browser: runs read-only).

The spec reads the call's arguments over the node's settings (``data``), the
dict the node runs with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

Predicate = Callable[[Mapping[str, Any]], bool]

_MAX_DETAIL = 300

#: What a card says once a message went, and when it did not.
MESSAGE_OUTCOME = ("Message sent", "Message not sent")


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return ""
    return str(value).strip()


@dataclass(frozen=True)
class ApprovalSpec:
    #: The app it goes out through ("WhatsApp").
    channel: str
    #: What the card says it does ("Send a WhatsApp message").
    action: str
    #: Which calls send: values of ``operation_field``; None means every call.
    operations: Optional[FrozenSet[str]] = None
    operation_field: str = "operation"
    #: A further test: the call sends only when this returns True. A test
    #: that raises counts as sending (held, never let through by mistake).
    when: Optional[Predicate] = None
    #: Arguments naming who it goes to; the first with a value wins.
    recipient: Tuple[str, ...] = ()
    #: Who it goes to when none of those has a value ("the sender").
    recipient_otherwise: str = ""
    #: The call goes to the owner themself.
    to_owner: Optional[Predicate] = None
    #: Arguments holding the message, the first with a value wins; the
    #: owner may edit it on the card.
    body: Tuple[str, ...] = ()
    #: The argument holding a subject line, editable too.
    subject: Optional[str] = None
    #: Other lines on the card: (label, argument).
    details: Tuple[Tuple[str, str], ...] = ()
    #: The longest edit the channel accepts.
    max_length: int = 20000
    #: Never runs while Ask first is on: the call is refused, saying so.
    refuse_while_asking: bool = False
    #: While Ask first is on, runs with these settings instead of being held.
    restrict_while_asking: Mapping[str, Any] = field(default_factory=dict)
    #: What the card says once the call went, and when it did not.
    outcome_labels: Tuple[str, str] = MESSAGE_OUTCOME

    def sends(self, data: Mapping[str, Any]) -> bool:
        """Whether this call reaches someone (and so waits for the owner)."""
        if self.operations is not None and str(data.get(self.operation_field) or "") not in self.operations:
            return False
        if self.when is not None:
            try:
                return bool(self.when(data))
            except Exception:
                return True
        return True

    def body_field(self, data: Mapping[str, Any]) -> Optional[str]:
        for name in self.body:
            if _text(data.get(name)):
                return name
        return self.body[0] if self.body else None

    def preview(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        """What the card shows: ``{channel, action, recipient,
        recipient_label, body, body_field, subject?, subject_field?,
        details: [{label, value}], max_length}``."""
        recipient = next((_text(data.get(name)) for name in self.recipient if _text(data.get(name))), "")
        owner = False
        if self.to_owner is not None:
            try:
                owner = bool(self.to_owner(data))
            except Exception:
                owner = False
        label = "you" if owner else (recipient or self.recipient_otherwise)
        body_field = self.body_field(data)
        out: Dict[str, Any] = {
            "channel": self.channel,
            "action": self.action,
            "recipient": "" if owner else recipient,
            "recipient_label": label,
            "body": _text(data.get(body_field)) if body_field else "",
            "body_field": body_field,
            "max_length": self.max_length,
        }
        if self.subject:
            out["subject_field"] = self.subject
            subject = _text(data.get(self.subject))
            if subject:
                out["subject"] = subject
        details: List[Dict[str, str]] = []
        for label_text, name in self.details:
            value = _text(data.get(name))
            if value:
                details.append({"label": label_text, "value": value[:_MAX_DETAIL]})
        out["details"] = details
        return out


def approval_spec(node_cls: Any) -> Optional[ApprovalSpec]:
    spec = getattr(node_cls, "approval", None) if node_cls is not None else None
    return spec if isinstance(spec, ApprovalSpec) else None


__all__ = ["ApprovalSpec", "MESSAGE_OUTCOME", "approval_spec"]
