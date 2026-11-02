"""The pinned Temporal CLI and the database its dev server may open.

RFC-0004 D1 and RFC-0005 E16: the CLI is a checksum pin, and a database
the pinned server minor did not create is deleted with every older CLI
file, so the pinned server starts from an empty database. RFC-0006 T17:
the dev server runs with fairness and one task-queue partition, and a
database created with other partition values starts from new too.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.temporal import _install, _runtime


def _db_files(db: Path) -> list[Path]:
    return [db, Path(f"{db}-wal"), Path(f"{db}-shm")]


def _write_db(db: Path) -> None:
    for path in _db_files(db):
        path.write_bytes(b"temporal state")


ONE_PARTITION = {"matching.numTaskqueueReadPartitions": 1, "matching.numTaskqueueWritePartitions": 1}


def _write_marker(db: Path, server_version: str, partitions: dict | None = ONE_PARTITION) -> None:
    record = {"server_version": server_version}
    if partitions is not None:
        record["partitions"] = partitions
    _install.marker_path(db).write_text(json.dumps(record), encoding="utf-8")


def _read_marker(db: Path) -> dict:
    return json.loads(_install.marker_path(db).read_text(encoding="utf-8"))


def _same_minor_other_patch() -> str:
    major, minor, _patch = _install.cli_pin()["server_version"].split(".")
    return f"{major}.{minor}.99"


def _other_minor() -> str:
    major, minor, _patch = _install.cli_pin()["server_version"].split(".")
    return f"{major}.{int(minor) - 1}.0"


def _old_clis(root: Path) -> list[Path]:
    """Lay out the CLI files an install may hold from before the pin, plus the pinned folder."""
    archive = root / "temporal_cli_latest_windows_amd64.zip"
    archive.write_bytes(b"unpinned archive")
    extracted = root / "temporal_cli_latest_windows_amd64.zip.unzip"
    extracted.mkdir()
    (extracted / "temporal.exe").write_bytes(b"unpinned")
    older = root / "1.8.0" / "archive.untar"
    older.mkdir(parents=True)
    (older / "temporal").write_bytes(b"older")
    pinned = root / _install.cli_pin()["version"] / "archive.untar"
    pinned.mkdir(parents=True)
    (pinned / "temporal").write_bytes(b"pinned")
    return [archive, extracted, root / "1.8.0"]


def test_pin_names_one_exact_release_with_a_hash_for_every_platform():
    pin = _install.cli_pin()

    assert "latest" not in _install.PIN_PATH.read_text(encoding="utf-8")
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin["version"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin["server_version"])
    assert "{version}" in pin["base"]
    assert set(pin["assets"]) == {f"{os_}_{arch}" for os_, arch in _install._CLI_PLATFORM_MAP.values()}
    for key, asset in pin["assets"].items():
        extension = "zip" if key.startswith("windows_") else "tar.gz"
        assert asset["file"] == f"temporal_cli_{pin['version']}_{key}.{extension}"
        assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])


def test_pin_turns_on_fairness_with_one_task_queue_partition():
    assert _install.cli_pin()["dynamic_config"] == {
        "matching.enableFairness": True, **ONE_PARTITION,
        # Pins the server default the stuck-runs count relies on (1b.38b).
        "system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute": 5,
    }


def test_the_dev_server_passes_the_pin_dynamic_config_on_every_start(tmp_path):
    settings = SimpleNamespace(
        temporal_graceful_shutdown_seconds=5,
        temporal_sqlite_path="temporal.db",
        temporal_frontend_grpc_port=7233,
        temporal_ui_port=8233,
        temporal_namespace="default",
        _resolve_under_data=lambda path: str(tmp_path / path),
    )
    runtime = _runtime.TemporalServerRuntime(settings=settings)
    runtime._binaries = {"temporal": tmp_path / "temporal"}

    argv = runtime.argv()

    values = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--dynamic-config-value"]
    assert values == [
        "matching.enableFairness=true",
        "matching.numTaskqueueReadPartitions=1",
        "matching.numTaskqueueWritePartitions=1",
        "system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute=5",
    ]


def test_the_dev_server_pins_its_http_and_metrics_ports(tmp_path, monkeypatch):
    # Unpinned, each takes a random free port the network policy cannot refuse (1b.33).
    for key in ("TEMPORAL_HTTP_PORT", "TEMPORAL_METRICS_PORT"):
        monkeypatch.delenv(key, raising=False)
    settings = SimpleNamespace(
        temporal_graceful_shutdown_seconds=5, temporal_sqlite_path="temporal.db", temporal_frontend_grpc_port=7233,
        temporal_ui_port=8233, temporal_namespace="default", _resolve_under_data=lambda path: str(tmp_path / path),
    )
    runtime = _runtime.TemporalServerRuntime(settings=settings)
    runtime._binaries = {"temporal": tmp_path / "temporal"}

    argv = runtime.argv()

    assert argv[argv.index("--http-port") + 1] == "5684"
    assert argv[argv.index("--metrics-port") + 1] == "5685"


def test_fetch_verifies_the_pinned_archive_into_its_versioned_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(_install, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(_install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_install.platform, "machine", lambda: "x86_64")
    calls = []

    def fake_retrieve(**kwargs):
        calls.append(kwargs)
        extracted = Path(kwargs["path"]) / f"{kwargs['fname']}.untar"
        extracted.mkdir(parents=True)
        binary = extracted / "temporal"
        binary.write_bytes(b"pinned")
        return [str(extracted / "LICENSE"), str(binary)]

    monkeypatch.setattr(_install.pooch, "retrieve", fake_retrieve)

    binary = _install._fetch_cli_sync()

    pin = _install.cli_pin()
    asset = pin["assets"]["linux_amd64"]
    assert len(calls) == 1
    assert calls[0]["url"].endswith(f"/v{pin['version']}/{asset['file']}")
    assert "latest" not in calls[0]["url"]
    assert calls[0]["known_hash"] == f"sha256:{asset['sha256']}"
    assert Path(calls[0]["path"]) == tmp_path / pin["version"]
    assert calls[0]["fname"] == asset["file"]
    assert binary == tmp_path / pin["version"] / f"{asset['file']}.untar" / "temporal"


def test_binary_versions_reads_the_cli_and_server_it_reports(monkeypatch):
    outputs = iter(["temporal version 1.9.1 (Server 1.32.0, UI 2.54.1)\n", "unexpected\n"])

    def fake_run(argv, **kwargs):
        assert argv[1:] == ["--version"]
        return SimpleNamespace(stdout=next(outputs))

    monkeypatch.setattr(_install.subprocess, "run", fake_run)

    assert _install.binary_versions(Path("temporal")) == {"cli_version": "1.9.1", "server_version": "1.32.0"}
    with pytest.raises(RuntimeError, match="Cannot read"):
        _install.binary_versions(Path("temporal"))


def test_only_the_pinned_cli_counts_as_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(_install, "_cache_dir", lambda: tmp_path)
    unpinned = tmp_path / "temporal_cli_latest_windows_amd64.zip.unzip"
    unpinned.mkdir()
    (unpinned / _install._binary_name()).write_bytes(b"unpinned")

    assert _install.binary_installed() is False

    pinned = tmp_path / _install.cli_pin()["version"] / "archive.unzip"
    pinned.mkdir(parents=True)
    (pinned / _install._binary_name()).write_bytes(b"pinned")

    assert _install.binary_installed() is True


def test_a_new_install_records_the_pinned_server_beside_its_database(tmp_path, monkeypatch):
    monkeypatch.setattr(_install, "_cache_dir", lambda: tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    db = tmp_path / "temporal.db"

    _install.prepare_database(db)

    pin = _install.cli_pin()
    assert not db.exists()
    marker = _read_marker(db)
    assert marker["cli_version"] == pin["version"]
    assert marker["server_version"] == pin["server_version"]
    assert marker["partitions"] == ONE_PARTITION
    assert marker["created_at"]


def test_an_unmarked_database_starts_from_new_with_the_old_clis_deleted(tmp_path, monkeypatch):
    root = tmp_path / "packages"
    root.mkdir()
    monkeypatch.setattr(_install, "_cache_dir", lambda: root)
    old = _old_clis(root)
    db = tmp_path / "temporal.db"
    _write_db(db)

    _install.prepare_database(db)

    assert not any(path.exists() for path in _db_files(db))
    assert not any(path.exists() for path in old)
    assert (root / _install.cli_pin()["version"] / "archive.untar" / "temporal").read_bytes() == b"pinned"
    assert _read_marker(db)["server_version"] == _install.cli_pin()["server_version"]


def test_another_server_minor_starts_from_new(tmp_path, monkeypatch):
    root = tmp_path / "packages"
    root.mkdir()
    monkeypatch.setattr(_install, "_cache_dir", lambda: root)
    db = tmp_path / "temporal.db"
    _write_db(db)
    _write_marker(db, _other_minor())

    _install.prepare_database(db)

    assert not any(path.exists() for path in _db_files(db))
    assert _read_marker(db)["server_version"] == _install.cli_pin()["server_version"]


def test_a_database_created_with_other_partitions_starts_from_new(tmp_path, monkeypatch):
    root = tmp_path / "packages"
    root.mkdir()
    monkeypatch.setattr(_install, "_cache_dir", lambda: root)
    pinned_server = _install.cli_pin()["server_version"]
    for partitions in (None, {"matching.numTaskqueueReadPartitions": 4, "matching.numTaskqueueWritePartitions": 4}):
        db = tmp_path / "temporal.db"
        _write_db(db)
        _write_marker(db, pinned_server, partitions)

        _install.prepare_database(db)

        assert not any(path.exists() for path in _db_files(db))
        assert _read_marker(db)["partitions"] == ONE_PARTITION


def test_an_unreadable_marker_starts_from_new(tmp_path, monkeypatch):
    root = tmp_path / "packages"
    root.mkdir()
    monkeypatch.setattr(_install, "_cache_dir", lambda: root)
    db = tmp_path / "temporal.db"
    _write_db(db)
    _install.marker_path(db).write_text("not json", encoding="utf-8")

    _install.prepare_database(db)

    assert not any(path.exists() for path in _db_files(db))
    assert _read_marker(db)["server_version"] == _install.cli_pin()["server_version"]


def test_a_patch_release_of_the_same_minor_keeps_the_database(tmp_path, monkeypatch):
    root = tmp_path / "packages"
    root.mkdir()
    monkeypatch.setattr(_install, "_cache_dir", lambda: root)
    old = _old_clis(root)
    db = tmp_path / "temporal.db"
    _write_db(db)
    _write_marker(db, _same_minor_other_patch())
    before = _install.marker_path(db).read_bytes()

    _install.prepare_database(db)

    assert all(path.read_bytes() == b"temporal state" for path in _db_files(db))
    assert _install.marker_path(db).read_bytes() == before
    assert all(path.exists() for path in old)


def test_an_old_cli_that_cannot_be_deleted_does_not_stop_the_start(tmp_path, monkeypatch):
    root = tmp_path / "packages"
    root.mkdir()
    monkeypatch.setattr(_install, "_cache_dir", lambda: root)
    _old_clis(root)
    db = tmp_path / "temporal.db"
    _write_db(db)

    def refuse(path, *args, **kwargs):
        raise PermissionError(f"in use: {path}")

    monkeypatch.setattr(_install.shutil, "rmtree", refuse)

    _install.prepare_database(db)

    assert not any(path.exists() for path in _db_files(db))
    assert _read_marker(db)["server_version"] == _install.cli_pin()["server_version"]


def test_the_dev_server_prepares_its_database_before_it_spawns(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        temporal_graceful_shutdown_seconds=5,
        temporal_sqlite_path="data/temporal.db",
        _resolve_under_data=lambda path: str(tmp_path / path),
    )
    runtime = _runtime.TemporalServerRuntime(settings=settings)
    prepared = []

    async def fake_binaries():
        return {"temporal": tmp_path / "temporal"}

    monkeypatch.setattr(_install, "ensure_temporal_binaries", fake_binaries)
    monkeypatch.setattr(_install, "prepare_database", prepared.append)

    asyncio.run(runtime._pre_spawn())

    assert prepared == [tmp_path / "data" / "temporal.db"]
    assert runtime.binary_path() == tmp_path / "temporal"
    assert (tmp_path / "data").is_dir()
