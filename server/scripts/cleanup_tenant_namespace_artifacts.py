"""One-time cleanup of pre-fix artifacts created in the wrong namespace.

Before the tenant-namespace fix (fix/tenant-namespace-artifacts), cron
Schedules and canary listeners for tenant workflows were always created
against the DEFAULT Temporal namespace (``container.temporal_client()``),
while the workflow's control row recorded the tenant namespace
(e.g. ``addval-connect``).  Consequences on a live deployment:

  * A stale cron Schedule (e.g. ``VTask_1-Cron_Scheduler``) kept firing
    every interval in ``default``, spawning ``MachinaWorkflow`` runs that
    failed (missing tenant credentials, wrong worker pool).
  * Rows in tenant DBs carried ``workflows.namespace='default'`` from
    before the namespace-column migration, so the UI (which filters by
    namespace) showed them under the wrong org.

What this script does (both steps idempotent — safe to re-run):

  1. Sweep (Temporal): for every Schedule in the default namespace whose
     ``EventWorkflowId`` belongs to a workflow that actually lives in a
     tenant namespace's workflow.db, pause it (to avoid a firing mid-
     cleanup) then delete it.  Re-deploy the workflow AFTER the fix so a
     fresh Schedule is created in the correct tenant namespace first.

  2. Retrofill (DB): in every tenant namespace's workflow.db, rewrite
     ``workflows.namespace`` from 'default' to the tenant namespace for
     the pre-migration rows.

Usage (as the app user on the host, app STOPPED or --force):
    python -m scripts.cleanup_tenant_namespace_artifacts [--data-dir DIR]
        [--temporal-host HOST] [--temporal-port PORT] [--dry-run]
        [--force] [--sweep-only] [--retrofill-only]

Exit codes: 0 = clean (or actions applied), 1 = error, 2 = refused
(app appears to be running and --force not passed).
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sqlite3
import sys
from pathlib import Path

DEFAULT_NS = "default"


# ---------------------------------------------------------------------------
# Step 2: DB retrofill
# ---------------------------------------------------------------------------


def _retrofill(namespaces_dir: Path, *, dry_run: bool, default_ns: str) -> list[str]:
    """Fix ``workflows.namespace`` rows written before the migration.

    Rows in a tenant namespace's workflow.db carrying ``namespace='default'``
    are exactly the pre-migration rows (post-migration rows inherit the
    active namespace = the tenant). Rewriting them to the enclosing
    namespace restores the UI filter ``routers/database.py`` applies.
    """
    actions: list[str] = []
    if not namespaces_dir.exists():
        return actions

    for ns_dir in sorted(p for p in namespaces_dir.iterdir() if p.is_dir()):
        ns_name = ns_dir.name
        if ns_name == default_ns:
            continue
        db_path = ns_dir / "workflow.db"
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name FROM workflows WHERE namespace = ?", (default_ns,))
            rows = cur.fetchall()
            for wf_id, wf_name in rows:
                label = (
                    f"[retrofill][{ns_name}] workflow {wf_id}"
                    + (f" ({wf_name})" if wf_name else "")
                )
                if dry_run:
                    actions.append(f"{label}: WOULD set namespace='{ns_name}'")
                else:
                    cur.execute(
                        "UPDATE workflows SET namespace = ? WHERE id = ? AND namespace = ?",
                        (ns_name, wf_id, default_ns),
                    )
                    actions.append(f"{label}: namespace set to '{ns_name}'")
            if rows and not dry_run:
                conn.commit()
        finally:
            conn.close()
    return actions


def _tenant_workflows(namespaces_dir: Path, default_ns: str) -> dict[str, str]:
    """Map workflow_id -> tenant namespace for every workflow defined in a
    tenant namespace's workflow.db (used to attribute orphans)."""
    mapping: dict[str, str] = {}
    if not namespaces_dir.exists():
        return mapping
    for ns_dir in sorted(p for p in namespaces_dir.iterdir() if p.is_dir()):
        ns_name = ns_dir.name
        if ns_name == default_ns:
            continue
        db_path = ns_dir / "workflow.db"
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM workflows")
            for (wf_id,) in cur.fetchall():
                mapping.setdefault(wf_id, ns_name)
        finally:
            conn.close()
    return mapping


# ---------------------------------------------------------------------------
# Step 1: Temporal sweep
# ---------------------------------------------------------------------------


