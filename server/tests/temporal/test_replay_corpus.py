"""Mandatory replay of immutable N-1 histories in a fresh real-SDK process."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile

FIXTURES = Path(__file__).with_name("fixtures")
REPO = Path(__file__).parents[3]


def test_previous_release_corpus_replays_with_production_workflows():
    if os.environ.get("TEMPORAL_REQUIRE_NATIVE_REPLAY") == "1":
        assert Path(os.environ.get("TEMPORAL_TEST_CLI", "")).is_file(), "Native replay CI requires TEMPORAL_TEST_CLI; skipping is forbidden"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.temporal.test_replay_corpus"],
        cwd=REPO / "server", stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=180, env={**os.environ, "DEBUG": "false"},
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-14000:]


async def replay() -> None:
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer
    from services.temporal.workflow_registry import replay_workflows

    manifests = sorted(FIXTURES.glob("*/manifest.json"))
    assert manifests, "N-1 corpus is missing; run the committed-source fixture recorder"
    workflows = replay_workflows()
    names = {definition.__temporal_workflow_definition.name for definition in workflows}
    covered, count = set(), 0
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["source_mode"] == "git-archive" and manifest["origin_dirty"] is False
        commit = manifest["origin_commit"]
        assert len(commit) == 40
        # git archive applies core.autocrlf; git show returns canonical blobs.
        # Reconstruct the EXACT archived bytes the worker loaded, including
        # the recording checkout's explicit line-ending setting, on every OS.
        autocrlf = "true" if manifest["source_archive_autocrlf"] else "false"
        archive = subprocess.check_output([
            "git", "-c", f"core.autocrlf={autocrlf}", "archive", commit, "server/services/temporal",
        ], cwd=REPO)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            for module, expected in manifest["workflow_source_sha256"].items():
                source = files.extractfile(f"server/services/temporal/{module}").read()
                assert hashlib.sha256(source).hexdigest() == expected, f"Fixture source provenance changed: {module}"
        assert {item.stem for item in path.parent.glob("*.json") if item.name != "manifest.json"} == set(manifest["scenarios"])
        for scenario in manifest["scenarios"]:
            document = json.loads((path.parent / f"{scenario}.json").read_text(encoding="utf-8"))
            assert document["scenario"] == scenario and document["histories"]
            for item in document["histories"]:
                history = WorkflowHistory.from_json(item["workflow_id"], json.dumps(item["history"]))
                opening = history.events[0].workflow_execution_started_event_attributes
                covered.add(opening.workflow_type.name)
                assert not any(event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED for event in history.events)
                result = await Replayer(workflows=workflows).replay_workflow(history)
                assert result.replay_failure is None, (scenario, result.replay_failure)
                count += 1
    # A type added since the release has no release history yet; the golden
    # names it in a reviewed entry (test_workflow_identity_golden.py).
    golden = json.loads((FIXTURES / "workflow_identities.json").read_text(encoding="utf-8"))
    added = {name for entry in golden.get("patch_entries", []) for name in entry.get("new_types", [])}
    assert names - added <= covered, f"Production workflow types missing N-1 histories: {sorted(names - added - covered)}"
    print(f"Replayed {count} real N-1 histories covering {len(covered)} production workflow types")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="opencompany-replay-") as directory:
        os.environ["DATA_DIR"] = directory
        asyncio.run(replay())
