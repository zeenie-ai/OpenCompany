"""Read-only approval queries other packages use (employee summaries)."""

from __future__ import annotations

from typing import Any, Dict, Iterable

from services.approvals import store


async def pending_approvals_by_workflow(database: Any, workflow_ids: Iterable[str]) -> Dict[str, int]:
    """Drafts waiting for the owner, per workflow."""
    return await store.pending_counts(database, workflow_ids)


__all__ = ["pending_approvals_by_workflow"]
