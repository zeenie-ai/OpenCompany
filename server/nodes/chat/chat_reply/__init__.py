"""Reply in Chat: an answer into the workflow's chat thread.

A sink wired after an agent: it posts ``message`` (a template, usually
``{{<agent label>.response}}``) to the thread of the workflow it runs in, as
the assistant. The owner reads it in Talk on the employee's Home page and
in the editor's chat pane. The write goes through services/chat_thread.py,
which stamps the live generation and announces ``chat.updated``.

With ``message`` empty it posts what the node's input answered: the
connected agent's ``response`` (``connected_outputs``, which NodeExecutor
gives this node), so an agent wired in the editor needs no template. An
answer that is empty, or exactly NO_REPLY (the agent had nothing to say),
posts nothing. A ``<followups>`` block the answer ends with is never
shown as text: in a chat run it becomes the reply's follow-up buttons
(``services/chat/guide.py``), elsewhere it is dropped.

In a run the owner's chat message started, the reply is that chat run's
answer (``run_scope``, set by MachinaWorkflow): it is saved under the run's
reply id through ``services/chat/ledger.py``, so a retried step saves it
once, and the run reports it when it finishes. A run whose conversation was
reset or cleared meanwhile posts nothing: its answer belongs to a thread
that no longer exists.

The thread ends with the workflow's generation: a Reset (every restart)
clears it through ``reset_execution_state``, as the Context node forgets
the conversation in the same Reset, and deleting the workflow deletes it
(the workflow-deleted hook registered below).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.approvals.contract import NO_REPLY
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class ChatReplyParams(BaseModel):
    message: str = Field(
        default="",
        description="What to post. Empty posts what the connected agent answered.",
        json_schema_extra={"rows": 3, "placeholder": "{{agent.response}}"},
    )

    model_config = ConfigDict(extra="ignore")

    @field_validator("message", mode="before")
    @classmethod
    def _as_text(cls, value: Any) -> str:
        # A whole-value template keeps its type (parameter_resolver), so an
        # upstream field that is not text arrives as-is.
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)


_ANSWER_FIELDS = ("response", "message", "text", "content")


def _connected_answer(raw: Any) -> str:
    """What the node's input answered: the first connected output's
    ``response`` (an agent's answer), else its ``message``, ``text`` or
    ``content``."""
    outputs = raw.get("connected_outputs") if isinstance(raw, dict) else None
    for output in outputs.values() if isinstance(outputs, dict) else ():
        if not isinstance(output, dict):
            continue
        for field in _ANSWER_FIELDS:
            value = output.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return ""


class ChatReplyOutput(BaseModel):
    #: False when there was nothing to post.
    posted: bool = False
    message: Optional[str] = None
    #: The saved message's id, and the chat run it answers (if any).
    message_id: Optional[str] = None
    run_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatReplyNode(ActionNode):
    type = "chatReply"
    display_name = "Reply in Chat"
    subtitle = "Answer in Talk"
    group = ("chat",)
    description = "Post a message to this workflow's chat thread, where the owner talks to it"
    component_kind = "square"
    handles = ({"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},)
    hide_output_handle = True
    annotations = {"destructive": False, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    # The agent wired into this node answers the chat run: its text streams
    # into the chat as it writes (services/chat/stream.py).
    answers_chat_run = True

    Params = ChatReplyParams
    Output = ChatReplyOutput

    @Operation("reply")
    async def reply(self, ctx: NodeContext, params: ChatReplyParams) -> ChatReplyOutput:
        from services.chat.guide import split_followups
        from services.chat_thread import record_chat_message
        from services.plugin.deps import get_database

        text = params.message.strip() or _connected_answer(ctx.raw).strip()
        # The <followups> block an answer may end with shows as buttons, never
        # as text: a message that is only the block says nothing.
        visible = split_followups(text)[0].strip()
        if not visible or visible == NO_REPLY:
            return ChatReplyOutput(posted=False)
        if not ctx.workflow_id:
            raise NodeUserError("Reply in Chat posts to the workflow's chat: save the workflow first.")
        database = get_database()
        scope = ctx.raw.get("run_scope") if isinstance(ctx.raw, dict) else None
        run_id = scope.get("run_id") if isinstance(scope, dict) else None
        if isinstance(run_id, str) and run_id:
            return await self._reply_to_run(database, ctx, run_id, text)
        saved = await record_chat_message(database, ctx.workflow_id, "assistant", visible)
        if not saved:
            raise RuntimeError("The reply could not be saved to the chat")
        return ChatReplyOutput(posted=True, message=visible, message_id=_message_id(saved))

    async def _reply_to_run(self, database, ctx: NodeContext, run_id: str, text: str) -> ChatReplyOutput:
        from services.chat import ledger
        from services.chat_thread import announce_chat_updated, chat_execution_id

        run = await ledger.get_run(database, run_id)
        if run is None or run.session_id != ctx.workflow_id:
            # Reset or Clear removed the conversation this run answered.
            return ChatReplyOutput(posted=False, run_id=run_id)
        control = await database.get_latest_workflow_control(run.session_id)
        saved = await ledger.post_reply(
            database, run=run, node_id=ctx.node_id, text=text, execution_id=chat_execution_id(control)
        )
        await announce_chat_updated(run.session_id, "assistant")
        return ChatReplyOutput(posted=True, message=saved.get("message", text), message_id=saved.get("uid"), run_id=run_id)

    @classmethod
    async def reset_execution_state(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        execution_id: str,
        generation: int,
        graph: dict,
        database,
    ) -> dict:
        """A Reset clears the workflow's thread: the conversation it holds
        ended with the generation (the Context node forgets it in the same
        Reset), so neither Talk nor the editor's chat pane keeps showing it."""
        del node_id, execution_id, generation, graph
        from services.chat_thread import clear_chat_thread

        cleared = await clear_chat_thread(database, str(workflow_id))
        return {"reset": bool(cleared), "cleared_chat_messages": cleared}


def _message_id(saved: Any) -> Optional[str]:
    return saved.get("uid") if isinstance(saved, dict) else None


# The thread belongs to its workflow: deleting the workflow deletes it.
from services.chat_thread import clear_chat_thread  # noqa: E402
from services.workflow_storage.hooks import register_workflow_deleted_hook  # noqa: E402

register_workflow_deleted_hook(clear_chat_thread)
