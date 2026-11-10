from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from models.pi_checkpoint import PiRuntimeBlob
from services import pi_blobs, pi_runtime


@pytest.mark.asyncio
async def test_immutable_sqlite_blob_cache_is_scoped_and_integrity_checked(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pi.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(PiRuntimeBlob.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def session():
        async with maker() as value:
            yield value
    monkeypatch.setattr(pi_blobs, "get_database", lambda: SimpleNamespace(get_session=session))
    scope = {"workflow_id": "root", "generation_id": "1", "node_id": "coding"}
    payload = {"header": {"version": 3}, "entries": [{"signature": "exact"}], "leaf_id": "id"}
    try:
        first = await pi_blobs.put_blob(scope, "snapshot", payload)
        assert await pi_blobs.put_blob(scope, "snapshot", payload) == first
        assert await pi_blobs.get_blob(scope, first) == payload
        other = await pi_blobs.put_blob({**scope, "generation_id": "2"}, "snapshot", payload)
        assert first["pi_blob"] != other["pi_blob"]
        with pytest.raises(ApplicationError, match="different execution"):
            await pi_blobs.get_blob({**scope, "generation_id": "2"}, first)
        async with session() as db:
            row = await db.get(PiRuntimeBlob, first["pi_blob"])
            row.payload = {"tampered": True}
            await db.commit()
        with pytest.raises(ApplicationError, match="integrity"):
            await pi_blobs.get_blob(scope, first)
    finally:
        await engine.dispose()


def _pi(tmp_path, monkeypatch):
    """A scratch server tree; the resolver reads server/pi next to its own module."""
    root = (tmp_path / "server").resolve()
    (root / "pi").mkdir(parents=True)
    monkeypatch.setattr(pi_runtime, "__file__", str(root / "services" / "pi_runtime.py"))
    return root / "pi"


def _source_checkout(pi, manifest):
    (pi / "src").mkdir()
    (pi / "src" / "index.ts").write_text("export {};", encoding="utf-8")
    package = pi / "node_modules" / "@earendil-works" / "pi-coding-agent"
    package.mkdir(parents=True)
    if manifest is not None:
        (package / "package.json").write_text(manifest, encoding="utf-8")
    return package


def test_the_built_bundle_is_the_host_and_its_folder_holds_the_sdk_manifest(tmp_path, monkeypatch):
    pi = _pi(tmp_path, monkeypatch)
    (pi / "dist").mkdir()
    (pi / "dist" / "index.js").write_text("export {};", encoding="utf-8")
    # A built bundle wins over the source tree beside it.
    _source_checkout(pi, '{"version":"1.1.0"}')
    host = pi_runtime.resolve_pi_host()
    assert host == pi / "dist" / "index.js"
    assert pi_runtime.pi_package_dir(host) == pi / "dist"


def test_an_unbuilt_source_checkout_runs_the_host_from_source(tmp_path, monkeypatch):
    pi = _pi(tmp_path, monkeypatch)
    package = _source_checkout(pi, '{"version":"1.1.0"}')
    host = pi_runtime.resolve_pi_host()
    assert host == pi / "src" / "index.ts"
    assert pi_runtime.pi_package_dir(host) == package


@pytest.mark.parametrize("manifest", ['{"version":"1.2.0"}', "invalid JSON", None])
def test_a_source_checkout_without_the_pinned_sdk_is_terminal(tmp_path, monkeypatch, manifest):
    _source_checkout(_pi(tmp_path, monkeypatch), manifest)
    with pytest.raises(ApplicationError) as error:
        pi_runtime.resolve_pi_host()
    assert error.value.type == "PiRuntimeMissing" and error.value.non_retryable


def test_a_missing_runtime_is_terminal(tmp_path, monkeypatch):
    _pi(tmp_path, monkeypatch)
    with pytest.raises(ApplicationError) as error:
        pi_runtime.resolve_pi_host()
    assert error.value.type == "PiRuntimeMissing" and error.value.non_retryable
