"""Canvas node — board store, display op, panel handlers, event contract.

Mirrors the named precedents:
- store fixture shape: tests/services/memory/test_tool_store.py (private real
  core.database load — the root conftest stubs it for unit speed)
- handler security: tests/services/test_tool_input_security.py (internal
  socket denial / owner mismatch / node-type check)
- event identity lock: tests/nodes/test_write_todos_handlers.py
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from nodes.tool.canvas import (
    CanvasNode,
    CanvasParams,
    _collect_connected_refs,
)
from nodes.tool.canvas._events import canvas_updated
from nodes.tool.canvas._handlers import (
    handle_canvas_add,
    handle_canvas_clear,
    handle_canvas_list,
    handle_canvas_remove,
    handle_canvas_version,
)
from nodes.tool.canvas._store import (
    CANVAS_NOTE_MAX_BYTES,
    CanvasItemVersion,
    CanvasScope,
    CanvasStore,
    CanvasStoreError,
    truncate_note,
)
from services.plugin import NodeContext, NodeUserError


# ---------------------------------------------------------------------------
# Real-database fixture (root conftest stubs core.database; load it privately)
# ---------------------------------------------------------------------------


@pytest.fixture
async def canvas_database():
    module_name = f"tests._real_canvas_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[2] / "core" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    db_path = Path.cwd() / f".canvas-{uuid.uuid4().hex}.db"
    database = module.Database(
        SimpleNamespace(
            database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
            database_echo=False,
            database_pool_size=5,
            database_max_overflow=5,
        )
    )
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)
        for candidate in (
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
        ):
            candidate.unlink(missing_ok=True)


def _scope(node_id: str = "canvas-1", workflow_id: str = "wf-1") -> CanvasScope:
    return CanvasScope(
        owner_id="owner", workflow_id=workflow_id, node_id=node_id
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_store_scope_isolation_between_nodes(canvas_database):
    store = CanvasStore(canvas_database)
    a, b = _scope("canvas-a"), _scope("canvas-b")

    await store.append(a, [{"kind": "note", "content": "only on a"}])

    assert len((await store.list(a))["items"]) == 1
    assert (await store.list(b)) == {"items": [], "revision": 0}


async def test_store_append_replace_and_revision_monotonicity(canvas_database):
    store = CanvasStore(canvas_database)
    scope = _scope()

    _, r1, total1 = await store.append(
        scope, [{"kind": "note", "content": "one"}]
    )
    _, r2, total2 = await store.append(
        scope, [{"kind": "url", "url": "https://example.com"}]
    )
    assert (r1, total1) == (1, 1)
    assert (r2, total2) == (2, 2)

    added, r3, total3 = await store.append(
        scope, [{"kind": "note", "content": "fresh"}], mode="replace"
    )
    assert (r3, total3) == (3, 1)
    items = (await store.list(scope))["items"]
    assert [row["id"] for row in items] == [added[0]["id"]]

    removed = await store.remove(scope, added[0]["id"])
    assert removed == {"removed": True, "revision": 4}
    cleared = await store.clear(scope)
    assert cleared["revision"] == 5

    with pytest.raises(CanvasStoreError):
        await store.remove(scope, "no-such-item")


async def test_store_fifo_eviction_at_cap(canvas_database, monkeypatch):
    monkeypatch.setattr("nodes.tool.canvas._store.CANVAS_MAX_ITEMS", 3)
    store = CanvasStore(canvas_database)
    scope = _scope()

    for index in range(5):
        await store.append(
            scope, [{"kind": "note", "content": f"note-{index}"}]
        )

    listed = await store.list(scope)
    assert [row["content"] for row in listed["items"]] == [
        "note-2",
        "note-3",
        "note-4",
    ]
    assert listed["revision"] == 5


async def test_store_rejects_unknown_kind(canvas_database):
    store = CanvasStore(canvas_database)
    with pytest.raises(CanvasStoreError):
        await store.append(_scope(), [{"kind": "bytes", "content": "x"}])


async def test_item_wire_shape_is_stable(canvas_database):
    store = CanvasStore(canvas_database)
    added, _, _ = await store.append(
        _scope(), [{"kind": "note", "content": "shape", "title": "t"}]
    )
    assert set(added[0]) == {
        "id",
        "kind",
        "title",
        "ref",
        "url",
        "content",
        "language",
        "source",
        "created_at",
        "version",
        "updated_at",
    }
    assert (added[0]["version"], added[0]["updated_at"]) == (1, None)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


async def _version_count(database, item_id=None) -> int:
    from sqlalchemy import func, select

    async with database.get_session() as session:
        query = select(func.count()).select_from(CanvasItemVersion)
        if item_id is not None:
            query = query.where(CanvasItemVersion.item_id == item_id)
        return int((await session.execute(query)).scalar() or 0)


async def test_an_update_is_the_next_version_and_the_earlier_ones_stay(canvas_database):
    store = CanvasStore(canvas_database)
    scope = _scope()
    [first], first_revision, _ = await store.append(scope, [{"kind": "note", "content": "# Plan\none", "title": "Plan"}])
    updated, revision = await store.update(scope, first["id"], {"kind": "note", "content": "# Plan\ntwo", "source": "agent"})
    assert revision == first_revision + 1
    assert (updated["id"], updated["version"], updated["content"], updated["title"]) == (first["id"], 2, "# Plan\ntwo", "Plan")
    assert updated["updated_at"] is not None

    listed = (await store.list(scope))["items"]
    assert [(item["version"], item["content"]) for item in listed] == [(2, "# Plan\ntwo")]
    history = await store.versions(scope, first["id"])
    assert history["latest"] == 2
    assert [(entry["version"], entry["size_bytes"]) for entry in history["versions"]] == [(2, 10), (1, 10)]
    old = await store.version(scope, first["id"], 1)
    assert (old["version"], old["latest"], old["content"]) == (1, 2, "# Plan\none")
    with pytest.raises(CanvasStoreError):
        await store.version(scope, first["id"], 3)


async def test_an_update_keeps_the_kind_and_needs_the_item(canvas_database):
    store = CanvasStore(canvas_database)
    scope = _scope()
    [note], _, _ = await store.append(scope, [{"kind": "note", "content": "x"}])
    with pytest.raises(CanvasStoreError, match="note"):
        await store.update(scope, note["id"], {"kind": "url", "url": "https://example.com"})
    with pytest.raises(CanvasStoreError):
        await store.update(scope, "missing", {"kind": "note", "content": "y"})
    # Another node's board does not hold it.
    with pytest.raises(CanvasStoreError):
        await store.update(_scope(node_id="other"), note["id"], {"kind": "note", "content": "y"})


async def test_an_item_keeps_its_newest_versions(canvas_database, monkeypatch):
    monkeypatch.setattr("nodes.tool.canvas._store.CANVAS_MAX_VERSIONS", 3)
    store = CanvasStore(canvas_database)
    scope = _scope()
    [note], _, _ = await store.append(scope, [{"kind": "note", "content": "v1"}])
    for number in range(2, 6):
        await store.update(scope, note["id"], {"kind": "note", "content": f"v{number}"})
    history = await store.versions(scope, note["id"])
    assert [entry["version"] for entry in history["versions"]] == [5, 4, 3]


async def test_removing_clearing_and_replacing_drop_versions(canvas_database):
    store = CanvasStore(canvas_database)
    scope = _scope()
    [kept, dropped], _, _ = await store.append(scope, [{"kind": "note", "content": "a"}, {"kind": "note", "content": "b"}])
    await store.update(scope, dropped["id"], {"kind": "note", "content": "b2"})
    await store.remove(scope, dropped["id"])
    assert await _version_count(canvas_database, dropped["id"]) == 0
    assert await _version_count(canvas_database, kept["id"]) == 1
    await store.append(scope, [{"kind": "note", "content": "c"}], mode="replace")
    assert await _version_count(canvas_database, kept["id"]) == 0
    await store.clear(scope)
    assert await _version_count(canvas_database) == 0


async def test_eviction_drops_versions(canvas_database, monkeypatch):
    monkeypatch.setattr("nodes.tool.canvas._store.CANVAS_MAX_ITEMS", 2)
    store = CanvasStore(canvas_database)
    scope = _scope()
    [oldest], _, _ = await store.append(scope, [{"kind": "note", "content": "1"}])
    await store.append(scope, [{"kind": "note", "content": "2"}, {"kind": "note", "content": "3"}])
    assert await _version_count(canvas_database, oldest["id"]) == 0
    assert await _version_count(canvas_database) == 2


async def test_a_board_saved_before_versions_gets_them(canvas_database):
    store = CanvasStore(canvas_database)
    scope = _scope()
    [note], _, _ = await store.append(scope, [{"kind": "note", "content": "old"}])
    # The table as it was before items had versions.
    async with canvas_database.engine.begin() as connection:
        await connection.exec_driver_sql("ALTER TABLE canvas_items RENAME TO canvas_items_new")
        await connection.exec_driver_sql(
            "CREATE TABLE canvas_items (id VARCHAR(64) PRIMARY KEY, board_id VARCHAR(80), kind VARCHAR(10), "
            "title VARCHAR(300), ref JSON, url VARCHAR(2048), content TEXT, language VARCHAR(40), "
            "source VARCHAR(10), position INTEGER, created_at DATETIME)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO canvas_items SELECT id, board_id, kind, title, ref, url, content, language, source, position, created_at "
            "FROM canvas_items_new"
        )
        await connection.exec_driver_sql("DROP TABLE canvas_items_new")
    CanvasStore._initialized_engines.discard(canvas_database.engine)

    [listed] = (await store.list(scope))["items"]
    assert (listed["id"], listed["version"], listed["updated_at"]) == (note["id"], 1, None)
    updated, _ = await store.update(scope, note["id"], {"kind": "note", "content": "new"})
    assert updated["version"] == 2


def test_truncate_note_caps_with_visible_marker():
    text, truncated = truncate_note("x" * (CANVAS_NOTE_MAX_BYTES + 100))
    assert truncated is True
    assert text.endswith("[truncated]")
    assert len(text.encode("utf-8")) <= CANVAS_NOTE_MAX_BYTES

    short, untouched = truncate_note("short")
    assert (short, untouched) == ("short", False)


def test_board_id_is_versioned_and_scope_sensitive():
    base = _scope()
    assert base.board_id.startswith("canv_")
    assert base.board_id != _scope(node_id="other").board_id
    assert base.board_id != _scope(workflow_id="wf-2").board_id


# ---------------------------------------------------------------------------
# Display op (direct op call with a constructed NodeContext)
# ---------------------------------------------------------------------------


def _node() -> CanvasNode:
    return CanvasNode()


def _ctx(tmp_path, *, raw=None, workflow_id="wf-op") -> NodeContext:
    return NodeContext(
        node_id="canvas-node",
        node_type="canvas",
        workflow_id=workflow_id,
        workspace_dir=str(tmp_path),
        raw=raw or {},
    )


@pytest.fixture
def op_database(canvas_database, monkeypatch):
    monkeypatch.setattr(
        "services.plugin.deps.get_database", lambda: canvas_database
    )
    return canvas_database


@pytest.fixture
def captured_events(monkeypatch):
    events = []

    async def _capture(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "nodes.tool.canvas._events.dispatch_canvas_updated", _capture
    )
    return events


async def test_display_paths_builds_contained_refs(
    tmp_path, op_database, captured_events
):
    media = tmp_path / "media"
    media.mkdir()
    (media / "chart.png").write_bytes(b"\x89PNG fake")

    result = await _node().display(
        _ctx(tmp_path),
        CanvasParams(paths=["media/chart.png"], title="Q3 chart"),
    )

    assert result["count"] == 1
    assert result["added"][0]["kind"] == "file"
    assert result["added"][0]["title"] == "Q3 chart"
    # Payload discipline: ids and titles only — no ref bodies in the output.
    assert set(result["added"][0]) == {"id", "kind", "title"}
    assert captured_events == [
        {"workflow_id": "wf-op", "node_id": "canvas-node", "revision": 1}
    ]

    store = CanvasStore(op_database)
    items = (
        await store.list(
            CanvasScope(
                owner_id="owner", workflow_id="wf-op", node_id="canvas-node"
            )
        )
    )["items"]
    ref = items[0]["ref"]
    assert ref["kind"] == "file"
    assert ref["path"] == "media/chart.png"
    assert ref["mime_type"] == "image/png"
    assert ref["workflow_id"] == "wf-op"
    assert ref["url"] == "/api/workspace/wf-op/files/media/chart.png"


async def test_display_rejects_traversal_paths(
    tmp_path, op_database, captured_events
):
    with pytest.raises(NodeUserError):
        await _node().display(
            _ctx(tmp_path), CanvasParams(paths=["../../credentials.db"])
        )
    assert captured_events == []


async def test_display_rejects_non_http_url(
    tmp_path, op_database, captured_events
):
    with pytest.raises(NodeUserError):
        await _node().display(
            _ctx(tmp_path), CanvasParams(url="javascript:alert(1)")
        )
    with pytest.raises(NodeUserError):
        await _node().display(
            _ctx(tmp_path), CanvasParams(url="file:///etc/passwd")
        )


async def test_display_note_truncates_and_reports(
    tmp_path, op_database, captured_events
):
    result = await _node().display(
        _ctx(tmp_path),
        CanvasParams(content="y" * (CANVAS_NOTE_MAX_BYTES + 10)),
    )
    assert "truncated" in result["message"]


async def test_display_empty_call_is_user_correctable(
    tmp_path, op_database, captured_events
):
    with pytest.raises(NodeUserError):
        await _node().display(_ctx(tmp_path), CanvasParams())


async def test_display_scans_connected_outputs_for_refs(
    tmp_path, op_database, captured_events
):
    connected = {
        "textToSpeech": {
            "audio": {
                "kind": "audio",
                "path": "audio/greeting.wav",
                "filename": "greeting.wav",
                "format": "wav",
            },
            # A gallery-style listing row is NOT a ref (extra keys fail
            # extra="forbid") — but the ref nested inside it is found.
            "row": {
                "name": "x.png",
                "is_dir": False,
                "ref": {
                    "kind": "file",
                    "path": "media/x.png",
                    "filename": "x.png",
                },
            },
        }
    }
    result = await _node().display(
        _ctx(tmp_path, raw={"connected_outputs": connected}), CanvasParams()
    )
    assert result["count"] == 2
    assert {entry["kind"] for entry in result["added"]} == {"file"}


async def test_display_labels_tool_calls_as_agent(
    tmp_path, op_database, captured_events
):
    await _node().display(
        _ctx(tmp_path, raw={"_tool_config": object()}),
        CanvasParams(content="from the model"),
    )
    store = CanvasStore(op_database)
    items = (
        await store.list(
            CanvasScope(
                owner_id="owner", workflow_id="wf-op", node_id="canvas-node"
            )
        )
    )["items"]
    assert items[0]["source"] == "agent"


async def test_display_replace_mode_clears_board(
    tmp_path, op_database, captured_events
):
    node, ctx = _node(), _ctx(tmp_path)
    await node.display(ctx, CanvasParams(content="first"))
    result = await node.display(
        ctx, CanvasParams(content="second", mode="replace")
    )
    assert result["count"] == 1
    assert "replaced" in result["message"]


async def test_display_update_id_revises_an_item(tmp_path, op_database, captured_events):
    node, ctx = _node(), _ctx(tmp_path)
    first = await node.display(ctx, CanvasParams(content="# Plan\nDraft", title="Plan"))
    item_id = first["added"][0]["id"]
    result = await node.display(ctx, CanvasParams(content="# Plan\nFinal", update_id=item_id))
    assert result["added"] == [{"id": item_id, "kind": "note", "title": "Plan", "version": 2}]
    assert "version 2" in result["message"]
    [item] = (await CanvasStore(op_database).list(CanvasScope(owner_id="owner", workflow_id="wf-op", node_id="canvas-node")))["items"]
    assert (item["version"], item["content"]) == (2, "# Plan\nFinal")
    assert len(captured_events) == 2


async def test_display_update_id_takes_one_item_of_its_kind(tmp_path, op_database, captured_events):
    node, ctx = _node(), _ctx(tmp_path)
    item_id = (await node.display(ctx, CanvasParams(content="note")))["added"][0]["id"]
    with pytest.raises(NodeUserError, match="one item"):
        await node.display(ctx, CanvasParams(content="x", url="https://example.com", update_id=item_id))
    with pytest.raises(NodeUserError, match="note"):
        await node.display(ctx, CanvasParams(url="https://example.com", update_id=item_id))
    with pytest.raises(NodeUserError):
        await node.display(ctx, CanvasParams(content="x", update_id="missing"))


@pytest.fixture
def shown_documents(monkeypatch):
    shown = []

    async def _show(database, stream, *, artifact):
        shown.append({"stream": stream, **artifact})

    monkeypatch.setattr("services.chat.parts.show_artifact", _show)
    return shown


async def test_a_note_the_answering_agent_writes_shows_in_its_reply(tmp_path, op_database, captured_events, shown_documents):
    stream = {"run_id": "r_1", "session_id": "wf-op", "workflow_id": "wf-op"}
    node, ctx = _node(), _ctx(tmp_path, raw={"_tool_config": object(), "chat_stream": stream})
    item_id = (await node.display(ctx, CanvasParams(content="## Weekly report\nAll good.")))["added"][0]["id"]
    await node.display(ctx, CanvasParams(content="## Weekly report\nAll better.", update_id=item_id, language="python"))
    assert shown_documents == [
        {"stream": stream, "workflow_id": "wf-op", "canvas_node_id": "canvas-node", "item_id": item_id, "version": 1, "title": "Weekly report", "format": "markdown"},
        {"stream": stream, "workflow_id": "wf-op", "canvas_node_id": "canvas-node", "item_id": item_id, "version": 2, "title": "Weekly report", "format": "code"},
    ]


async def test_only_notes_in_a_chat_run_show_in_the_reply(tmp_path, op_database, captured_events, shown_documents):
    (tmp_path / "chart.png").write_bytes(b"\x89PNG fake")
    stream = {"run_id": "r_1", "session_id": "wf-op", "workflow_id": "wf-op"}
    await _node().display(_ctx(tmp_path, raw={"chat_stream": stream}), CanvasParams(paths=["chart.png"], url="https://example.com"))
    await _node().display(_ctx(tmp_path), CanvasParams(content="no run"))
    assert shown_documents == []


async def test_a_markdown_note_is_a_document_not_code(tmp_path, op_database, captured_events, shown_documents):
    stream = {"run_id": "r_1", "session_id": "wf-op", "workflow_id": "wf-op"}
    ctx = _ctx(tmp_path, raw={"chat_stream": stream})
    await _node().display(ctx, CanvasParams(content="Plain words", language="Markdown"))
    assert [(shown["title"], shown["format"]) for shown in shown_documents] == [("Plain words", "markdown")]


def test_collect_connected_refs_dedupes_and_caps():
    ref = {"kind": "file", "path": "a/b.png", "filename": "b.png"}
    payload = {
        "one": ref,
        "two": {"nested": [dict(ref), {"kind": "file", "path": "c.md", "filename": "c.md"}]},
        "not_a_ref": {"kind": "file", "path": "x", "filename": "x", "extra_key": 1},
    }
    refs = _collect_connected_refs(payload, limit=10)
    assert [r["path"] for r in refs] == ["a/b.png", "c.md"]

    many = {str(i): {"kind": "file", "path": f"p{i}", "filename": f"p{i}"} for i in range(9)}
    assert len(_collect_connected_refs(many, limit=3)) == 3


# ---------------------------------------------------------------------------
# Params coercion
# ---------------------------------------------------------------------------


def test_params_coerce_paths_shapes():
    assert CanvasParams(paths='["a.png", "b.md"]').paths == ["a.png", "b.md"]
    assert CanvasParams(paths="single/path.png").paths == ["single/path.png"]
    assert CanvasParams(
        paths=[{"kind": "file", "path": "from/ref.wav", "filename": "ref.wav"}]
    ).paths == ["from/ref.wav"]
    assert CanvasParams(paths="").paths is None
    # Blank-string mode from a cleared panel field falls back to the default.
    assert CanvasParams(mode="").mode == "append"


# ---------------------------------------------------------------------------
# Panel WS handlers — security preamble + round trip
# ---------------------------------------------------------------------------


class _FakeSocket:
    def __init__(self, *, path="/ws/status", user_id="owner"):
        self.scope = {"path": path, "user_id": user_id}
        self.state = SimpleNamespace(user_id=user_id)


class _HandlerDatabase:
    """Real store engine + a canned workflow graph for scope resolution."""

    def __init__(self, database, graph):
        self._database = database
        self._graph = graph

    @property
    def engine(self):
        return self._database.engine

    def get_session(self):
        return self._database.get_session()

    async def get_workflow(self, workflow_id):
        if workflow_id != self._graph["id"]:
            return None
        return SimpleNamespace(data=self._graph)


def _graph(workflow_id="wf-h", node_id="canvas-h", node_type="canvas", owner="owner"):
    return {
        "id": workflow_id,
        "owner_id": owner,
        "nodes": [{"id": node_id, "type": node_type}],
        "edges": [],
    }


@pytest.fixture
def handler_env(canvas_database, monkeypatch):
    def bind(graph):
        wrapper = _HandlerDatabase(canvas_database, graph)
        monkeypatch.setattr(
            "nodes.tool.canvas._handlers.get_database", lambda: wrapper
        )
        return wrapper

    return bind


@pytest.fixture
def handler_events(monkeypatch):
    events = []

    async def _capture(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "nodes.tool.canvas._handlers.dispatch_canvas_updated", _capture
    )
    return events


async def test_internal_socket_is_denied(handler_env, handler_events):
    handler_env(_graph())
    response = await handle_canvas_list(
        {"workflow_id": "wf-h", "node_id": "canvas-h"},
        _FakeSocket(path="/ws/internal"),
    )
    assert response["success"] is False
    assert "authenticated" in response["error"]


async def test_owner_mismatch_is_denied(handler_env, handler_events):
    handler_env(_graph(owner="someone-else"))
    response = await handle_canvas_list(
        {"workflow_id": "wf-h", "node_id": "canvas-h"}, _FakeSocket()
    )
    assert response["success"] is False
    assert "denied" in response["error"].lower()


async def test_wrong_node_type_is_denied(handler_env, handler_events):
    handler_env(_graph(node_type="simpleMemory"))
    response = await handle_canvas_list(
        {"workflow_id": "wf-h", "node_id": "canvas-h"}, _FakeSocket()
    )
    assert response["success"] is False
    assert "does not belong" in response["error"]


async def test_handler_round_trip_list_remove_clear(
    canvas_database, handler_env, handler_events
):
    handler_env(_graph())
    store = CanvasStore(canvas_database)
    scope = CanvasScope(owner_id="owner", workflow_id="wf-h", node_id="canvas-h")
    added, _, _ = await store.append(
        scope,
        [
            {"kind": "note", "content": "keep"},
            {"kind": "note", "content": "drop"},
        ],
    )

    listed = await handle_canvas_list(
        {"workflow_id": "wf-h", "node_id": "canvas-h"}, _FakeSocket()
    )
    assert listed["success"] is True
    assert [row["content"] for row in listed["items"]] == ["keep", "drop"]

    removed = await handle_canvas_remove(
        {
            "workflow_id": "wf-h",
            "node_id": "canvas-h",
            "item_id": added[1]["id"],
        },
        _FakeSocket(),
    )
    assert removed["success"] is True

    cleared = await handle_canvas_clear(
        {"workflow_id": "wf-h", "node_id": "canvas-h"}, _FakeSocket()
    )
    assert cleared["success"] is True

    # Both mutations broadcast identity + revision.
    assert [event["node_id"] for event in handler_events] == [
        "canvas-h",
        "canvas-h",
    ]

    final = await handle_canvas_list(
        {"workflow_id": "wf-h", "node_id": "canvas-h"}, _FakeSocket()
    )
    assert final["items"] == []


async def test_handler_reads_a_version(canvas_database, handler_env, handler_events):
    handler_env(_graph())
    store = CanvasStore(canvas_database)
    scope = CanvasScope(owner_id="owner", workflow_id="wf-h", node_id="canvas-h")
    [note], _, _ = await store.append(scope, [{"kind": "note", "content": "one"}])
    await store.update(scope, note["id"], {"kind": "note", "content": "two"})
    where = {"workflow_id": "wf-h", "node_id": "canvas-h", "item_id": note["id"]}

    first = await handle_canvas_version({**where, "version": 1}, _FakeSocket())
    assert (first["success"], first["item"]["content"], first["item"]["latest"]) == (True, "one", 2)

    assert (await handle_canvas_version({**where, "version": 9}, _FakeSocket()))["success"] is False
    assert (await handle_canvas_version({**where, "version": "1"}, _FakeSocket()))["success"] is False
    assert (await handle_canvas_version({**where, "item_id": "", "version": 1}, _FakeSocket()))["success"] is False
    # The same preamble as the other handlers.
    assert (await handle_canvas_version({**where, "version": 1}, _FakeSocket(path="/ws/internal")))["success"] is False


async def test_the_owner_adds_a_workspace_file_to_the_board(canvas_database, handler_env, handler_events, tmp_path, monkeypatch):
    handler_env(_graph())

    async def root(workflow_id, database, *, allow_default=True):
        assert (workflow_id, allow_default) == ("wf-h", False)
        return tmp_path

    monkeypatch.setattr("services.workspace_locator.resolve_workspace_root", root)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "phone-1.png").write_bytes(b"\x89PNG" + b"0" * 200)
    where = {"workflow_id": "wf-h", "node_id": "canvas-h"}

    added = await handle_canvas_add({**where, "path": "media/phone-1.png"}, _FakeSocket())
    assert added["success"] is True and added["count"] == 1
    ref = added["item"]["ref"]
    # Rebuilt from the file: the client's word is never taken for it.
    assert (ref["path"], ref["filename"], ref["mime_type"], ref["size_bytes"]) == ("media/phone-1.png", "phone-1.png", "image/png", 204)
    assert ref["url"] == "/api/workspace/wf-h/files/media/phone-1.png"
    assert added["item"]["source"] == "owner"
    assert [event["node_id"] for event in handler_events] == ["canvas-h"]

    for refused in ({**where, "path": ""}, {**where, "path": "../secret.txt"}, {**where, "path": "media/missing.png"}):
        assert (await handle_canvas_add(refused, _FakeSocket()))["success"] is False
    assert (await handle_canvas_add({**where, "path": "media/phone-1.png"}, _FakeSocket(path="/ws/internal")))["success"] is False


# ---------------------------------------------------------------------------
# Event + tool-schema contract locks
# ---------------------------------------------------------------------------


def test_canvas_updated_event_is_identity_only():
    event = canvas_updated(workflow_id="wf-e", node_id="canvas-e", revision=7)
    assert event.source == "opencompany://nodes/canvas"
    assert event.type == "com.opencompany.canvas.updated"
    assert event.subject == "canvas-e"
    assert event.data == {
        "workflow_id": "wf-e",
        "node_id": "canvas-e",
        "revision": 7,
    }


def test_tool_schema_is_locked_flat_and_named_canvas():
    schema = CanvasNode.as_tool_schema()
    assert schema["name"] == "canvas"
    assert CanvasNode.tool_schema_locked is True

    def assert_no_ref_keys(value):
        if isinstance(value, dict):
            assert "$defs" not in value and "$ref" not in value, value.keys()
            for child in value.values():
                assert_no_ref_keys(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_ref_keys(child)

    assert_no_ref_keys(schema)

    assert CanvasNode.ui_hints["isCanvasPanel"] is True
    assert CanvasNode.ui_hints["isConfigNode"] is False
    assert CanvasNode.annotations == {
        "destructive": False,
        "readonly": False,
        "open_world": False,
    }
