"""Contract tests for ``services.workflow_validator.validate_workflow``.

Locks the six issue codes the validator surfaces:

- ``DANGLING_EDGE`` — edge references a node id that isn't in the graph.
- ``UNKNOWN_NODE_TYPE`` — plugin not installed on this instance.
- ``INVALID_PARAM`` — Pydantic ``Params.model_validate`` raises.
- ``MISSING_CREDENTIAL`` — declared credential not stored.
- ``CYCLE`` — Kahn's algorithm leaves nodes unresolved.
- Empty report — valid workflow returns ``{errors: [], warnings: []}``.

Used to gate ``handle_execute_workflow`` (force=False), all
``handle_deploy_workflow`` calls, and ``example_loader.import_examples_for_user``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes — a small in-memory plugin registry so tests are deterministic and
# don't depend on which plugins happen to be installed at run time.
# ---------------------------------------------------------------------------


class _FakeCredential:
    id = "fake_provider"
    auth = "api_key"


class _FakeParams(BaseModel):
    text: str = Field(..., min_length=1)


class _FakeNodeClass:
    type = "fakeNode"
    Params = _FakeParams
    credentials = (_FakeCredential,)


class _FakeNodelessClass:
    """Plugin with no credentials and a permissive Params (every workflow
    invariant is honored)."""

    type = "fakeNodeless"

    class Params(BaseModel):
        pass

    credentials = ()


def _patch_registry(monkeypatch, mapping: dict[str, object]) -> None:
    """Patch ``get_node_class`` to return our fakes."""
    monkeypatch.setattr(
        "services.workflow_validator.get_node_class",
        lambda node_type: mapping.get(node_type),
    )


def _patch_auth(monkeypatch, has_valid_key_return: bool) -> None:
    """Patch the container so ``auth_service.has_valid_key`` returns a fixed bool."""
    fake_auth = MagicMock()
    fake_auth.has_valid_key = AsyncMock(return_value=has_valid_key_return)
    fake_container = MagicMock()
    fake_container.auth_service.return_value = fake_auth
    monkeypatch.setattr("core.container.container", fake_container)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_dangling_edge_target_is_error(monkeypatch):
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNodeless": _FakeNodelessClass})
    _patch_auth(monkeypatch, True)

    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "fakeNodeless", "data": {}}],
        edges=[{"id": "e1", "source": "n1", "target": "ghost"}],
    )
    codes = [iss["code"] for iss in report["errors"]]
    assert "DANGLING_EDGE" in codes


async def test_dangling_edge_source_is_error(monkeypatch):
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNodeless": _FakeNodelessClass})
    _patch_auth(monkeypatch, True)

    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "fakeNodeless", "data": {}}],
        edges=[{"id": "e1", "source": "ghost", "target": "n1"}],
    )
    codes = [iss["code"] for iss in report["errors"]]
    assert "DANGLING_EDGE" in codes


async def test_unknown_node_type_is_error(monkeypatch):
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {})
    _patch_auth(monkeypatch, True)

    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "doesNotExist", "data": {}}],
        edges=[],
    )
    codes = [iss["code"] for iss in report["errors"]]
    assert "UNKNOWN_NODE_TYPE" in codes
    issue = next(iss for iss in report["errors"] if iss["code"] == "UNKNOWN_NODE_TYPE")
    assert issue["node_id"] == "n1"
    assert issue["node_type"] == "doesNotExist"


async def test_invalid_param_is_warning(monkeypatch):
    """INVALID_PARAM is a WARNING — matches the runtime soft-fail at
    ``node_executor._prepare_parameters`` (logs WARN, continues)."""
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNode": _FakeNodeClass})
    _patch_auth(monkeypatch, True)

    # text is required + min_length=1; empty triggers ValidationError.
    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "fakeNode", "data": {"parameters": {"text": ""}}}],
        edges=[],
    )
    codes = [iss["code"] for iss in report["warnings"]]
    assert "INVALID_PARAM" in codes
    assert all(iss["code"] != "INVALID_PARAM" for iss in report["errors"]), (
        "INVALID_PARAM must not block execution; only deploy-time errors block."
    )


async def test_missing_credential_is_warning(monkeypatch):
    """MISSING_CREDENTIAL is a WARNING so the workflow can be saved/imported
    and credentials configured afterward. Runtime failure (different path,
    different broadcast) is what surfaces to the user during execution."""
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNode": _FakeNodeClass})
    _patch_auth(monkeypatch, False)  # credential NOT stored

    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "fakeNode", "data": {"parameters": {"text": "ok"}}}],
        edges=[],
    )
    codes = [iss["code"] for iss in report["warnings"]]
    assert "MISSING_CREDENTIAL" in codes
    issue = next(iss for iss in report["warnings"] if iss["code"] == "MISSING_CREDENTIAL")
    assert issue["provider_id"] == "fake_provider"
    assert issue["remediation"] == "add_key"
    assert issue["node_id"] == "n1"


async def test_cycle_is_error(monkeypatch):
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNodeless": _FakeNodelessClass})
    _patch_auth(monkeypatch, True)

    # Two-node cycle: n1 -> n2 -> n1
    report = await validate_workflow(
        nodes=[
            {"id": "n1", "type": "fakeNodeless", "data": {}},
            {"id": "n2", "type": "fakeNodeless", "data": {}},
        ],
        edges=[
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n1"},
        ],
    )
    codes = [iss["code"] for iss in report["errors"]]
    assert "CYCLE" in codes
    cycle = next(iss for iss in report["errors"] if iss["code"] == "CYCLE")
    assert set(cycle["nodes"]) == {"n1", "n2"}


async def test_valid_workflow_returns_empty_report(monkeypatch):
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNodeless": _FakeNodelessClass})
    _patch_auth(monkeypatch, True)

    report = await validate_workflow(
        nodes=[
            {"id": "n1", "type": "fakeNodeless", "data": {}},
            {"id": "n2", "type": "fakeNodeless", "data": {}},
        ],
        edges=[{"id": "e1", "source": "n1", "target": "n2"}],
    )
    assert report == {"errors": [], "warnings": []}


async def test_parameters_by_id_overrides_node_data(monkeypatch):
    """When parameters_by_id is supplied, it wins over node.data.parameters.
    Used by the WS execute handler (hydrating from DB) and the
    example_loader (which holds params in the JSON's nodeParameters block)."""
    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {"fakeNode": _FakeNodeClass})
    _patch_auth(monkeypatch, True)

    # node.data has valid params, parameters_by_id has invalid → invalid wins.
    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "fakeNode", "data": {"parameters": {"text": "good"}}}],
        edges=[],
        parameters_by_id={"n1": {"text": ""}},
    )
    codes = [iss["code"] for iss in report["warnings"]]
    assert "INVALID_PARAM" in codes


