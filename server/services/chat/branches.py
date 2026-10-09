"""Branches of a chat: editing the owner's message, trying an answer again,
and moving between versions (docs-internal/chat_protocol.md, "Branches").

A session's messages form a tree: each row names the one before it
(``parent_uid``), and the thread's active leaf (``chat_threads``) is where
the shown path ends. An edit adds the edited message beside the original
(same parent) and starts a run that answers it; a retry starts a new run for
the same message, whose answer goes beside the old one; a switch moves the
leaf into another version's branch, at the newest message on it. Only the
path from the first message to the leaf is shown (``active_path``); the
messages beside each one, with the same parent and role, are its versions
(``sibling_ids``).

**The employee's memory follows the path.** Each run records, per agent
with a stored conversation that works for it, the conversation as the run
began (``ChatRun.context_cursors``: its length and a digest without the
stamps the store adds; ``record_cursor``). Leaving a branch saves the
conversations of the agents that worked on the part being left under that
branch's leaf (``chat_branch_snapshots``) and takes each back to where it
stood before that part: the cursor of the first run on it that touched the
agent, checked against the stored conversation. A conversation that no
longer starts that way (it was summarized or cleared since) refuses the move
(``cannot_rewind``). Moving back onto a branch restores its saved
conversations; one no longer saved refuses the move (``branch_unavailable``).

Every move runs in one reserved write transaction, under the conversation
store's per-agent locks, after checking the thread's revision (a change made
meanwhile refuses it, ``revision_conflict``) and that no run holds the lane
(``run_in_progress``). Messages and runs from before the live generation
cannot be moved (``older_generation``). After it commits, ``after_move``
tells the Context panel about the conversations it changed, cancels the
drafts the runs on the part left made that still wait, and leaves the
employee a note about what those runs sent anyway.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy import delete, func
from sqlmodel import select

from core.logging import get_logger
from models.agent_context import AgentConversation
from models.chat import LIVE_STATES, TERMINAL_STATES, ChatBranchSnapshot, ChatRun, ChatThread
from models.database import ChatMessage
from services.chat.config import branches_setting

logger = get_logger(__name__)

#: Kinds of the owner's message that can be edited (not a button press, a
#: report or a notice).
EDITABLE_KINDS = frozenset({"text"})


class BranchRefused(Exception):
    """A change to the conversation that cannot be made; ``code`` says why."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


# ---- cursors -------------------------------------------------------------


