"""Release command identities and worker/replayer registration cannot drift."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from tests.temporal.workflow_identities import digest, handler_snapshot, identity_snapshot

GOLDEN = Path(__file__).with_name("fixtures") / "workflow_identities.json"


def _markers(snapshot: dict, command: str) -> set[str]:
    return {item["type"].strip("'\"") for module in snapshot.values() for item in module["commands"] if item["command"] == command}


def _vanished_markers(release: dict, current: dict) -> set[str]:
    return _markers(release, "patched") - _markers(current, "patched") - _markers(current, "deprecate_patch")


def _missing_handlers(release: dict, current: dict) -> dict[str, list[str]]:
    missing = {name: sorted(set(handlers) - set(current.get(name, []))) for name, handlers in release.items()}
    return {name: handlers for name, handlers in missing.items() if handlers}


def test_workflow_command_identities_match_release_or_explicit_patch_entry():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    current = identity_snapshot()
    if current == golden["identities"]:
        return
    before, after = digest(golden["identities"]), digest(current)
    entries = [entry for entry in golden.get("patch_entries", []) if entry["before"] == before and entry["after"] == after]
    assert entries, "Temporal activity names/child IDs/markers changed: add a reviewed versioning patch entry and replay N-1 histories"
    markers = _markers(current, "patched") | _markers(current, "deprecate_patch")
    assert all(entry.get("patch") in markers and entry.get("reason") for entry in entries)


def test_patch_markers_move_to_deprecated_before_they_vanish():
    """RFC-0005 §24.3: a marker the release patches stays in the code, as
    ``workflow.patched`` or ``workflow.deprecate_patch``; only one the release
    already deprecated may be removed, a release later."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    vanished = _vanished_markers(golden["identities"], identity_snapshot())
    assert not vanished, f"Patch markers removed without a release of deprecate_patch: {sorted(vanished)}"


def test_the_marker_rule_allows_deprecation_and_catches_removal():
    def snapshot(*commands):
        return {"m.py": {"commands": [{"command": command, "type": f'"{marker}"'} for command, marker in commands]}}

    release = snapshot(("patched", "x-v1"), ("deprecate_patch", "y-v1"))
    assert not _vanished_markers(release, snapshot(("patched", "x-v1")))
    assert not _vanished_markers(release, snapshot(("deprecate_patch", "x-v1")))
    assert _vanished_markers(release, snapshot(("deprecate_patch", "y-v1"))) == {"x-v1"}


def test_message_handlers_are_never_renamed_or_removed():
    """A recorded Signal or Update names its handler, so open runs of the
    release would lose one that is renamed or removed. New handlers are fine."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    missing = _missing_handlers(golden["handlers"], handler_snapshot())
    assert not missing, f"Message handlers renamed or removed: {missing}"


def test_the_handler_rule_allows_additions_and_catches_renames():
    release = {"W": ["signal:pause", "update:set_control_state"]}
    assert not _missing_handlers(release, {"W": ["signal:pause", "signal:wake", "update:set_control_state"]})
    assert _missing_handlers(release, {"W": ["signal:pause", "update:set_state"]}) == {"W": ["update:set_control_state"]}
    assert _missing_handlers(release, {}) == {"W": ["signal:pause", "update:set_control_state"]}


def test_the_handler_inventory_reads_names_from_the_decorators(tmp_path):
    temporal = tmp_path / "services" / "temporal"
    temporal.mkdir(parents=True)
    cron = tmp_path / "nodes" / "scheduler" / "cron_scheduler"
    cron.mkdir(parents=True)
    (cron / "_workflow.py").write_text("", encoding="utf-8")
    (temporal / "probe.py").write_text(textwrap.dedent('''\
        from temporalio import workflow

        NAME = "ProbeWorkflow"


        @workflow.defn(name=NAME, sandboxed=False)
        class Probe:
            @workflow.signal
            def pause(self): ...

            @workflow.update(name="apply")
            async def apply_change(self): ...

            @workflow.query
            def status(self): ...

            @workflow.signal(dynamic=True)
            def other(self, name, args): ...
        '''), encoding="utf-8")

    assert handler_snapshot(tmp_path) == {"ProbeWorkflow": ["query:status", "signal:<dynamic>", "signal:pause", "update:apply"]}


def test_worker_and_replayer_use_the_shared_production_registry():
    from services.temporal.worker import _framework_workflows
    from services.temporal.workflow_registry import framework_workflows, replay_workflows

    assert _framework_workflows is framework_workflows
    names = [item.__temporal_workflow_definition.name for item in replay_workflows()]
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(names) == golden["workflow_types"]
    assert len(names) == len(set(names)), "Duplicate workflow registrations"


def test_plugin_activity_type_versions_retain_the_release_names():
    import nodes  # noqa: F401 -- discover the production node classes
    from services.node_registry import registered_node_classes

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    current = {name: f"node.{cls.type}.v{cls.version}" for name, cls in registered_node_classes().items()}
    # New node types are additive; old names must remain registerable until
    # their N-1 workflows have drained or a reviewed migration is supplied.
    changed = {name: expected for name, expected in golden["plugin_activity_types"].items() if current.get(name) != expected}
    assert not changed, f"Historical plugin activity names changed without compatibility registration: {changed}"