async def test_report_shape_is_plain_dicts(monkeypatch):
    """Issues must be plain dicts (no dataclasses / enums) so the report
    serializes directly to JSON without custom encoders."""
    import json

    from services.workflow_validator import validate_workflow

    _patch_registry(monkeypatch, {})
    _patch_auth(monkeypatch, True)

    report = await validate_workflow(
        nodes=[{"id": "n1", "type": "unknown", "data": {}}],
        edges=[],
    )
    # Should round-trip through stdlib JSON without errors.
    serialized = json.dumps(report)
    assert "UNKNOWN_NODE_TYPE" in serialized


# ---------------------------------------------------------------------------
# WS-handler gating invariants
# ---------------------------------------------------------------------------


class TestExecuteAndDeployHandlersGate:
    """Static-source contract: ``handle_execute_workflow`` and
    ``handle_deploy_workflow`` MUST call ``validate_workflow`` before
    handing the graph off to ``WorkflowService``. Anchor on the function
    name string so a rename forces a deliberate test update.
    """

    @staticmethod
    def _handler_source(handler) -> str:
        import inspect

        fn = handler
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        return inspect.getsource(fn)

    def test_execute_workflow_calls_validator(self):
        from routers import websocket as ws_module

        src = self._handler_source(ws_module.handle_execute_workflow)
        assert "validate_workflow" in src, (
            "handle_execute_workflow must call validate_workflow so "
            "broken workflows are blocked at the gate (force=true overrides)."
        )
        assert "force" in src, (
            "handle_execute_workflow must support the force=true override "
            "so 'Run anyway' can bypass warnings (Windmill pattern)."
        )

    def test_deploy_workflow_calls_validator_unconditionally(self):
        # Wave 13.2: handle_deploy_workflow moved out of routers.websocket
        # into services/deployment/handlers.py.
        from services.deployment.handlers import handle_deploy_workflow

        src = self._handler_source(handle_deploy_workflow)
        assert "validate_workflow" in src, (
            "handle_deploy_workflow must call validate_workflow — a broken "
            "workflow deployed on a schedule is much worse than a failed "
            "manual run, so deploy never honors a force-override."
        )

    def test_validate_workflow_handler_registered(self):
        from routers import websocket as ws_module

        assert "validate_workflow" in ws_module.MESSAGE_HANDLERS, (
            "validate_workflow message type must be in MESSAGE_HANDLERS "
            "so the frontend live-lint and import dry-run paths can reach it."
        )

    def test_example_loader_calls_validator(self):
        """First-launch example import must run validator to skip
        malformed examples (errors) and log credential gaps (warnings)."""
        import inspect
        from services import example_loader

        src = inspect.getsource(example_loader.import_examples_for_user)
        assert "validate_workflow" in src, (
            "import_examples_for_user must run validate_workflow before "
            "save_workflow — broken examples shipped on disk are bugs."
        )

    def test_example_loader_remaps_node_ids(self):
        """Example loader MUST rewrite node ids before save. Without this,
        two example workflows that share a node id (today: AI Assistant +
        Claude Assistant share 6 trigger/console/memory nodes) collide in
        the node_parameters table (keyed unique on node_id), and the
        second example silently overwrites the first's parameters."""
        import inspect
        from services import example_loader

        src = inspect.getsource(example_loader.import_examples_for_user)
        assert "remap_node_ids" in src, (
            "import_examples_for_user must call workflow_import.remap_node_ids "
            "before save_workflow / save_node_parameters."
        )


