"""Tests for the compile-pipeline additions to ``machina build``.

Covers the two new steps wired into ``cli.commands.build.build_command``:

- ``[3/6] Building Node.js sidecar`` -> ``pnpm --filter machinaos-nodejs-executor run build``
- ``[5/6] Compiling Python bytecode`` -> ``uv run python -O -m compileall -q -j 0 ...``

The orchestrator is exercised once per test with every external surface
mocked (toolchain probes, ``run``, ``project_root``) so the assertions
focus on *which* commands fire, in what *order*, and with the right
*flags* — not on subprocess behaviour. ``test_build.py`` already covers
the underlying ``run`` and ``capture`` helpers in isolation.

The set of source dirs that compileall walks comes from the public
``build.COMPILEALL_SOURCE_DIRS`` constant — tests reference that
constant rather than duplicating the list, so a future addition flows
to the assertion automatically.

Refer to ``docs-internal/release_build_pipeline.md`` for context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from unittest.mock import patch

from cli.commands import build


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_build_capture_invocations(tmp_path: Path) -> list[dict]:
    """Run ``build_command()`` with all external I/O mocked.

    Returns a list of dicts ``{"argv": [...], **kwargs}`` — one per call
    to ``cli.run.run`` — in the order the orchestrator emitted them.
    Preserving kwargs lets tests assert ``check=False`` and ``cwd=...``.
    """
    captured: list[dict] = []

    def fake_run(argv, **kwargs):
        captured.append({"argv": list(argv), **kwargs})
        return 0

    def fake_capture(argv):
        # ``_check_python`` parses for major.minor and needs Python 3.12+.
        if argv and argv[0].startswith("python"):
            return "Python 3.12.5"
        return "v1.0.0"

    # Step [6/6] invokes ``services.temporal._install`` via ``uv_run`` to
    # materialise the Temporal binaries at build time. ``fake_run``
    # captures the argv without actually spawning, so pooch never fires.
    with (
        patch.object(build, "run", side_effect=fake_run),
        patch.object(build, "capture", side_effect=fake_capture),
        patch.object(build, "_which_python", return_value="python"),
        patch.object(build, "_ensure_uv"),
        patch.object(build, "project_root", return_value=tmp_path),
    ):
        build.build_command()

    return captured


def _find_call(
    captured: list[dict], predicate: Callable[[list[str]], bool]
) -> tuple[int, dict] | None:
    """First (index, call-dict) pair matching the predicate, or ``None``."""
    for idx, call in enumerate(captured):
        if predicate(call["argv"]):
            return idx, call
    return None


# ---------------------------------------------------------------------------
# COMPILEALL_SOURCE_DIRS constant
# ---------------------------------------------------------------------------


def test_source_dirs_constant_excludes_venv_and_tests():
    """The bytecode-compile path list must skip ``.venv/`` and
    ``tests/``: ``.venv/`` is wasted work (uv compiles deps at install
    time, plus packages like crawlee ship non-Python cookiecutter
    templates that would log spurious errors), and ``tests/`` ships
    outside the production tarball.
    """
    assert ".venv" not in build.COMPILEALL_SOURCE_DIRS
    assert "tests" not in build.COMPILEALL_SOURCE_DIRS
    assert "." not in build.COMPILEALL_SOURCE_DIRS


def test_source_dirs_constant_covers_runtime_modules():
    """Whatever's in this list is the surface area the runtime imports
    at startup. If a new top-level package or entry-point module is
    added to ``server/`` and the runtime imports it, it should be in
    this list — otherwise that code pays bytecode-compile cost on
    first import in production.
    """
    # Core dirs that the runtime always imports at startup. Anyone
    # adding a new top-level dir under server/ that's imported on
    # boot should add it to COMPILEALL_SOURCE_DIRS too.
    must_include = {"services", "core", "nodes", "routers", "models", "middleware"}
    actual = set(build.COMPILEALL_SOURCE_DIRS)
    missing = must_include - actual
    assert not missing, f"COMPILEALL_SOURCE_DIRS missing required dirs: {missing}"
    # And the entry-point modules.
    assert "main.py" in actual
    assert "constants.py" in actual


# ---------------------------------------------------------------------------
# Sidecar bundle step ([3/6])
# ---------------------------------------------------------------------------


def test_build_invokes_sidecar_pnpm_filter(tmp_path: Path):
    """[3/6] Build Node.js sidecar via the package's ``build`` script."""
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_call(
        captured,
        lambda c: c[:1] == ["pnpm"]
        and "--filter" in c
        and "machinaos-nodejs-executor" in c,
    )
    assert match is not None, (
        "expected `pnpm --filter machinaos-nodejs-executor run build` in "
        f"{[c['argv'] for c in captured]}"
    )
    argv = match[1]["argv"]
    assert (
        "run" in argv and "build" in argv
    ), f"sidecar step must invoke the `build` script, got {argv}"