def conversation_digest(messages: Sequence[Any]) -> str:
    """A digest of a stored conversation without the stamps the store adds
    (``ts``), so the same messages read back give the same digest."""
    plain = [{key: value for key, value in dict(message).items() if key != "ts"} for message in messages if isinstance(message, Mapping)]
    data = json.dumps(plain, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


async def record_cursor(
    database: Any, *, run_id: str, agent_node_id: str, generation: int, messages: Sequence[Any]
) -> None:
    """Keep where the agent's stored conversation stood as the run began.
    Only the first time the agent is prepared in the run counts."""
    async with database.reserved_session() as session:
        run = await session.get(ChatRun, run_id)
        if run is None or agent_node_id in (run.context_cursors or {}):
            return
        cursors = dict(run.context_cursors or {})
        cursors[agent_node_id] = {
            "generation": int(generation),
            "length": len(messages),
            "digest": conversation_digest(messages),
        }
        run.context_cursors = cursors
        session.add(run)
        await session.commit()


# ---- the path ------------------------------------------------------------


def active_path(rows: Sequence[Any], leaf_uid: Optional[str]) -> List[Any]:
    """The rows from the first message to ``leaf_uid``, oldest first. A
    session with no leaf recorded (or one gone) ends at its newest row."""
    by_uid = {_field(row, "uid"): row for row in rows if _field(row, "uid")}
    if not by_uid:
        return []
    leaf = by_uid.get(leaf_uid) if leaf_uid else None
    if leaf is None:
        leaf = max(by_uid.values(), key=lambda row: _field(row, "id") or 0)
    path: List[Any] = []
    seen: Set[str] = set()
    current: Any = leaf
    while current is not None and _field(current, "uid") not in seen:
        seen.add(_field(current, "uid"))
        path.append(current)
        parent = _field(current, "parent_uid")
        current = by_uid.get(parent) if parent else None
    path.reverse()
    return path


def sibling_ids(rows: Sequence[Any]) -> Dict[str, List[str]]:
    """Per message, the messages sharing its parent and role, oldest first
    (itself included): its versions."""
    groups: Dict[Tuple[Optional[str], Optional[str]], List[Any]] = {}
    for row in sorted(rows, key=lambda row: _field(row, "id") or 0):
        if _field(row, "uid"):
            groups.setdefault((_field(row, "parent_uid"), _field(row, "role")), []).append(row)
    siblings: Dict[str, List[str]] = {}
    for group in groups.values():
        ids = [_field(row, "uid") for row in group]
        for uid in ids:
            siblings[uid] = ids
    return siblings


def newest_leaf_under(rows: Sequence[Any], uid: str) -> str:
    """The newest message in ``uid``'s branch: always a leaf, since a reply
    is newer than what it follows. The branch a switch moves to ends there."""
    children: Dict[Optional[str], List[Any]] = {}
    for row in rows:
        children.setdefault(_field(row, "parent_uid"), []).append(row)
    by_uid = {_field(row, "uid"): row for row in rows}
    best = by_uid[uid]
    stack, seen = [uid], {uid}
    while stack:
        for child in children.get(stack.pop(), []):
            child_uid = _field(child, "uid")
            if child_uid in seen:
                continue
            seen.add(child_uid)
            if (_field(child, "id") or 0) > (_field(best, "id") or 0):
                best = child
            stack.append(child_uid)
    return _field(best, "uid")


def _branch_rows(rows: Sequence[Any], top_uid: str, leaf_uid: str) -> List[Any]:
    """The rows from ``top_uid`` down to ``leaf_uid``, oldest first."""
    path = active_path(rows, leaf_uid)
    uids = [_field(row, "uid") for row in path]
    return path[uids.index(top_uid):] if top_uid in uids else []


def answering_run_ids(path: Sequence[Any], runs: Sequence[Any]) -> Dict[str, str]:
    """For each of the owner's messages on the path, the run answering it
    there: the run of the answer after it on the path, else its newest run
    (a retry still working), else the run it started."""
    newest: Dict[str, Any] = {}
    for run in runs:
        message_uid = _field(run, "user_message_uid")
        if not message_uid:
            continue
        known = newest.get(message_uid)
        if known is None or (_field(run, "created_at"), _field(run, "run_id")) > (_field(known, "created_at"), _field(known, "run_id")):
            newest[message_uid] = run
    runs_by_id = {_field(run, "run_id"): run for run in runs}
    answering: Dict[str, str] = {}
    for index, row in enumerate(path):
        if _field(row, "role") != "user":
            continue
        uid = _field(row, "uid")
        after = path[index + 1] if index + 1 < len(path) else None
        after_run = runs_by_id.get(_field(after, "run_id")) if after is not None and _field(after, "role") == "assistant" else None
        if after_run is not None and _field(after_run, "user_message_uid") == uid:
            answering[uid] = _field(after_run, "run_id")
        elif uid in newest:
            answering[uid] = _field(newest[uid], "run_id")
        elif _field(row, "run_id"):
            answering[uid] = _field(row, "run_id")
    return answering


def editable_ids(path: Sequence[Any], runs: Sequence[Any], live_root: Optional[str]) -> Set[str]:
    """What the owner may change on the path, in the live generation: their
    text messages a run answered (Edit), and the answer the path ends at,
    once its run has ended (Try again)."""
    if not live_root or not path:
        return set()
    runs_by_id = {_field(run, "run_id"): run for run in runs}
    on_path = {_field(row, "uid") for row in path}
    editable: Set[str] = set()
    for row in path:
        if (
            _field(row, "role") == "user"
            and (_field(row, "kind") or "text") in EDITABLE_KINDS
            and _field(row, "run_id")
            and _field(row, "execution_id") == live_root
        ):
            editable.add(_field(row, "uid"))
    leaf = path[-1]
    run = runs_by_id.get(_field(leaf, "run_id"))
    if (
        _field(leaf, "role") == "assistant"
        and run is not None
        and _field(run, "state") in TERMINAL_STATES
        and _field(run, "run_key") == live_root
        and _field(run, "user_message_uid") in on_path
    ):
        editable.add(_field(leaf, "uid"))
    return editable


# ---- moving --------------------------------------------------------------


@dataclass
class _Shape:
    """What a move changes on the tree."""

    #: The rows of the path it leaves, oldest first.
    leaving: List[ChatMessage] = field(default_factory=list)
    #: Runs taken back though none of their rows is left (a retried run
    #: that never answered).
    extra_run_ids: List[str] = field(default_factory=list)
    #: The leaf of the branch left, the key its conversations are kept under
    #: (None when nothing is left behind to come back to).
    left_leaf: Optional[str] = None
    #: The rows of the branch moved to, oldest first (a switch).
    target: List[ChatMessage] = field(default_factory=list)
    target_leaf: Optional[str] = None


@dataclass
class _Plan:
    rewinds: Dict[str, Dict[str, Any]]
    restores: Dict[str, ChatBranchSnapshot]
    left_run_ids: List[str]

    @property
    def agents(self) -> Tuple[str, ...]:
        return tuple(sorted(set(self.rewinds) | set(self.restores)))


@dataclass
class Moved:
    """A move that committed: what ``after_move`` needs."""

    session_id: str
    workflow_id: str
    generation: int
    #: Conversations rewritten: ``(agent_node_id, message count)``.
    changed: List[Tuple[str, int]]
    #: Runs on the part of the path left behind.
    left_run_ids: List[str]
    #: What the operation wrote (a message and its run, or the new leaf).
    result: Dict[str, Any] = field(default_factory=dict)


def _ordered_runs(rows: Iterable[Any], extra: Iterable[str] = ()) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for run_id in [*extra, *(_field(row, "run_id") for row in rows)]:
        if run_id and run_id not in seen:
            seen.add(run_id)
            ordered.append(run_id)
    return ordered


async def _runs(session: Any, run_ids: Sequence[str]) -> Dict[str, ChatRun]:
    if not run_ids:
        return {}
    result = await session.execute(select(ChatRun).where(ChatRun.run_id.in_(list(run_ids))))
    return {run.run_id: run for run in result.scalars().all()}


async def _plan(session: Any, *, session_id: str, generation: int, shape: _Shape) -> _Plan:
    left_run_ids = _ordered_runs(shape.leaving, shape.extra_run_ids)
    runs = await _runs(session, left_run_ids)
    rewinds: Dict[str, Dict[str, Any]] = {}
    for run_id in left_run_ids:
        run = runs.get(run_id)
        for agent, cursor in ((run.context_cursors or {}) if run is not None else {}).items():
            if agent not in rewinds and isinstance(cursor, dict):
                rewinds[agent] = cursor
    if any(int(cursor.get("generation", -1)) != generation for cursor in rewinds.values()):
        raise BranchRefused("older_generation", "the conversation from before the employee restarted cannot change")
    restores: Dict[str, ChatBranchSnapshot] = {}
    if shape.target_leaf is not None:
        target_runs = await _runs(session, _ordered_runs(shape.target))
        touched = {agent for run in target_runs.values() for agent in (run.context_cursors or {})}
        if touched:
            found = await session.execute(
                select(ChatBranchSnapshot).where(
                    ChatBranchSnapshot.session_id == session_id, ChatBranchSnapshot.leaf_uid == shape.target_leaf
                )
            )
            saved = {snapshot.agent_node_id: snapshot for snapshot in found.scalars().all()}
            for agent in touched:
                snapshot = saved.get(agent)
                if snapshot is None or snapshot.generation != generation:
                    raise BranchRefused("branch_unavailable", "that version of the conversation can no longer be restored")
                restores[agent] = snapshot
    return _Plan(rewinds=rewinds, restores=restores, left_run_ids=left_run_ids)


def _size(messages: Sequence[Any]) -> int:
    return len(json.dumps(list(messages), ensure_ascii=False, default=str).encode("utf-8"))


async def _keep_snapshot(session: Any, *, session_id: str, leaf_uid: str, agent: str, generation: int, messages: List[Any]) -> None:
    found = await session.execute(
        select(ChatBranchSnapshot).where(
            ChatBranchSnapshot.session_id == session_id,
            ChatBranchSnapshot.leaf_uid == leaf_uid,
            ChatBranchSnapshot.agent_node_id == agent,
        )
    )
    snapshot = found.scalar_one_or_none()
    if _size(messages) > branches_setting("max_snapshot_bytes"):
        # Too large to keep: coming back to this branch is refused instead.
        if snapshot is not None:
            await session.delete(snapshot)
        return
    if snapshot is None:
        snapshot = ChatBranchSnapshot(session_id=session_id, leaf_uid=leaf_uid, agent_node_id=agent)
    snapshot.generation = generation
    snapshot.messages = list(messages)
    snapshot.created_at = _utcnow()
    session.add(snapshot)


async def _evict_snapshots(session: Any, session_id: str) -> None:
    await session.flush()
    keep = branches_setting("max_snapshots_per_session")
    total = (
        await session.execute(select(func.count()).select_from(ChatBranchSnapshot).where(ChatBranchSnapshot.session_id == session_id))
    ).scalar() or 0
    if total <= keep:
        return
    oldest = await session.execute(
        select(ChatBranchSnapshot.id)
        .where(ChatBranchSnapshot.session_id == session_id)
        .order_by(ChatBranchSnapshot.created_at, ChatBranchSnapshot.id)
        .limit(total - keep)
    )
    ids = [value for (value,) in oldest.all()]
    if ids:
        await session.execute(delete(ChatBranchSnapshot).where(ChatBranchSnapshot.id.in_(ids)))


async def _apply(session: Any, plan: _Plan, *, session_id: str, workflow_id: str, generation: int, left_leaf: Optional[str]) -> List[Tuple[str, int]]:
    agents = plan.agents
    if not agents:
        return []
    found = await session.execute(
        select(AgentConversation).where(
            AgentConversation.workflow_id == workflow_id,
            AgentConversation.generation == generation,
            AgentConversation.agent_node_id.in_(list(agents)),
        )
    )
    rows = {row.agent_node_id: row for row in found.scalars().all()}
    stored = {agent: list(rows[agent].messages or []) if agent in rows else [] for agent in agents}
    # Every agent taken back must still start the way it did then.
    for agent, cursor in plan.rewinds.items():
        if agent in plan.restores:
            continue
        length = int(cursor.get("length") or 0)
        if len(stored[agent]) < length or conversation_digest(stored[agent][:length]) != cursor.get("digest"):
            raise BranchRefused("cannot_rewind", "the employee has summarized or cleared what came before")
    if left_leaf is not None:
        for agent in plan.rewinds:
            await _keep_snapshot(session, session_id=session_id, leaf_uid=left_leaf, agent=agent, generation=generation, messages=stored[agent])
    changed: List[Tuple[str, int]] = []
    now = _utcnow()
    for agent in agents:
        if agent in plan.restores:
            new = list(plan.restores[agent].messages or [])
        else:
            new = stored[agent][: int(plan.rewinds[agent].get("length") or 0)]
        if new == stored[agent]:
            continue
        row = rows.get(agent) or AgentConversation(workflow_id=workflow_id, generation=generation, agent_node_id=agent)
        row.messages = new
        row.updated_at = now
        session.add(row)
        changed.append((agent, len(new)))
    await _evict_snapshots(session, session_id)
    return changed


async def _session_rows(session: Any, session_id: str) -> List[ChatMessage]:
    result = await session.execute(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id))
    return list(result.scalars().all())


