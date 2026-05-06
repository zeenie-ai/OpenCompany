"""Gmail Receive — Wave 11.D.5 fully inlined (polling trigger)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import (
    NodeContext, Operation, PollingTriggerNode, TaskQueue,
)

from ._credentials import GoogleCredential

from ._base import build_google_service, track_google_usage
from ._gmail import fetch_email_details, mark_email_as_read, poll_gmail_ids

logger = get_logger(__name__)


class GmailReceiveParams(BaseModel):
    filter_query: str = Field(
        default="is:unread",
        description="Gmail search query (e.g. 'is:unread from:boss@co.com')",
    )
    label_filter: str = Field(
        default="INBOX",
        description="Label to filter by (or 'all' to disable)",
        json_schema_extra={"loadOptionsMethod": "gmailLabels"},
    )
    mark_as_read: bool = False
    poll_interval: int = Field(default=60, ge=10, le=3600)

    model_config = ConfigDict(extra="ignore")


class GmailReceiveOutput(BaseModel):
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    from_: Optional[str] = Field(default=None)
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    snippet: Optional[str] = None
    date: Optional[str] = None
    labels: Optional[list] = None
    attachments: Optional[list] = None
    is_unread: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class GmailReceiveNode(PollingTriggerNode):
    type = "googleGmailReceive"
    # Pre-existing alias used by deployment manager + POLLING_TRIGGER_TYPES;
    # registers the deployment-mode poll factory under both names so the
    # mismatch can be retired in a separate rename commit.
    type_alias = "gmailReceive"
    display_name = "Gmail Receive"
    subtitle = "Inbound Email"
    group = ("google", "trigger")
    description = "Polling trigger for incoming Gmail emails"
    component_kind = "trigger"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    credentials = (GoogleCredential,)
    task_queue = TaskQueue.TRIGGERS_POLL
    default_poll_interval = 60

    Params = GmailReceiveParams
    Output = GmailReceiveOutput

    # ---- PollingTriggerNode hooks (deployment-mode loop) -------------

    @staticmethod
    def _build_query(parameters: Dict[str, Any]) -> str:
        query = parameters.get("filter_query", "is:unread")
        label = parameters.get("label_filter", "INBOX")
        return f"label:{label} {query}" if label and label != "all" else query

    async def setup_service(self, params: Dict[str, Any]) -> Any:
        return await build_google_service("gmail", "v1", params, {})

    async def fetch_ids(self, service: Any, params: Dict[str, Any]) -> Set[str]:
        return await poll_gmail_ids(service, self._build_query(params))

    async def fetch_detail(
        self, service: Any, msg_id: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await fetch_email_details(service, msg_id)

    async def post_emit(
        self, service: Any, msg_id: str, params: Dict[str, Any]
    ) -> None:
        if params.get("mark_as_read"):
            try:
                await mark_email_as_read(service, msg_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[GmailReceive] Failed to mark as read: {exc}")

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        """Polling loop: baseline existing IDs, sleep, fetch new ones, dispatch."""
        from services import event_waiter
        from services.status_broadcaster import get_status_broadcaster

        start_time = time.time()
        try:
            svc = await build_google_service("gmail", "v1", parameters, context.raw)
            poll_interval = max(10, min(3600, parameters.get("poll_interval", 60)))
            filter_query = parameters.get("filter_query", "is:unread")
            label_filter = parameters.get("label_filter", "INBOX")
            mark_read = parameters.get("mark_as_read", False)

            query = filter_query
            if label_filter and label_filter != "all":
                query = f"label:{label_filter} {query}"

            await get_status_broadcaster().update_node_status(
                node_id, "waiting",
                {
                    "message": f"Waiting for Gmail email (polling every {poll_interval}s)...",
                    "event_type": "gmail_email_received",
                },
                workflow_id=context.workflow_id,
            )

            seen_ids: Set[str] = set()
            try:
                seen_ids.update(await poll_gmail_ids(svc, query))
                logger.info(
                    f"[GmailReceive] Baseline: {len(seen_ids)} existing emails for query '{query}'",
                )
            except Exception as e:
                logger.warning(f"[GmailReceive] Baseline fetch failed (treating all as new): {e}")

            while True:
                await asyncio.sleep(poll_interval)
                try:
                    current_ids = await poll_gmail_ids(svc, query)
                    new_ids = current_ids - seen_ids
                    if not new_ids:
                        continue

                    newest_id = next(iter(new_ids))
                    seen_ids.update(new_ids)
                    email_data = await fetch_email_details(svc, newest_id)

                    if mark_read:
                        try:
                            await mark_email_as_read(svc, newest_id)
                        except Exception as e:
                            logger.warning(f"[GmailReceive] Failed to mark as read: {e}")

                    await track_google_usage(
                        "gmail", node_id, "receive", 1,
                        {"workflow_id": context.workflow_id, "session_id": context.session_id},
                    )
                    event_waiter.dispatch("gmail_email_received", email_data)
                    logger.info(
                        f"[GmailReceive] New email: {email_data.get('subject', 'no subject')}",
                    )
                    return {
                        "success": True, "node_id": node_id, "node_type": self.type,
                        "result": email_data,
                        "execution_time": time.time() - start_time,
                        "timestamp": datetime.now().isoformat(),
                    }
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[GmailReceive] Poll error (will retry): {e}")

        except asyncio.CancelledError:
            logger.info(f"[GmailReceive] Cancelled by user: node_id={node_id}")
            return {
                "success": False, "node_id": node_id, "node_type": self.type,
                "error": "Cancelled by user",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"[GmailReceive] Error: {e}")
            return {
                "success": False, "node_id": node_id, "node_type": self.type,
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: GmailReceiveParams) -> GmailReceiveOutput:
        raise NotImplementedError(
            "gmailReceive uses execute() override (Wave 11.D.5 inlined)."
        )
