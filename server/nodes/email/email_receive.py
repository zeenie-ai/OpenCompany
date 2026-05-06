"""Email Receive — Wave 11.C migration (polling trigger).

IMAP polling for new mail via Himalaya CLI. Thin delegation to
``handle_email_receive`` which owns the poll loop + baseline tracking.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode


class EmailReceiveParams(BaseModel):
    provider: Literal[
        "gmail", "outlook", "yahoo", "icloud",
        "protonmail", "fastmail", "custom",
    ] = "gmail"
    folder: str = Field(default="INBOX")
    poll_interval: int = Field(default=60, ge=30, le=3600)
    filter_query: str = Field(default="")
    mark_as_read: bool = False

    model_config = ConfigDict(extra="ignore")


class EmailReceiveOutput(BaseModel):
    message_id: Optional[str] = None
    subject: Optional[str] = None
    from_: Optional[str] = Field(default=None)
    body: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class EmailReceiveNode(TriggerNode):
    type = "emailReceive"
    display_name = "Email Receive"
    subtitle = "IMAP Polling"
    group = ("email", "trigger")
    description = "Polling trigger for new emails via IMAP"
    component_kind = "trigger"
    # Wave 11.I, milestone K: ``event_type`` ClassVar lets
    # ``event_waiter._auto_populate_from_plugins`` backfill
    # TRIGGER_REGISTRY without a hardcoded entry in event_waiter.
    event_type = "email_received"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    task_queue = TaskQueue.TRIGGERS_POLL
    mode = "polling"

    Params = EmailReceiveParams
    Output = EmailReceiveOutput

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        """Polling-trigger body inlined from handlers/email.py (Wave 11.D.1).

        Establishes a baseline set of seen message IDs, then polls the
        IMAP folder at the configured interval. Returns the first new
        email that arrives; also dispatches an ``email_received`` event
        so deployment-mode listeners fire.
        """
        import asyncio
        import time
        from datetime import datetime
        from ._service import get_email_service
        from services.status_broadcaster import get_status_broadcaster
        from services import event_waiter

        start_time = time.time()
        raw_params = parameters
        try:
            svc = get_email_service()
            creds = await svc.resolve_credentials(raw_params)
            poll = svc.resolve_poll_params(raw_params)

            await get_status_broadcaster().update_node_status(
                node_id, "waiting",
                {
                    "message": f"Waiting for email (every {poll['interval']}s)...",
                    "event_type": "email_received",
                },
                workflow_id=context.workflow_id,
            )

            seen = await svc.poll_ids(creds, poll["folder"])
            from core.logging import get_logger
            get_logger(__name__).info(
                f"[EmailReceive] Baseline: {len(seen)} emails in {poll['folder']}",
            )

            while True:
                await asyncio.sleep(poll["interval"])
                new_ids = await svc.poll_ids(creds, poll["folder"]) - seen
                if not new_ids:
                    continue

                msg_id = next(iter(new_ids))
                seen.update(new_ids)
                email_data = await svc.fetch_detail(creds, msg_id, poll["folder"])

                if poll["mark_as_read"]:
                    d = svc.defaults
                    await svc.himalaya.flag_message(
                        creds, msg_id, d.get("flag"), d.get("flag_action"),
                        poll["folder"],
                    )

                event_waiter.dispatch("email_received", email_data)
                return {
                    "success": True, "node_id": node_id, "node_type": self.type,
                    "result": email_data,
                    "execution_time": time.time() - start_time,
                    "timestamp": datetime.now().isoformat(),
                }

        except asyncio.CancelledError:
            return {
                "success": False, "node_id": node_id, "node_type": self.type,
                "error": "Cancelled by user",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            from core.logging import get_logger
            get_logger(__name__).error(f"[EmailReceive] {e}")
            return {
                "success": False, "node_id": node_id, "node_type": self.type,
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: EmailReceiveParams) -> EmailReceiveOutput:
        raise NotImplementedError("Polling trigger uses execute() override")