async def _lane_held(session: Any, session_id: str) -> bool:
    result = await session.execute(
        select(ChatRun.run_id).where(ChatRun.session_id == session_id, ChatRun.state.in_(LIVE_STATES)).limit(1)
    )
    return result.scalar_one_or_none() is not None


class _Operation:
    """One kind of move: where it forks (``shape``) and what it writes."""

    async def shape(self, session: Any, rows: List[ChatMessage], path: List[ChatMessage]) -> _Shape:
        raise NotImplementedError

    async def write(self, session: Any, database: Any, thread: ChatThread, rows: List[ChatMessage], path: List[ChatMessage]) -> Dict[str, Any]:
        raise NotImplementedError


def _on_path(path: List[ChatMessage], uid: str) -> int:
    for index, row in enumerate(path):
        if row.uid == uid:
            return index
    raise BranchRefused("not_found", "that message is not in the conversation shown")


def _new_run(
    *,
    session_id: str,
    workflow_id: str,
    live_root: str,
    kind: str,
    state: str,
    message_uid: str,
    parent_run_id: Optional[str],
    options: Dict[str, Any],
) -> ChatRun:
    from services.chat.ledger import new_run_id, reply_uid

    run_id = new_run_id()
    return ChatRun(
        run_id=run_id,
        session_id=session_id,
        workflow_id=workflow_id,
        run_key=live_root,
        kind=kind,
        state=state,
        user_message_uid=message_uid,
        reply_message_uid=reply_uid(run_id),
        parent_run_id=parent_run_id,
        options=dict(options),
    )