def test_sidecar_build_runs_after_client_build(tmp_path: Path):
    """Sidecar bundle runs AFTER the client build (the workspace install
    + client build populate ``server/nodejs/node_modules`` with esbuild
    first).
    """
    captured = _run_build_capture_invocations(tmp_path)
    client = _find_call(captured, lambda c: "react-flow-client" in c)
    sidecar = _find_call(captured, lambda c: "machinaos-nodejs-executor" in c)
    assert client is not None and sidecar is not None
    assert (
        client[0] < sidecar[0]
    ), f"client build idx {client[0]} must precede sidecar bundle idx {sidecar[0]}"


def test_only_one_sidecar_bundle_invocation(tmp_path: Path):
    """Sidecar bundle must fire exactly once per build."""
    captured = _run_build_capture_invocations(tmp_path)
    matches = [c for c in captured if "machinaos-nodejs-executor" in c["argv"]]
    assert len(matches) == 1, f"expected exactly 1 sidecar bundle, got {len(matches)}"


# ---------------------------------------------------------------------------
# Python bytecode compile step ([5/6])
# ---------------------------------------------------------------------------


def test_compileall_uses_optimised_flag_quiet_and_parallel(tmp_path: Path):
    """``-O`` strips asserts; ``-q`` silences per-file output; ``-j 0``
    parallelises across all CPU cores.
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_call(captured, lambda c: "compileall" in c)
    assert (
        match is not None
    ), f"compileall step missing in {[c['argv'] for c in captured]}"
    argv = match[1]["argv"]
    assert "-O" in argv, "must use -O for .opt-1.pyc output"
    assert "-q" in argv, "must use -q to silence per-file logging"
    assert "-j" in argv, "must enable parallelism via -j"
    j_idx = argv.index("-j")
    assert argv[j_idx + 1] == "0", "-j 0 means 'use all CPU cores'"


def test_compileall_runs_via_uv_run_python(tmp_path: Path):
    """Compileall must go through ``uv run --no-sync python`` (built by
    :func:`cli.run.uv_run`) so the project's pinned workspace interpreter
    is used regardless of what ``python`` is on PATH, without re-syncing
    the lockfile mid-build.
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_call(captured, lambda c: "compileall" in c)
    assert match is not None
    argv = match[1]["argv"]
    assert argv[:6] == [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-O",
        "-m",
    ], f"expected `uv run --no-sync python -O -m compileall ...`, got {argv[:7]}"


def test_compileall_path_list_matches_source_dirs_constant(tmp_path: Path):
    """The argv passed to compileall must end with exactly the dirs +
    modules listed in ``COMPILEALL_SOURCE_DIRS`` — no fewer (would skip
    boot-imported code), no more (would re-introduce ``.venv/`` /
    ``tests/`` regression).
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_call(captured, lambda c: "compileall" in c)
    assert match is not None
    argv = match[1]["argv"]
    # Trailing args after `-j 0` are the path list.
    j_idx = argv.index("-j")
    paths_passed = tuple(argv[j_idx + 2 :])
    assert paths_passed == build.COMPILEALL_SOURCE_DIRS, (
        f"compileall args drift: build_command passed {paths_passed!r}, "
        f"constant says {build.COMPILEALL_SOURCE_DIRS!r}"
    )


def test_compileall_runs_in_server_dir(tmp_path: Path):
    """Compileall's ``cwd`` must be ``server/`` so the relative paths
    in COMPILEALL_SOURCE_DIRS (``services``, ``main.py``, etc.) resolve.
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_call(captured, lambda c: "compileall" in c)
    assert match is not None
    call = match[1]
    assert (
        call.get("cwd") == tmp_path / "server"
    ), f"compileall must cwd into server/, got {call.get('cwd')}"


