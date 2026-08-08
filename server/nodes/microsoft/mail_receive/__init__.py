"""Outlook Mail Receive — Microsoft Graph polling trigger.

Fires when a new message matching the filter arrives in the mailbox.
Mirrors ``googleGmailReceive``: the ``PollingTriggerNode`` base owns the
loop, seen-id baseline, and the per-cycle Temporal poll activity; this
class supplies the four Graph-specific hooks plus an ``execute()``
override for the inline canvas-Run path.

Because the polling hooks run without a ``NodeContext`` (deployment loop
and Temporal activity), they authenticate through the ctx-free
``graph_get_raw`` / ``mark_message_read_raw`` helpers in ``.._base``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import NodeContext, Operation, PollingTriggerNode, TaskQueue

from .._base import graph_get_raw, mailbox_base, mark_message_read_raw, track_microsoft_usage
from .._credentials import MicrosoftCredential

logger = get_logger(__name__)

# Fields fetched per message. bodyPreview keeps the payload small; the
# full body is included so downstream nodes have it without a second call.
_SELECT = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,isRead,hasAttachments,webLink,conversationId"


class MailReceiveParams(BaseModel):
    mailbox: str = Field(
        default="",
        description="Shared mailbox address to watch (empty = your own mailbox). Requires Full Access + Mail.ReadWrite.Shared.",
    )
    only_unread: bool = Field(
        default=True,
        description="Only fire for unread messages (Graph $filter=isRead eq false).",
    )
    from_filter: str = Field(
        default="",
        description="Only fire for messages from this sender address (optional).",
    )
    folder: str = Field(
        default="inbox",
        description="Mail folder well-known name (e.g. inbox, sentitems) or folder id.",
    )
    mark_as_read: bool = Field(default=False, description="Mark the message read after firing.")
    poll_interval: int = Field(default=60, ge=10, le=3600)

    model_config = ConfigDict(extra="ignore")


class MailReceiveOutput(BaseModel):
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    from_: Optional[str] = Field(default=None)
    from_name: Optional[str] = None
    to: Optional[list] = None
    subject: Optional[str] = None
    body_preview: Optional[str] = None
    body: Optional[str] = None
    received: Optional[str] = None
    is_read: Optional[bool] = None
    has_attachments: Optional[bool] = None
    web_link: Optional[str] = None

    model_config = ConfigDict(extra="allow")


def _summarize(msg: Dict[str, Any]) -> Dict[str, Any]:
    sender = (msg.get("from") or {}).get("emailAddress") or {}
    return {
        "message_id": msg.get("id"),
        "conversation_id": msg.get("conversationId"),
        "from": sender.get("address", ""),
        "from_name": sender.get("name", ""),
        "to": [(r.get("emailAddress") or {}).get("address", "") for r in msg.get("toRecipients", [])],
        "subject": msg.get("subject", ""),
        "body_preview": msg.get("bodyPreview", ""),
        "body": (msg.get("body") or {}).get("content", ""),
        "received": msg.get("receivedDateTime"),
        "is_read": msg.get("isRead"),
        "has_attachments": msg.get("hasAttachments"),
        "web_link": msg.get("webLink"),
    }


def _list_path(params: Dict[str, Any]) -> str:
    folder = (params.get("folder") or "inbox").strip()
    # Well-known folder names and folder ids both slot into mailFolders/{id}.
    # mailbox_base() -> /me or /users/{shared-address}.
    return f"{mailbox_base(params.get('mailbox'))}/mailFolders/{folder}/messages"


def _query(params: Dict[str, Any]) -> Dict[str, Any]:
    filters = []
    if params.get("only_unread", True):
        filters.append("isRead eq false")
    sender = (params.get("from_filter") or "").strip()
    if sender:
        # OData string literals use single quotes; double any embedded quote.
        safe = sender.replace("'", "''")
        filters.append(f"from/emailAddress/address eq '{safe}'")
    q: Dict[str, Any] = {
        "$select": _SELECT,
        "$top": 25,
    }
    if filters:
        # Graph rejects ($filter on isRead/from) + ($orderby on a DIFFERENT
        # property, receivedDateTime) with 400 InefficientFilter unless the
        # advanced-query header is set. The trigger dedups by seen-ids and
        # doesn't depend on ordering, so we simply drop $orderby whenever a
        # filter is present. Without a filter we keep newest-first ordering.
        q["$filter"] = " and ".join(filters)
    else:
        q["$orderby"] = "receivedDateTime desc"
    return q


class MailReceiveNode(PollingTriggerNode):
    type = "msMailReceive"
    event_type = "ms_mail_received"
    display_name = "Outlook Mail Receive"
    subtitle = "Inbound Email"
    group = ("microsoft", "trigger")
    description = "Polling trigger for incoming Outlook mail via Microsoft Graph"
    component_kind = "trigger"
    handles = ({"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},)
    credentials = (MicrosoftCredential,)
    task_queue = TaskQueue.TRIGGERS_POLL
    default_poll_interval = 60

    Params = MailReceiveParams
    Output = MailReceiveOutput

    # ---- PollingTriggerNode hooks (deployment / Temporal poll) --------

    async def setup_service(self, params: Dict[str, Any]) -> Any:
        # No long-lived handle: Graph is stateless bearer REST and each
        # helper re-resolves a fresh token. Pass params through so the
        # other hooks can read filters without extra plumbing.
        return params

    async def fetch_ids(self, service: Any, params: Dict[str, Any]) -> Set[str]:
        data = await graph_get_raw(_list_path(params), params=_query(params))
        return {m.get("id") for m in data.get("value", []) if m.get("id")}

    async def fetch_detail(self, service: Any, msg_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        msg = await graph_get_raw(f"{mailbox_base(params.get('mailbox'))}/messages/{msg_id}", params={"$select": _SELECT})
        detail = _summarize(msg)
        detail["id"] = msg_id  # stable cross-cycle dedup key for the workflow
        return detail

    async def post_emit(self, service: Any, msg_id: str, params: Dict[str, Any]) -> None:
        if params.get("mark_as_read"):
            try:
                await mark_message_read_raw(msg_id, mailbox=params.get("mailbox"))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[msMailReceive] Failed to mark as read: {exc}")

    # ---- inline canvas-Run path (mirrors gmail_receive.execute) -------

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        from services.status_broadcaster import get_status_broadcaster

        start_time = time.time()
        try:
            poll_interval = self._clamp_interval(parameters.get("poll_interval"))

            await get_status_broadcaster().update_node_status(
                node_id,
                "waiting",
                {
                    "message": f"Waiting for Outlook mail (polling every {poll_interval}s)...",
                    "event_type": self.event_type,
                },
                workflow_id=context.workflow_id,
            )

            seen_ids: Set[str] = set()
            try:
                seen_ids = await self.fetch_ids(parameters, parameters)
                logger.info(f"[msMailReceive] Baseline: {len(seen_ids)} existing messages")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[msMailReceive] Baseline fetch failed (treating all as new): {e}")

            while True:
                await asyncio.sleep(poll_interval)
                try:
                    current_ids = await self.fetch_ids(parameters, parameters)
                    new_ids = current_ids - seen_ids
                    if not new_ids:
                        seen_ids = set(current_ids)
                        continue

                    newest_id = next(iter(new_ids))
                    seen_ids = set(current_ids)
                    detail = await self.fetch_detail(parameters, newest_id, parameters)
                    await self.post_emit(parameters, newest_id, parameters)

                    await track_microsoft_usage(
                        node_id,
                        "read",
                        1,
                        {"workflow_id": context.workflow_id, "session_id": context.session_id},
                    )
                    logger.info(f"[msMailReceive] New message: {detail.get('subject', 'no subject')}")
                    return {
                        "success": True,
                        "node_id": node_id,
                        "node_type": self.type,
                        "result": detail,
                        "execution_time": time.time() - start_time,
                        "timestamp": datetime.now().isoformat(),
                    }
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 -- per-cycle isolation
                    logger.error(f"[msMailReceive] Poll error (will retry): {e}")

        except asyncio.CancelledError:
            logger.info(f"[msMailReceive] Cancelled by user: node_id={node_id}")
            return {
                "success": False,
                "node_id": node_id,
                "node_type": self.type,
                "error": "Cancelled by user",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"[msMailReceive] Error: {e}")
            return {
                "success": False,
                "node_id": node_id,
                "node_type": self.type,
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: MailReceiveParams) -> MailReceiveOutput:
        raise NotImplementedError("msMailReceive uses execute() override.")