@dataclass
class _Edit(_Operation):
    session_id: str
    workflow_id: str
    live_root: str
    message_uid: str
    text: str
    state: str
    uid: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    #: The choices sent with the edit (``ChatRun.options``).
    options: Dict[str, Any] = field(default_factory=dict)

    async def shape(self, session: Any, rows: List[ChatMessage], path: List[ChatMessage]) -> _Shape:
        index = _on_path(path, self.message_uid)
        row = path[index]
        if row.role != "user" or (row.kind or "text") not in EDITABLE_KINDS or not row.run_id:
            raise BranchRefused("not_editable", "only your own messages the employee answered can be edited")
        if row.execution_id != self.live_root:
            raise BranchRefused("older_generation", "messages from before the employee restarted cannot be edited")
        return _Shape(leaving=path[index:], left_leaf=path[-1].uid)

    async def write(self, session: Any, database: Any, thread: ChatThread, rows: List[ChatMessage], path: List[ChatMessage]) -> Dict[str, Any]:
        original = path[_on_path(path, self.message_uid)]
        run = _new_run(
            session_id=self.session_id, workflow_id=self.workflow_id, live_root=self.live_root, kind="edit",
            state=self.state, message_uid="", parent_run_id=original.run_id, options=self.options,
        )
        row = await database.append_chat_row(
            session,
            session_id=self.session_id,
            role="user",
            message=self.text,
            execution_id=self.live_root,
            uid=self.uid,
            run_id=run.run_id,
            attachments=list(original.attachments or []),
            meta={**self.meta, "edit_of": original.uid},
            after=original.parent_uid,
        )
        run.user_message_uid = row.uid
        session.add(run)
        return {"message": database.chat_row(row), "run": run, "prompt": self.text}