def test_compileall_is_non_fatal(tmp_path: Path):
    """Missing or unparseable Python files in the path list must NOT
    fail the build — the runtime regenerates pyc on first import. The
    step must pass ``check=False`` so a borderline file in a future
    source dir doesn't block the rest of the build.
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_call(captured, lambda c: "compileall" in c)
    assert match is not None
    call = match[1]
    assert call.get("check") is False, (
        "compileall must pass check=False so a malformed source file "
        "doesn't block the rest of the build"
    )


def test_only_one_compileall_invocation(tmp_path: Path):
    """The orchestrator must compile bytecode exactly once per build."""
    captured = _run_build_capture_invocations(tmp_path)
    matches = [c for c in captured if "compileall" in c["argv"]]
    assert len(matches) == 1, f"expected exactly 1 compileall call, got {len(matches)}"


# ---------------------------------------------------------------------------
# Temporal install step ([6/6])
# ---------------------------------------------------------------------------


def _find_temporal_install_call(captured: list[dict]) -> tuple[int, dict] | None:
    return _find_call(
        captured,
        lambda c: (
            c[:3] == ["uv", "run", "--no-sync"] and "services.temporal._install" in c
        ),
    )


def test_build_invokes_temporal_install_via_uv_run(tmp_path: Path):
    """[6/6] must invoke ``uv run --no-sync python -m
    services.temporal._install`` so pooch downloads the Temporal
    tarball at build time (idempotent on re-build via the pooch cache).
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_temporal_install_call(captured)
    assert match is not None, (
        "expected temporal install step in " f"{[c['argv'] for c in captured]}"
    )
    argv = match[1]["argv"]
    assert (
        argv[:6]
        == ["uv", "run", "--no-sync", "python", "-m", "services.temporal._install"]
    ), f"expected `uv run --no-sync python -m services.temporal._install`, got {argv[:7]}"


def test_temporal_install_runs_in_server_dir(tmp_path: Path):
    """Temporal install runs under ``server/`` so ``uv run`` resolves the
    workspace venv with ``services.temporal._install`` on sys.path.
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_temporal_install_call(captured)
    assert match is not None
    call = match[1]
    assert (
        call.get("cwd") == tmp_path / "server"
    ), f"temporal install must cwd into server/, got {call.get('cwd')}"


def test_temporal_install_is_fatal_on_failure(tmp_path: Path):
    """Temporal is a required runtime dep, so the install step must NOT
    pass ``check=False`` — a pooch failure (network, SHA mismatch,
    missing asset) has to abort the build cleanly rather than silently
    deferring the failure to ``machina start``.
    """
    captured = _run_build_capture_invocations(tmp_path)
    match = _find_temporal_install_call(captured)
    assert match is not None
    call = match[1]
    # ``check`` defaults to True in ``cli.run.run`` — assert no override.
    assert (
        call.get("check", True) is True
    ), "temporal install must keep check=True so build aborts on fetch failure"


def test_temporal_install_runs_after_uv_sync(tmp_path: Path):
    """Temporal install MUST follow ``uv sync`` because it invokes
    ``uv run python -m services.temporal._install``, which requires the
    workspace venv (and pooch + the install module) to be materialised.
    """
    captured = _run_build_capture_invocations(tmp_path)
    uv_sync = _find_call(captured, lambda c: c[:2] == ["uv", "sync"])
    temporal = _find_temporal_install_call(captured)
    assert uv_sync is not None and temporal is not None
    assert (
        uv_sync[0] < temporal[0]
    ), f"uv_sync idx {uv_sync[0]} must precede temporal install idx {temporal[0]}"


def test_only_one_temporal_install_invocation(tmp_path: Path):
    """Temporal install must fire exactly once per build."""
    captured = _run_build_capture_invocations(tmp_path)
    matches = [c for c in captured if "services.temporal._install" in c["argv"]]
    assert (
        len(matches) == 1
    ), f"expected exactly 1 temporal install call, got {len(matches)}"


# ---------------------------------------------------------------------------
# Step ordering invariants
# ---------------------------------------------------------------------------


def test_pipeline_order_client_then_sidecar_then_uv_then_compileall(tmp_path: Path):
    """Full ordering invariant for the new pipeline:

    1. client build  (``pnpm --filter react-flow-client run build``)
    2. sidecar bundle (``pnpm --filter machinaos-nodejs-executor run build``)
    3. uv sync       (``uv sync``)
    4. compileall    (``uv run python -O -m compileall ...``)

    compileall MUST follow uv sync because it invokes ``uv run python``,
    which requires the venv to exist.
    """
    captured = _run_build_capture_invocations(tmp_path)
    client = _find_call(captured, lambda c: "react-flow-client" in c)
    sidecar = _find_call(captured, lambda c: "machinaos-nodejs-executor" in c)
    uv_sync = _find_call(captured, lambda c: c[:2] == ["uv", "sync"])
    compileall = _find_call(captured, lambda c: "compileall" in c)
    for label, match in (
        ("client", client),
        ("sidecar", sidecar),
        ("uv_sync", uv_sync),
        ("compileall", compileall),
    ):
        assert match is not None, f"missing pipeline step: {label}"
    indices = [client[0], sidecar[0], uv_sync[0], compileall[0]]
    assert indices == sorted(indices), (
        f"pipeline order broken: client={client[0]} sidecar={sidecar[0]} "
        f"uv_sync={uv_sync[0]} compileall={compileall[0]}"
    )
