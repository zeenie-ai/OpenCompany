"""The employee's answer, streamed into the chat as it is written.

One :class:`ChatStreamEmitter` per LLM step of the agent that answers a chat
run (``chat_stream`` in the step's payload, set by ``agent.prepare_payload``
for the agent whose output reaches a reply node). It turns the provider's
:class:`~services.llm.protocol.StreamEvent` deltas into the run's text
events (docs-internal/chat_protocol.md):

- **One segment per attempt.** The segment id is ``<run>.<iteration>.<attempt>``.
  A retried step first discards the segments its earlier attempts streamed
  (``custom`` ``opencompany.segment_discarded``), so a client never shows
  text twice.
- **Batched.** Deltas go out every ``stream.flush_ms`` or ``stream.flush_chars``
  (``config/chat_defaults.json``), and whatever is left when the step ends.
- **Held back.** Text that could still turn out to be ``NO_REPLY`` (the agent
  had nothing to say, so the reply node posts nothing) is not shown until it
  stops matching; a ``<followups>`` block, and anything after it, never
  streams (the server reads it when the run ends).
- **Reply or narration.** ``text.ended`` says ``final: true`` when the step
  answered, ``final: false`` when tool calls followed (narration beside them).

Publishing never fails the step: the hub drops what it cannot deliver, and a
problem here is logged, not raised.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from core.logging import get_logger
from services.approvals.contract import NO_REPLY
from services.chat.config import stream_setting
from services.llm.protocol import StreamEvent

logger = get_logger(__name__)

#: Opens the block the agent ends a reply with to offer follow-up questions.
FOLLOWUPS_OPEN = "<followups>"

Publish = Callable[..., Any]


def _default_publish(**kwargs: Any) -> Any:
    from services.chat.hub import publish_run_event

    return publish_run_event(**kwargs)


def segment_id(run_id: str, iteration: int, attempt: int) -> str:
    return f"{run_id}.{iteration}.{attempt}"


def chat_run_id_of(context: Mapping[str, Any]) -> Optional[str]:
    """The chat run a node works for (its ``run_scope``), or None."""
    scope = context.get("run_scope")
    run_id = scope.get("run_id") if isinstance(scope, Mapping) else None
    return run_id if isinstance(run_id, str) and run_id else None


def chat_stream_for(context: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Where an agent's text streams, or None when it does not answer a chat
    run.

    An agent answers the run when the run's scope reached it (MachinaWorkflow
    puts ``run_scope`` on every node of the run), it is not working for
    another agent (a delegated child carries ``parent_node_id``), and its
    output goes straight to a node that posts the run's answer
    (``answers_chat_run``, Reply in Chat). Decided by the plugin attribute,
    never by a node type name.
    """
    scope = context.get("run_scope")
    if not isinstance(scope, Mapping) or context.get("parent_node_id"):
        return None
    run_id = chat_run_id_of(context)
    session_id = scope.get("session_id")
    if run_id is None or not isinstance(session_id, str) or not session_id:
        return None
    node_id = context.get("node_id")
    nodes = {str(node.get("id")): node for node in context.get("nodes") or [] if isinstance(node, Mapping)}
    from services.chat.ledger import reply_uid
    from services.node_registry import get_node_class

    for edge in context.get("edges") or []:
        if not isinstance(edge, Mapping) or edge.get("source") != node_id:
            continue
        target = nodes.get(str(edge.get("target")))
        node_class = get_node_class(str(target.get("type") or "")) if target else None
        if node_class is not None and getattr(node_class, "answers_chat_run", False):
            workflow_id = context.get("workflow_id")
            return {
                "run_id": run_id,
                "session_id": session_id,
                "workflow_id": workflow_id if isinstance(workflow_id, str) and workflow_id else session_id,
                "reply_message_id": reply_uid(run_id),
            }
    return None


def shown_text(text: str) -> str:
    """The part of ``text`` a client may show: nothing while it could still
    be ``NO_REPLY``, and nothing from a ``<followups>`` block on (nor a tag
    that is still arriving)."""
    stripped = text.lstrip()
    if stripped and NO_REPLY.startswith(stripped):
        return ""
    cut = text.find(FOLLOWUPS_OPEN)
    if cut != -1:
        return text[:cut]
    # A tail that could still become the tag waits for the next delta.
    for length in range(min(len(FOLLOWUPS_OPEN) - 1, len(text)), 0, -1):
        if text.endswith(FOLLOWUPS_OPEN[:length]):
            return text[:-length]
    return text