@dataclass
class _Retry(_Operation):
    session_id: str
    workflow_id: str
    live_root: str
    message_uid: str
    state: str
    question: Optional[ChatMessage] = None
    retried: Optional[ChatRun] = None
    #: The choices sent with the retry (``ChatRun.options``).
    options: Dict[str, Any] = field(default_factory=dict)

    async def shape(self, session: Any, rows: List[ChatMessage], path: List[ChatMessage]) -> _Shape:
        index = _on_path(path, self.message_uid)
        row = path[index]
        if index != len(path) - 1:
            raise BranchRefused("not_editable", "only the latest answer can be tried again")
        if row.role == "assistant":
            run = (await _runs(session, [row.run_id] if row.run_id else [])).get(row.run_id or "")
            if run is None or not run.user_message_uid:
                raise BranchRefused("not_editable", "this message did not come from a run")
            question_index = _on_path(path, run.user_message_uid)
        else:
            answering = answering_run_ids(path, await _session_runs(session, self.session_id)).get(row.uid)
            run = (await _runs(session, [answering] if answering else [])).get(answering or "")
            if run is None:
                raise BranchRefused("not_editable", "nothing answered this message")
            question_index = index
        if run.state not in TERMINAL_STATES:
            raise BranchRefused("run_in_progress", "the employee is still answering")
        question = path[question_index]
        if run.run_key != self.live_root or question.execution_id != self.live_root:
            raise BranchRefused("older_generation", "answers from before the employee restarted cannot be tried again")
        self.question, self.retried = question, run
        leaving = path[question_index + 1:]
        return _Shape(leaving=leaving, extra_run_ids=[run.run_id], left_leaf=path[-1].uid if leaving else None)

    async def write(self, session: Any, database: Any, thread: ChatThread, rows: List[ChatMessage], path: List[ChatMessage]) -> Dict[str, Any]:
        assert self.question is not None and self.retried is not None
        run = _new_run(
            session_id=self.session_id, workflow_id=self.workflow_id, live_root=self.live_root, kind="regenerate",
            state=self.state, message_uid=self.question.uid, parent_run_id=self.retried.run_id, options=self.options,
        )
        session.add(run)
        thread.active_leaf_uid = self.question.uid
        thread.revision = (thread.revision or 0) + 1
        thread.updated_at = _utcnow()
        session.add(thread)
        return {"message": database.chat_row(self.question), "run": run, "prompt": _prompt_of(self.question)}