async def _sweep_temporal(
    temporal_host: str,
    temporal_port: str,
    namespaces_dir: Path,
    default_ns: str,
    *,
    dry_run: bool,
) -> list[str]:
    """Pause + delete orphan Schedules/listeners in ``default``.

    A Schedule is orphaned when its ``EventWorkflowId`` Search Attribute
    references a workflow that lives in a tenant namespace's workflow.db —
    i.e. the schedule was created in the wrong namespace before the fix.
    """
    actions: list[str] = []
    tenant_map = _tenant_workflows(namespaces_dir, default_ns)
    if not tenant_map:
        return ["[sweep] no tenant workflows found; nothing to attribute"]

    try:
        from temporalio.client import Client
    except ImportError:
        return ["[sweep] temporalio not installed; skipping Temporal sweep"]

    client = await Client.connect(f"{temporal_host}:{temporal_port}", namespace=default_ns)

    # --- Schedules ---
    iterator = await client.list_schedules()
    async for desc in iterator:
        handle = client.get_schedule_handle(desc.id)
        detail = await handle.describe()
        # keyword-value pairs; SDK exposes .search_attributes as a
        # TypedSearchAttributes-ish collection — normalize to a dict.
        sa_map: dict[str, str] = {}
        for pair in getattr(detail, "search_attributes", None) or []:
            sa_map[pair.key.name] = pair.value
        event_wf = sa_map.get("EventWorkflowId")
        if not event_wf:
            continue
        tenant_ns = tenant_map.get(event_wf)
        if tenant_ns is None:
            continue  # schedule genuinely belongs to a default-namespace workflow
        label = f"[sweep] orphan Schedule '{desc.id}' (EventWorkflowId={event_wf}, tenant={tenant_ns})"
        if dry_run:
            actions.append(f"{label}: WOULD pause + delete")
        else:
            try:
                await handle.pause(note="pre-fix orphan cleanup")
                actions.append(f"{label}: paused")
            except Exception as exc:  # noqa: BLE001 — already paused/finished
                actions.append(f"{label}: pause skipped ({exc})")
            await handle.delete()
            actions.append(f"{label}: deleted")

    # --- Canary listeners (running TriggerListenerWorkflow / PollingTriggerWorkflow
    # in default whose EventWorkflowId belongs to a tenant) ---
    wf_types_in = ", ".join(f"'{t}'" for t in ("TriggerListenerWorkflow", "PollingTriggerWorkflow"))
    query = f"ExecutionStatus='Running' AND WorkflowType IN ({wf_types_in})"
    try:
        async for wf in client.list_workflows(query=query):
            event_wf = None
            for pair in getattr(wf, "search_attributes", None) or []:
                if pair.key.name == "EventWorkflowId":
                    event_wf = pair.value
                    break
            if event_wf is None:
                continue
            tenant_ns = tenant_map.get(event_wf)
            if tenant_ns is None:
                continue
            label = f"[sweep] orphan listener {wf.id} (EventWorkflowId={event_wf}, tenant={tenant_ns})"
            if dry_run:
                actions.append(f"{label}: WOULD cancel")
            else:
                await client.get_workflow_handle(wf.id).cancel()
                actions.append(f"{label}: cancelled")
    except Exception as exc:  # noqa: BLE001 — visibility query may be unsupported
        actions.append(f"[sweep] listener visibility scan skipped ({exc})")

    return actions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _app_appears_running() -> bool:
    """Heuristic: refuse cleanup while the backend listens on :8000."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", 8000)) == 0
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", default=None, help="override ~/.opencompany")
    parser.add_argument("--temporal-host", default="localhost")
    parser.add_argument("--temporal-port", default="5681")
    parser.add_argument("--namespace", default=DEFAULT_NS, help="default namespace name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="allow while app running")
    parser.add_argument("--sweep-only", action="store_true")
    parser.add_argument("--retrofill-only", action="store_true")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir or (Path.home() / ".opencompany"))
    namespaces_dir = data_dir / "namespaces"

    if not args.force and _app_appears_running():
        print(
            "Refusing to run while the app appears to be listening on :8000. "
            "Stop the service or pass --force.",
            file=sys.stderr,
        )
        return 2

    actions: list[str] = []

    if not args.retrofill_only:
        actions += asyncio.run(
            _sweep_temporal(
                args.temporal_host,
                args.temporal_port,
                namespaces_dir,
                args.namespace,
                dry_run=args.dry_run,
            )
        )

    if not args.sweep_only:
        actions += _retrofill(namespaces_dir, dry_run=args.dry_run, default_ns=args.namespace)

    for a in actions:
        print(a)
    if not actions:
        print("Nothing to do (all namespaces clean).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