class TestRemapNodeIds:
    """Regression contract for ``services.workflow_import.remap_node_ids``.
    Two example workflows in ``workflows/`` share 6 node ids — this helper
    is what prevents parameter overwrites at import time."""

    def test_remap_eliminates_duplicate_ids(self):
        """Two graphs with identical input ids must emit disjoint output
        id sets after independent remap calls."""
        from services.workflow_import import remap_node_ids

        nodes_a = [{"id": "shared-1", "type": "telegramSend", "data": {}}]
        nodes_b = [{"id": "shared-1", "type": "telegramSend", "data": {}}]
        a_nodes, _, _ = remap_node_ids(nodes_a, [], {"shared-1": {"x": 1}})
        b_nodes, _, _ = remap_node_ids(nodes_b, [], {"shared-1": {"x": 2}})

        a_ids = {n["id"] for n in a_nodes}
        b_ids = {n["id"] for n in b_nodes}
        assert a_ids.isdisjoint(b_ids), (
            f"Remap must produce disjoint id sets, got overlap: {a_ids & b_ids}"
        )

    def test_remap_rewrites_edge_refs(self):
        """Edges must point at the new node ids; no dangling references."""
        from services.workflow_import import remap_node_ids

        nodes = [
            {"id": "n1", "type": "start", "data": {}},
            {"id": "n2", "type": "httpRequest", "data": {}},
        ]
        edges = [{"id": "e1", "source": "n1", "target": "n2"}]
        new_nodes, new_edges, _ = remap_node_ids(nodes, edges, {})

        node_id_set = {n["id"] for n in new_nodes}
        assert len(new_edges) == 1
        assert new_edges[0]["source"] in node_id_set
        assert new_edges[0]["target"] in node_id_set

    def test_remap_rekeys_parameters(self):
        """nodeParameters keys must be remapped to match the new node ids."""
        from services.workflow_import import remap_node_ids

        nodes = [{"id": "orig-1", "type": "telegramSend", "data": {}}]
        new_nodes, _, new_params = remap_node_ids(
            nodes, [], {"orig-1": {"text": "hello"}}
        )
        assert "orig-1" not in new_params
        assert new_nodes[0]["id"] in new_params
        assert new_params[new_nodes[0]["id"]] == {"text": "hello"}

    def test_remap_drops_orphan_parameters(self):
        """A parameter entry whose node isn't in the graph is dead data
        (rare, only happens when an export was hand-edited). Dropped."""
        from services.workflow_import import remap_node_ids

        _, _, new_params = remap_node_ids(
            [{"id": "n1", "type": "start", "data": {}}],
            [],
            {"n1": {"x": 1}, "ghost": {"y": 2}},
        )
        assert len(new_params) == 1, "orphan parameter entry should be dropped"

    def test_remap_real_examples_no_collisions(self):
        """Spot check against the actual example workflow files shipped in
        ``workflows/``: today AI Assistant + Claude Assistant share 6
        node ids; after remap, the union must be collision-free."""
        import collections
        import glob
        import json

        from core.paths import example_workflows_dir
        from services.workflow_import import remap_node_ids

        examples_dir = example_workflows_dir()
        all_ids = collections.defaultdict(list)
        for path in sorted(glob.glob(str(examples_dir / "*.json"))):
            with open(path, encoding="utf-8") as fh:
                wf = json.load(fh)
            nodes, _, _ = remap_node_ids(
                wf.get("nodes", []),
                wf.get("edges", []),
                wf.get("nodeParameters", {}),
            )
            for node in nodes:
                all_ids[node["id"]].append(path)

        collisions = {k: v for k, v in all_ids.items() if len(v) > 1}
        assert not collisions, (
            f"Remap should produce disjoint ids across all example "
            f"workflows; found collisions: {collisions}"
        )