@dataclass
class _Switch(_Operation):
    session_id: str
    live_root: str
    message_uid: str
    leaf: Optional[str] = None

    async def shape(self, session: Any, rows: List[ChatMessage], path: List[ChatMessage]) -> _Shape:
        by_uid = {row.uid: row for row in rows}
        target = by_uid.get(self.message_uid)
        if target is None:
            raise BranchRefused("not_found", "that version is not in this conversation")
        if any(row.uid == target.uid for row in path):
            # Already shown: nothing moves.
            self.leaf = path[-1].uid if path else None
            return _Shape()
        beside = next((row for row in path if row.parent_uid == target.parent_uid and row.role == target.role), None)
        if beside is None:
            raise BranchRefused("not_found", "that version is not beside the conversation shown")
        self.leaf = newest_leaf_under(rows, target.uid)
        leaving = path[_on_path(path, beside.uid):]
        arriving = _branch_rows(rows, target.uid, self.leaf)
        if any(row.execution_id != self.live_root for row in [*leaving, *arriving]):
            raise BranchRefused("older_generation", "the conversation from before the employee restarted cannot change")
        return _Shape(leaving=leaving, left_leaf=path[-1].uid, target=arriving, target_leaf=self.leaf)

    async def write(self, session: Any, database: Any, thread: ChatThread, rows: List[ChatMessage], path: List[ChatMessage]) -> Dict[str, Any]:
        if self.leaf and thread.active_leaf_uid != self.leaf:
            thread.active_leaf_uid = self.leaf
            thread.revision = (thread.revision or 0) + 1
            thread.updated_at = _utcnow()
            session.add(thread)
        return {"leaf": self.leaf}


def _prompt_of(message: ChatMessage) -> str:
    """What the employee reads for the owner's message: its text, or for a
    button press the ``[ui-event]`` line it was sent as."""
    event = (message.meta or {}).get("ui_event")
    if message.kind == "action" and isinstance(event, dict):
        from services.chat.parts import ui_event_message

        return ui_event_message(
            part_id=str(event.get("part_id") or ""),
            element_id=str(event.get("element_id") or ""),
            label=message.message,
            action=str(event.get("action") or ""),
            params=event.get("params") if isinstance(event.get("params"), dict) else {},
        )
    return message.message


