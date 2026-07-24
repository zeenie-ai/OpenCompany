"""Security and behavior tests for the native workspace backend."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from nodes.filesystem._backend import (
    EMPTY_CONTENT_WARNING,
    WorkspaceBackend,
    perform_string_replacement,
)


def _test_directory() -> Path:
    path = Path.cwd() / f".test-filesystem-backend-{uuid4().hex}"
    path.mkdir()
    return path


def test_backend_rejects_traversal_and_symlink_escape():
    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        outside = test_root / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        backend = WorkspaceBackend(root)

        with pytest.raises(ValueError, match="traversal"):
            backend._resolve_path("../outside/secret.txt")

        link = root / "escape"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("creating symlinks is not permitted on this platform")
        with pytest.raises(ValueError, match="outside workspace"):
            backend._resolve_path("/escape/secret.txt")
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_backend_read_list_glob_and_literal_grep():
    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        docs = root / "docs"
        docs.mkdir(parents=True)
        (docs / "one.md").write_text(
            "alpha\nneedle here\n",
            encoding="utf-8",
        )
        (docs / "two.txt").write_text("needle again\n", encoding="utf-8")
        backend = WorkspaceBackend(root)

        read = backend.read("/docs/one.md", offset=1, limit=1)
        assert read.error is None
        assert read.file_data == {
            "content": "needle here\n",
            "encoding": "utf-8",
        }

        entries = backend.ls_info("/docs")
        assert [entry["path"] for entry in entries] == [
            "/docs/one.md",
            "/docs/two.txt",
        ]
        assert [
            match["path"] for match in backend.glob_info("*.md", "/docs")
        ] == ["/docs/one.md"]
        assert backend.grep_raw("needle", "/docs", glob="*.md") == [
            {"path": "/docs/one.md", "line": 2, "text": "needle here"}
        ]
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_backend_empty_text_preserves_agent_reminder():
    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        root.mkdir()
        (root / "empty.txt").write_bytes(b"")
        (root / "whitespace.txt").write_text(" \n\t", encoding="utf-8")
        backend = WorkspaceBackend(root)

        for path in ("/empty.txt", "/whitespace.txt"):
            result = backend.read(path)
            assert result.error is None
            assert result.file_data == {
                "content": EMPTY_CONTENT_WARNING,
                "encoding": "utf-8",
            }
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_secure_read_rejects_symlink_substituted_after_resolution():
    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        outside = test_root / "outside"
        root.mkdir()
        outside.mkdir()
        victim = root / "victim.txt"
        victim.write_text("safe", encoding="utf-8")
        secret = outside / "secret.txt"
        secret.write_text("outside", encoding="utf-8")
        backend = WorkspaceBackend(root)

        # Resolve while the target is safe, then deterministically substitute
        # a symlink before the protected open. No timing race is involved.
        resolved = backend._resolve_path("/victim.txt")
        victim.unlink()
        try:
            os.symlink(secret, victim)
        except (OSError, NotImplementedError):
            pytest.skip("creating symlinks is not permitted on this platform")

        with pytest.raises((OSError, ValueError)):
            backend.read_text_secure(resolved)
        result = backend.read("/victim.txt")
        assert result.error is not None
        assert "outside workspace" in result.error
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_atomic_write_rejects_target_symlink_substituted_after_resolution():
    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        outside = test_root / "outside"
        root.mkdir()
        outside.mkdir()
        victim = root / "victim.txt"
        victim.write_text("safe", encoding="utf-8")
        secret = outside / "secret.txt"
        secret.write_text("outside", encoding="utf-8")
        backend = WorkspaceBackend(root)

        resolved = backend._resolve_path("/victim.txt")
        victim.unlink()
        try:
            os.symlink(secret, victim)
        except (OSError, NotImplementedError):
            pytest.skip("creating symlinks is not permitted on this platform")

        with pytest.raises((OSError, ValueError)):
            backend.atomic_write_text(resolved, "replacement")
        assert secret.read_text(encoding="utf-8") == "outside"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_atomic_write_rejects_parent_symlink_substituted_after_resolution():
    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        parent = root / "docs"
        detached_parent = root / "detached-docs"
        outside = test_root / "outside"
        parent.mkdir(parents=True)
        outside.mkdir()
        victim = parent / "victim.txt"
        victim.write_text("safe", encoding="utf-8")
        outside_victim = outside / "victim.txt"
        outside_victim.write_text("outside", encoding="utf-8")
        backend = WorkspaceBackend(root)

        resolved = backend._resolve_path("/docs/victim.txt")
        parent.rename(detached_parent)
        try:
            os.symlink(outside, parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("creating symlinks is not permitted on this platform")

        with pytest.raises((OSError, ValueError)):
            backend.atomic_write_text(resolved, "replacement")
        assert outside_victim.read_text(encoding="utf-8") == "outside"
        assert (
            detached_parent / "victim.txt"
        ).read_text(encoding="utf-8") == "safe"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_windows_fallback_revalidates_parent_before_writing_payload(
    monkeypatch,
):
    from nodes.filesystem import _backend as backend_module

    test_root = _test_directory()
    try:
        root = test_root / "workspace"
        parent = root / "docs"
        parent.mkdir(parents=True)
        target = parent / "victim.txt"
        target.write_text("safe", encoding="utf-8")
        real_snapshot = backend_module._windows_parent_snapshot
        snapshot_calls = 0

        def changed_second_snapshot(*args, **kwargs):
            nonlocal snapshot_calls
            snapshot_calls += 1
            current_parent, identities = real_snapshot(*args, **kwargs)
            if snapshot_calls == 2:
                changed = (*identities[:-1], (identities[-1][0], -1))
                return current_parent, changed
            return current_parent, identities

        def fail_if_payload_is_written(*args, **kwargs):
            pytest.fail("payload was written before parent revalidation")

        monkeypatch.setattr(
            backend_module,
            "_windows_parent_snapshot",
            changed_second_snapshot,
        )
        monkeypatch.setattr(
            backend_module,
            "_write_utf8_to_fd",
            fail_if_payload_is_written,
        )

        with pytest.raises(
            ValueError,
            match="parent directory changed",
        ):
            backend_module._atomic_write_text_windows(
                root,
                Path("docs/victim.txt"),
                "replacement",
            )

        assert target.read_text(encoding="utf-8") == "safe"
        assert not list(parent.glob(".opencompany-write-*"))
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_string_replacement_preserves_ambiguity_contract():
    assert perform_string_replacement("foo foo", "foo", "bar") == (
        "Error: String 'foo' appears 2 times in file. Use replace_all=True "
        "to replace all instances, or provide a more specific string with "
        "surrounding context."
    )
    assert perform_string_replacement("foo foo", "foo", "bar", True) == (
        "bar bar",
        2,
    )
