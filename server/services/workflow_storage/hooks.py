"""Workflow-deleted hooks.

Packages that keep rows keyed by a workflow (Normal-mode employees, pending
approvals) register a cleanup here instead of the delete path importing
them: the same self-registration shape as the plugin registries, and the
storage layer stays unaware of its dependents.

Hooks run after the workflow row is gone (both the WebSocket and the REST
delete go through ``delete_workflow_with_context_archival``). A failing hook
is logged and never fails the delete or the hooks after it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, List

from core.logging import get_logger

logger = get_logger(__name__)

WorkflowDeletedHook = Callable[[Any, str], Awaitable[None]]

_HOOKS: List[WorkflowDeletedHook] = []


def register_workflow_deleted_hook(hook: WorkflowDeletedHook) -> None:
    """Run ``await hook(database, workflow_id)`` after every workflow delete.
    Registering the same hook twice is a no-op."""
    if hook not in _HOOKS:
        _HOOKS.append(hook)


async def run_workflow_deleted_hooks(database: Any, workflow_id: str) -> None:
    for hook in list(_HOOKS):
        try:
            await hook(database, workflow_id)
        except Exception:
            logger.warning(
                "Workflow-deleted hook failed",
                hook=getattr(hook, "__qualname__", repr(hook)),
                workflow_id=workflow_id,
                exc_info=True,
            )


__all__ = ["WorkflowDeletedHook", "register_workflow_deleted_hook", "run_workflow_deleted_hooks"]