async def _session_runs(session: Any, session_id: str) -> List[ChatRun]:
    result = await session.execute(select(ChatRun).where(ChatRun.session_id == session_id))
    return list(result.scalars().all())


async def _shape_and_plan(session: Any, *, session_id: str, generation: int, op: _Operation) -> Tuple[Optional[ChatThread], List[ChatMessage], List[ChatMessage], _Shape, _Plan]:
    thread = await session.get(ChatThread, session_id)
    rows = await _session_rows(session, session_id)
    path = active_path(rows, thread.active_leaf_uid if thread is not None else None)
    shape = await op.shape(session, rows, path)
    plan = await _plan(session, session_id=session_id, generation=generation, shape=shape)
    return thread, rows, path, shape, plan


async def _move(
    database: Any,
    *,
    session_id: str,
    workflow_id: str,
    generation: int,
    expected_revision: Optional[int],
    op: _Operation,
) -> Moved:
    from services.agent_context.conversation import conversation_lock

    # A first look names the agents whose conversations may change, so
    # their saves wait while the move runs.
    async with database.get_session() as session:
        _, _, _, _, first = await _shape_and_plan(session, session_id=session_id, generation=generation, op=op)
    async with AsyncExitStack() as stack:
        for agent in first.agents:
            await stack.enter_async_context(conversation_lock(workflow_id, generation, agent))
        async with database.reserved_session() as session:
            thread = await session.get(ChatThread, session_id)
            revision = thread.revision if thread is not None else 0
            if expected_revision is not None and revision != expected_revision:
                raise BranchRefused("revision_conflict", "the conversation changed meanwhile")
            if await _lane_held(session, session_id):
                raise BranchRefused("run_in_progress", "the employee is still answering")
            thread, rows, path, shape, plan = await _shape_and_plan(session, session_id=session_id, generation=generation, op=op)
            if plan.agents != first.agents:
                raise BranchRefused("revision_conflict", "the conversation changed meanwhile")
            if thread is None:
                # A session written before threads existed: its path ends
                # at its newest message.
                thread = ChatThread(session_id=session_id, active_leaf_uid=path[-1].uid if path else None)
                session.add(thread)
                await session.flush()
            changed = await _apply(session, plan, session_id=session_id, workflow_id=workflow_id, generation=generation, left_leaf=shape.left_leaf)
            result = await op.write(session, database, thread, rows, path)
            await session.commit()
    return Moved(
        session_id=session_id,
        workflow_id=workflow_id,
        generation=generation,
        changed=changed,
        left_run_ids=plan.left_run_ids,
        result=result,
    )