# ---------------------------------------------------------------------------
# workflow_import orchestrator tests
# ---------------------------------------------------------------------------


class _FakeWorkflow:
    """Mimics the SQLModel.Workflow rows returned by get_all_workflows."""

    def __init__(self, name: str):
        self.name = name


def _fake_database(existing_names: list[str] | None = None):
    """In-memory database double for the orchestrator tests."""
    saved_workflows: list[dict] = []
    saved_params: dict[str, dict] = {}

    db = MagicMock()
    db.get_all_workflows = AsyncMock(
        return_value=[_FakeWorkflow(n) for n in (existing_names or [])]
    )

    async def save_workflow(**kwargs):
        saved_workflows.append(kwargs)
        return True

    async def save_node_parameters(node_id, params):
        saved_params[node_id] = params
        return True

    db.save_workflow = AsyncMock(side_effect=save_workflow)
    db.save_node_parameters = AsyncMock(side_effect=save_node_parameters)
    db._saved_workflows = saved_workflows  # test-readable
    db._saved_params = saved_params
    return db


def _fake_auth(has_valid_key_return: bool = True):
    auth = MagicMock()
    auth.has_valid_key = AsyncMock(return_value=has_valid_key_return)
    return auth


class TestImportWorkflowOrchestrator:
    """Contract for ``services.workflow_import.import_workflow``. Locks the
    two-step UX: first call returns preview when confirmations are needed
    (name conflict / missing creds), second call with confirmations saves."""

    async def test_save_path_with_no_conflicts_no_missing_creds(self, monkeypatch):
        """Happy path: valid workflow, no name conflict, no missing creds
        → preview is False, workflow saved, workflow_id returned."""
        from services.workflow_import import import_workflow

        # Patch the registry so the synthetic node type validates.
        _patch_registry_in_validator(monkeypatch)

        db = _fake_database(existing_names=[])
        auth = _fake_auth(has_valid_key_return=True)

        result = await import_workflow(
            {
                "name": "My Workflow",
                "nodes": [{"id": "n1", "type": "fakeNodeless", "data": {}}],
                "edges": [],
            },
            auth_service=auth,
            database=db,
        )
        assert result["success"] is True
        assert result["preview"] is False
        assert result["workflow_id"].startswith("workflow-")
        assert result["name"] == "My Workflow"
        assert len(db._saved_workflows) == 1

    async def test_preview_when_name_conflicts(self, monkeypatch):
        """Name conflict → preview=True with suggested_name; no save yet."""
        from services.workflow_import import import_workflow

        _patch_registry_in_validator(monkeypatch)
        db = _fake_database(existing_names=["My Workflow"])
        auth = _fake_auth(has_valid_key_return=True)

        result = await import_workflow(
            {
                "name": "My Workflow",
                "nodes": [{"id": "n1", "type": "fakeNodeless", "data": {}}],
                "edges": [],
            },
            auth_service=auth,
            database=db,
        )
        assert result["success"] is True
        assert result["preview"] is True
        assert result["name_conflict"] is True
        assert result["suggested_name"] == "My Workflow (imported)"
        assert len(db._saved_workflows) == 0  # NOT saved yet

    async def test_preview_when_credentials_missing(self, monkeypatch):
        """Missing credentials (without force flag) → preview=True with
        missing_credentials list; no save yet."""
        from services.workflow_import import import_workflow

        _patch_registry_in_validator(monkeypatch)
        # Patch CREDENTIAL_REGISTRY so cross-check can pull display info.
        monkeypatch.setattr(
            "services.plugin.credential.CREDENTIAL_REGISTRY",
            {"fake_provider": _FakeCredential},
        )
        db = _fake_database(existing_names=[])
        auth = _fake_auth(has_valid_key_return=False)  # no creds stored

        result = await import_workflow(
            {
                "name": "Need Creds",
                "nodes": [{"id": "n1", "type": "fakeNode", "data": {"parameters": {"text": "ok"}}}],
                "edges": [],
            },
            auth_service=auth,
            database=db,
        )
        assert result["preview"] is True
        assert len(result["missing_credentials"]) == 1
        assert result["missing_credentials"][0]["provider_id"] == "fake_provider"
        assert len(db._saved_workflows) == 0

    async def test_force_credentials_skips_preview(self, monkeypatch):
        """force_credentials=True bypasses the missing-credential preview."""
        from services.workflow_import import import_workflow

        _patch_registry_in_validator(monkeypatch)
        monkeypatch.setattr(
            "services.plugin.credential.CREDENTIAL_REGISTRY",
            {"fake_provider": _FakeCredential},
        )
        # Mock the broadcaster — save path emits workflow.imported.
        _patch_broadcaster(monkeypatch)
        db = _fake_database(existing_names=[])
        auth = _fake_auth(has_valid_key_return=False)

        result = await import_workflow(
            {
                "name": "Force",
                "nodes": [{"id": "n1", "type": "fakeNode", "data": {"parameters": {"text": "ok"}}}],
                "edges": [],
            },
            force_credentials=True,
            auth_service=auth,
            database=db,
        )
        assert result["success"] is True
        assert result["preview"] is False
        assert len(db._saved_workflows) == 1

    async def test_validation_errors_block_import(self, monkeypatch):
        """Validation errors (e.g. unknown node type, cycle) short-circuit
        with success=False — no preview, no save."""
        from services.workflow_import import import_workflow

        _patch_registry_in_validator(monkeypatch)
        db = _fake_database()
        auth = _fake_auth()

        result = await import_workflow(
            {
                "name": "Bad",
                "nodes": [{"id": "n1", "type": "doesNotExist", "data": {}}],
                "edges": [],
            },
            auth_service=auth,
            database=db,
        )
        assert result["success"] is False
        assert result["error"] == "validation_failed"
        assert any(i["code"] == "UNKNOWN_NODE_TYPE" for i in result["report"]["errors"])
        assert len(db._saved_workflows) == 0

    async def test_save_path_remaps_node_ids(self, monkeypatch):
        """Save path applies remap_node_ids so the saved workflow has
        fresh ids regardless of the input. Two saves of the same JSON
        produce disjoint id sets."""
        from services.workflow_import import import_workflow

        _patch_registry_in_validator(monkeypatch)
        _patch_broadcaster(monkeypatch)
        db = _fake_database(existing_names=[])
        auth = _fake_auth(has_valid_key_return=True)

        payload = {
            "name": "Remap A",
            "nodes": [{"id": "orig-1", "type": "fakeNodeless", "data": {}}],
            "edges": [],
            "nodeParameters": {"orig-1": {"x": 1}},
        }
        r1 = await import_workflow(payload, auth_service=auth, database=db)
        r2 = await import_workflow(
            {**payload, "name": "Remap B"}, auth_service=auth, database=db
        )

        # Saved node ids must differ across the two imports.
        saved_a_nodes = db._saved_workflows[0]["data"]["nodes"]
        saved_b_nodes = db._saved_workflows[1]["data"]["nodes"]
        a_ids = {n["id"] for n in saved_a_nodes}
        b_ids = {n["id"] for n in saved_b_nodes}
        assert a_ids.isdisjoint(b_ids), (
            f"Two imports of the same JSON must produce disjoint node ids; "
            f"overlap: {a_ids & b_ids}"
        )
        # Parameters saved under the NEW ids, not the original.
        assert "orig-1" not in db._saved_params
        assert len(db._saved_params) >= 2  # both r1 and r2 saved their params
        # Sanity: r1 and r2 both succeeded.
        assert r1["success"] and r2["success"]

    async def test_save_path_broadcasts_workflow_imported(self, monkeypatch):
        """Save path must emit a CloudEvents-typed workflow.imported event
        via broadcast_workflow_lifecycle. Other connected clients listen
        for this and invalidate the workflows query."""
        from services.workflow_import import import_workflow

        _patch_registry_in_validator(monkeypatch)
        broadcaster, calls = _patch_broadcaster(monkeypatch)
        db = _fake_database()
        auth = _fake_auth()

        await import_workflow(
            {
                "name": "Broadcast Test",
                "nodes": [{"id": "n1", "type": "fakeNodeless", "data": {}}],
                "edges": [],
            },
            auth_service=auth,
            database=db,
        )
        assert len(calls) == 1, (
            "import_workflow save path must call broadcast_workflow_lifecycle "
            "exactly once on success."
        )
        stage, kwargs = calls[0]
        assert stage == "imported"
        assert kwargs["workflow_id"].startswith("workflow-")
        assert kwargs["name"] == "Broadcast Test"