class ChatStreamEmitter:
    """Streams one step's text to a chat run's subscribers."""

    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        workflow_id: Optional[str],
        segment: str,
        discarded: List[str],
        reply_message_id: Optional[str],
        flush_ms: Optional[float] = None,
        flush_chars: Optional[int] = None,
        publish: Publish = _default_publish,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.workflow_id = workflow_id
        self.segment = segment
        self.reply_message_id = reply_message_id
        self._discarded = list(discarded)
        self._flush_s = (flush_ms if flush_ms is not None else stream_setting("flush_ms")) / 1000.0
        self._flush_chars = int(flush_chars if flush_chars is not None else stream_setting("flush_chars"))
        self._publish = publish
        self._clock = clock
        self._text = ""
        self._published = ""
        self._opened = False
        self._discarded_current = False
        self._last_flush = clock()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, attempt: int, **kwargs: Any) -> Optional["ChatStreamEmitter"]:
        """The emitter for an LLM step's payload, or None when the step does
        not answer a chat run."""
        stream = payload.get("chat_stream")
        if not isinstance(stream, Mapping):
            return None
        run_id = stream.get("run_id")
        session_id = stream.get("session_id")
        if not isinstance(run_id, str) or not run_id or not isinstance(session_id, str) or not session_id:
            return None
        iteration = int(payload.get("iteration") or 0)
        attempt = max(1, int(attempt))
        return cls(
            run_id=run_id,
            session_id=session_id,
            workflow_id=stream.get("workflow_id") if isinstance(stream.get("workflow_id"), str) else None,
            segment=segment_id(run_id, iteration, attempt),
            discarded=[segment_id(run_id, iteration, earlier) for earlier in range(1, attempt)],
            reply_message_id=stream.get("reply_message_id") if isinstance(stream.get("reply_message_id"), str) else None,
            **kwargs,
        )

    # ---- publishing ------------------------------------------------------

    def _send(self, suffix: str, fields: Dict[str, Any], event_key: Optional[str] = None) -> None:
        try:
            self._publish(
                run_id=self.run_id,
                session_id=self.session_id,
                workflow_id=self.workflow_id,
                suffix=suffix,
                fields=fields,
                event_key=event_key,
            )
        except Exception:  # noqa: BLE001 - streaming is a courtesy; the answer is saved anyway
            logger.warning("Chat stream event could not be published", run_id=self.run_id, suffix=suffix, exc_info=True)

    def begin(self) -> None:
        """Withdraw what earlier attempts of this step streamed."""
        for segment in self._discarded:
            self._send(
                "custom",
                {"name": "opencompany.segment_discarded", "value": {"message_id": segment}},
                event_key=f"discard:{segment}",
            )

    def _flush(self) -> None:
        visible = shown_text(self._text)
        if len(visible) <= len(self._published) or not visible.startswith(self._published):
            return
        delta = visible[len(self._published):]
        if not self._opened:
            if not visible.strip():
                return
            self._opened = True
            self._send("text.started", {"message_id": self.segment, "role": "assistant"}, event_key=f"start:{self.segment}")
        self._published = visible
        self._last_flush = self._clock()
        self._send("text.content", {"message_id": self.segment, "delta": delta})

    async def __call__(self, event: StreamEvent) -> None:
        if self._discarded_current or event.kind != "text" or not event.delta:
            return
        self._text += event.delta
        pending = len(self._text) - len(self._published)
        if pending >= self._flush_chars or self._clock() - self._last_flush >= self._flush_s:
            self._flush()

    def end(self, *, final: bool) -> None:
        """The step finished: send what is left and close the segment."""
        if self._discarded_current:
            return
        self._flush()
        if not self._opened:
            return
        fields: Dict[str, Any] = {"message_id": self.segment, "final": bool(final)}
        if final and self.reply_message_id:
            fields["reply_message_id"] = self.reply_message_id
        self._send("text.ended", fields, event_key=f"end:{self.segment}")

    def discard(self) -> None:
        """Withdraw a failed attempt immediately, including during backoff."""
        if self._discarded_current:
            return
        self._discarded_current = True
        if self._opened:
            self._send(
                "custom",
                {"name": "opencompany.segment_discarded", "value": {"message_id": self.segment}},
                event_key=f"discard:{self.segment}",
            )

    @property
    def text(self) -> str:
        """Everything the provider wrote so far, held-back parts included."""
        return self._text

    @property
    def published(self) -> str:
        return self._published


__all__ = ["ChatStreamEmitter", "FOLLOWUPS_OPEN", "chat_run_id_of", "chat_stream_for", "segment_id", "shown_text"]