async def edit_message(
    database: Any,
    *,
    session_id: str,
    workflow_id: str,
    generation: int,
    live_root: str,
    message_uid: str,
    text: str,
    expected_revision: Optional[int],
    state: str = "pending",
    client_message_id: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Moved:
    """Add ``text`` as a new version of the owner's message ``message_uid``
    and start the run that answers it (kind ``edit``), keeping ``options``
    on the run. ``result`` holds ``message``, ``run`` and ``prompt``."""
    from services.chat.ledger import client_message_uid

    uid = client_message_uid(session_id, client_message_id) if client_message_id is not None else None
    meta = {"client_message_id": client_message_id} if client_message_id is not None else {}
    op = _Edit(
        session_id=session_id, workflow_id=workflow_id, live_root=live_root, message_uid=message_uid,
        text=text, state=state, uid=uid, meta=meta, options=dict(options or {}),
    )
    return await _move(database, session_id=session_id, workflow_id=workflow_id, generation=generation, expected_revision=expected_revision, op=op)


async def retry_answer(
    database: Any,
    *,
    session_id: str,
    workflow_id: str,
    generation: int,
    live_root: str,
    message_uid: str,
    expected_revision: Optional[int],
    state: str = "pending",
    options: Optional[Dict[str, Any]] = None,
) -> Moved:
    """Answer again the message the latest answer (``message_uid``) answered,
    or the owner's latest message when its run gave no answer; the new answer
    goes beside the old one (kind ``regenerate``), with ``options`` on its
    run. ``result`` holds ``message`` (the owner's message answered), ``run``
    and ``prompt``."""
    op = _Retry(
        session_id=session_id, workflow_id=workflow_id, live_root=live_root, message_uid=message_uid,
        state=state, options=dict(options or {}),
    )
    return await _move(database, session_id=session_id, workflow_id=workflow_id, generation=generation, expected_revision=expected_revision, op=op)


async def switch_branch(
    database: Any,
    *,
    session_id: str,
    workflow_id: str,
    generation: int,
    live_root: str,
    message_uid: str,
    expected_revision: Optional[int],
) -> Moved:
    """Show the version ``message_uid`` and the conversation after it.
    ``result`` holds the new ``leaf``."""
    op = _Switch(session_id=session_id, live_root=live_root, message_uid=message_uid)
    return await _move(database, session_id=session_id, workflow_id=workflow_id, generation=generation, expected_revision=expected_revision, op=op)


# ---- after a move ----------------------------------------------------------

#: What a send on the part left still did, as the employee hears it.
_STILL_DID = {"sent": "sent", "sending": "being sent"}


async def after_move(database: Any, moved: Moved) -> None:
    """What follows a committed move, each step best effort: the Context
    panel hears of the conversations changed; drafts the runs on the part
    left made that still wait are cancelled (nobody can see them any more);
    and the employee is told what those runs sent anyway."""
    from services.agent_context.listeners import notify_conversation_saved

    for agent, count in moved.changed:
        try:
            await notify_conversation_saved(
                workflow_id=moved.workflow_id, generation=moved.generation, agent_node_id=agent, message_count=count,
            )
        except Exception:  # noqa: BLE001 - the panel refreshes on its next read
            logger.warning("Context listeners were not told of a branch move", agent_node_id=agent, exc_info=True)
    if not moved.left_run_ids:
        return
    try:
        from services.approvals import store, waiter
        from services.approvals.listeners import change_of, notify_approval_changed

        for row in await store.cancel_open(database, workflow_id=moved.workflow_id, run_ids=moved.left_run_ids):
            waiter.notify(row.id)
            await notify_approval_changed(change_of(row, "cancelled"))
        await _note_sends(database, moved)
    except Exception:  # noqa: BLE001 - the move stands; a draft left waiting still shows on its card
        logger.warning("Drafts of a branch left were not settled", session_id=moved.session_id, exc_info=True)


async def _note_sends(database: Any, moved: Moved) -> None:
    from services.approvals import store
    from services.chat.notes import upsert_note

    rows = []
    for run_id in moved.left_run_ids:
        rows.extend(await store.list_approvals(database, workflow_id=moved.workflow_id, run_id=run_id, limit=100))
    went = []
    for row in rows:
        result = _STILL_DID.get(row.status)
        if result is None and row.status == "failed" and row.outcome == "unknown":
            result = "may or may not have gone out"
        if result is None:
            continue
        went.append({"what": row.action or f"{row.channel} message", "to": row.recipient_label or row.recipient or None, "result": result})
    if not went:
        return
    payload = {
        "note": (
            "The owner went back to an earlier point in this chat, so what came after it is no longer part of the "
            "conversation. These were sent on that part anyway."
        ),
        "sends": went,
    }
    text = f"[update]{json.dumps(payload, ensure_ascii=False, separators=(', ', ': '))}[/update]"
    await upsert_note(database, session_id=moved.session_id, key=f"branch:{moved.left_run_ids[0]}", kind="update", text=text)


__all__ = [
    "BranchRefused",
    "EDITABLE_KINDS",
    "Moved",
    "active_path",
    "after_move",
    "answering_run_ids",
    "conversation_digest",
    "edit_message",
    "editable_ids",
    "newest_leaf_under",
    "record_cursor",
    "retry_answer",
    "sibling_ids",
    "switch_branch",
]