def _patch_registry_in_validator(monkeypatch):
    """Patch ``services.workflow_validator.get_node_class`` (the lookup
    used by the validator)."""
    registry = {"fakeNodeless": _FakeNodelessClass, "fakeNode": _FakeNodeClass}
    monkeypatch.setattr(
        "services.workflow_validator.get_node_class",
        lambda t: registry.get(t),
    )
    # workflow_import.extract_requirements imports get_node_class too.
    monkeypatch.setattr(
        "services.workflow_import.get_node_class",
        lambda t: registry.get(t),
    )


def _patch_broadcaster(monkeypatch):
    """Replace get_status_broadcaster with a stub that records lifecycle
    calls so tests can assert on them without touching real WebSockets."""
    calls: list[tuple[str, dict]] = []

    class _StubBroadcaster:
        async def broadcast_workflow_lifecycle(self, stage: str, **kwargs):
            calls.append((stage, kwargs))

    stub = _StubBroadcaster()
    monkeypatch.setattr(
        "services.status_broadcaster.get_status_broadcaster", lambda: stub,
    )
    return stub, calls


# ---------------------------------------------------------------------------
# extract_requirements + cross_check_credentials + check_name_conflict
# ---------------------------------------------------------------------------


class TestExtractRequirements:
    def test_pulls_credential_ids_and_node_versions(self, monkeypatch):
        from services.workflow_import import extract_requirements

        monkeypatch.setattr(
            "services.workflow_import.get_node_class",
            lambda t: {
                "fakeNode": _FakeNodeClass,
                "fakeNodeless": _FakeNodelessClass,
            }.get(t),
        )

        reqs = extract_requirements([
            {"id": "n1", "type": "fakeNode"},
            {"id": "n2", "type": "fakeNodeless"},
        ])
        cred_ids = [c["provider_id"] for c in reqs["credentials"]]
        types = [n["type"] for n in reqs["nodes"]]
        assert "fake_provider" in cred_ids
        assert "fakeNode" in types
        assert "fakeNodeless" in types

    def test_ignores_unknown_types(self, monkeypatch):
        from services.workflow_import import extract_requirements

        monkeypatch.setattr(
            "services.workflow_import.get_node_class",
            lambda t: None,
        )
        reqs = extract_requirements([{"id": "n1", "type": "unknown"}])
        assert reqs == {"credentials": [], "nodes": []}


