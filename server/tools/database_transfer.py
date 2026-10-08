"""Offline application metadata transfer; encrypted local stores stay local.

Run with ``python -m tools.database_transfer``. Both applications must be
stopped and deployments/tasks reset before export; this is not live replication.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, func, inspect, select, text
from sqlmodel import SQLModel

from core.config import Settings
from core.database import Database

EXCLUDED_TABLES = frozenset({
    "api_keys", "api_key_validations", "google_connections", "gmail_connections",
    "credentials_metadata", "encrypted_api_keys", "oauth_tokens",
    "cache_entries", "browser_owners", "browser_transient_routes",
    "subagent_concurrency_counters", "subagent_concurrency_permits",
})
SECRET_FIELDS = frozenset({"api_key", "apiKey", "access_token", "refresh_token", "password", "client_secret"})


def _assert_no_inline_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in SECRET_FIELDS and child:
                raise ValueError("Remove inline credentials and enroll 1Password bindings before exporting application metadata")
            _assert_no_inline_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_inline_secrets(child)


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("tables"), dict):
        raise ValueError("Unsupported database transfer format")
    for name, rows in manifest["tables"].items():
        if name in EXCLUDED_TABLES or name not in SQLModel.metadata.tables or not isinstance(rows, list):
            raise ValueError("Unknown or excluded table in database transfer")
        table = SQLModel.metadata.tables[name]
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(table.columns.keys()):
                raise ValueError("Database transfer schema differs from the current application schema")
            if name in {"workflows", "node_parameters", "workflow_control_executions"}:
                _assert_no_inline_secrets(row)
            if name == "workspace_task_records" and row.get("completed_at") is None:
                raise ValueError("Cancel or finish every Workspace task before offline transfer")
            if name == "browser_profile_owners":
                if row.get("task_id"):
                    raise ValueError("Finish Browser cleanup before offline transfer")
                if row.get("owner_id") != "local":
                    raise ValueError("This command transfers local metadata; distributed ownership needs an operator migration")
                if not isinstance(manifest.get("local_owner_machine_id"), str) or not 0 < len(manifest["local_owner_machine_id"]) <= 255:
                    raise ValueError("Offline browser ownership requires its original machine identity")
            if name == "workflow_control_executions" and row.get("status") not in {"reset", "completed", "failed", "cancelled", "terminated"}:
                raise ValueError("Reset every deployed workflow before offline transfer")


async def export_manifest(database: Database) -> dict:
    if database.engine.dialect.name != "sqlite":
        raise ValueError("Export requires a local SQLite application database")
    tables = {}
    async with database.engine.connect() as connection:
        await connection.exec_driver_sql("BEGIN EXCLUSIVE")
        try:
            present = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
            for table in SQLModel.metadata.sorted_tables:
                if table.name in EXCLUDED_TABLES or table.name not in present:
                    continue
                tables[table.name] = [dict(row) for row in (await connection.execute(select(table))).mappings()]
        finally:
            await connection.rollback()
    manifest = {"version": 1, "tables": tables,
                "local_owner_machine_id": database.settings.browser_machine_id or socket.gethostname()}
    # Profiles created by the old SQLite app predate owner metadata. Preserve
    # their original machine instead of letting the first cluster caller adopt
    # them after import. Existing ownership and recovery latches stay intact.
    from models.browser_owners import BrowserProfileOwner
    ownership = tables.setdefault("browser_profile_owners", [])
    owned = {row["profile_id"] for row in ownership}
    for profile in tables.get("browser_profiles", []):
        if profile["id"] not in owned:
            ownership.append(BrowserProfileOwner(profile_id=profile["id"],
                principal_id=profile["owner_id"], owner_id="local").model_dump())
    validate_manifest(manifest)
    return manifest


async def import_manifest(database: Database, manifest: dict) -> None:
    validate_manifest(manifest)
    async with database.engine.begin() as connection:
        if database.engine.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_xact_lock(763746822)"))
        # No destructive overwrite or partial merge: import into a fresh schema.
        for table in SQLModel.metadata.sorted_tables:
            if table.name in EXCLUDED_TABLES:
                continue
            if (await connection.execute(select(func.count()).select_from(table))).scalar_one():
                raise ValueError("Database transfer requires an empty target application database")
        for table in SQLModel.metadata.sorted_tables:
            rows = manifest["tables"].get(table.name, [])
            decoded = []
            for row in rows:
                row = dict(row)
                for column in table.columns:
                    typ = getattr(column.type, "original", column.type)
                    if isinstance(typ, DateTime) and isinstance(row.get(column.name), str):
                        row[column.name] = datetime.fromisoformat(row[column.name])
                decoded.append(row)
            if decoded:
                await connection.execute(table.insert(), decoded)
            if database.engine.dialect.name == "postgresql":
                for column in table.primary_key.columns:
                    if column.autoincrement is False or not isinstance(column.type, Integer):
                        continue
                    sequence = (await connection.execute(text("SELECT pg_get_serial_sequence(:table, :column)"),
                                {"table": table.name, "column": column.name})).scalar_one()
                    if sequence:
                        maximum = (await connection.execute(select(func.max(column)))).scalar_one() or 0
                        await connection.execute(text("SELECT setval(CAST(:sequence AS regclass), :next_id, false)"),
                                                 {"sequence": sequence, "next_id": maximum + 1})
        if manifest["tables"].get("browser_profile_owners"):
            from models.browser_owners import BrowserOwner
            # Keep the imported profile owner unavailable until the original
            # machine explicitly boots its first cluster replica as `local`.
            await connection.execute(BrowserOwner.__table__.insert().values(owner_id="local",
                machine_id=manifest["local_owner_machine_id"], runtime_epoch="offline-import",
                base_url="", heartbeat_at=0))


async def _run(args):
    if not args.offline:
        raise ValueError("Stop local writers and Reset deployed workflows, then acknowledge with --offline")
    if args.operation == "export":
        source = Path(args.sqlite).resolve(strict=True)
        output = Path(args.output)
        if output.exists():
            raise ValueError("Export output already exists")
        database = Database(Settings(distributed_mode=False, database_url_override=f"sqlite+aiosqlite:///{source.as_posix()}"))
    else:
        database = Database(Settings(distributed_mode=False))
        if not database.settings.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("Import requires DATABASE_URL configured for the new shared PostgreSQL database")
    await database.startup()
    try:
        if args.operation == "export":
            manifest = await export_manifest(database)
            with output.open("x", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, default=lambda value: value.isoformat() if isinstance(value, datetime) else value)
        else:
            await import_manifest(database, json.loads(Path(args.input).read_text(encoding="utf-8")))
    finally:
        await database.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    export = commands.add_parser("export")
    export.add_argument("--sqlite", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--offline", action="store_true")
    target = commands.add_parser("import")
    target.add_argument("--input", required=True)
    target.add_argument("--offline", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