class TestCrossCheckCredentials:
    async def test_reports_missing_with_display_info(self, monkeypatch):
        from services.workflow_import import cross_check_credentials

        monkeypatch.setattr(
            "services.plugin.credential.CREDENTIAL_REGISTRY",
            {"fake_provider": _FakeCredential},
        )
        auth = _fake_auth(has_valid_key_return=False)

        missing = await cross_check_credentials(
            {"credentials": [{"provider_id": "fake_provider"}]}, auth
        )
        assert len(missing) == 1
        assert missing[0]["provider_id"] == "fake_provider"
        assert "display_name" in missing[0]
        assert missing[0]["kind"] == "api_key"

    async def test_empty_when_all_stored(self, monkeypatch):
        from services.workflow_import import cross_check_credentials

        auth = _fake_auth(has_valid_key_return=True)
        missing = await cross_check_credentials(
            {"credentials": [{"provider_id": "x"}]}, auth
        )
        assert missing == []


class TestCheckNameConflict:
    async def test_detects_conflict_and_suggests_name(self):
        from services.workflow_import import check_name_conflict

        db = _fake_database(existing_names=["My Flow", "Other"])
        result = await check_name_conflict("My Flow", db)
        assert result["has_conflict"] is True
        assert result["suggested_name"] == "My Flow (imported)"

    async def test_increments_suffix_when_suggestion_also_conflicts(self):
        from services.workflow_import import check_name_conflict

        db = _fake_database(existing_names=["A", "A (imported)"])
        result = await check_name_conflict("A", db)
        assert result["suggested_name"] == "A (imported) 2"

    async def test_no_conflict_returns_none(self):
        from services.workflow_import import check_name_conflict

        db = _fake_database(existing_names=["Other"])
        result = await check_name_conflict("Unique", db)
        assert result["has_conflict"] is False
        assert result["suggested_name"] is None


class TestCloudEventsBroadcastShape:
    """The save path emits a real WorkflowEvent envelope. Static-source
    check that the broadcaster method uses the typed factory."""

    def test_broadcaster_uses_workflow_lifecycle_factory(self):
        import inspect

        from services import status_broadcaster

        src = inspect.getsource(status_broadcaster.StatusBroadcaster.broadcast_workflow_lifecycle)
        assert "WorkflowEvent.workflow_lifecycle" in src, (
            "broadcast_workflow_lifecycle must build the envelope via the "
            "typed factory so the dataschema URI + reverse-DNS type prefix "
            "are consistent with the rest of the CloudEvents surface."
        )
        assert '"workflow_lifecycle"' in src, (
            "wire-format key must be 'workflow_lifecycle' so the frontend "
            "WebSocketContext.tsx case statement routes correctly."
        )

    def test_import_workflow_emits_imported_lifecycle(self):
        import inspect

        from services import workflow_import

        src = inspect.getsource(workflow_import.import_workflow)
        assert "broadcast_workflow_lifecycle" in src, (
            "import_workflow save path must broadcast via "
            "broadcast_workflow_lifecycle so connected clients refresh."
        )
        assert '"imported"' in src, (
            "import_workflow must use the 'imported' lifecycle stage "
            "(matches WorkflowEvent.workflow_lifecycle Literal)."
        )
