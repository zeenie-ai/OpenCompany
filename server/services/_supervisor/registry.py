"""Cross-plugin registry of live supervisors.

Plugin packages register their supervisor singletons here so the FastAPI
lifespan can stop them all in one shot, and the broadcaster/health
endpoints can enumerate their status without hardcoded per-plugin
imports.

Same pattern as ``services.ws_handler_registry``,
``services.event_waiter.FILTER_BUILDERS``, and
``services.status_broadcaster._SERVICE_REFRESH_CALLBACKS``.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from .base import BaseSupervisor

logger = logging.getLogger(__name__)

_SUPERVISORS: Dict[str, BaseSupervisor] = {}


def register_supervisor(supervisor: BaseSupervisor) -> None:
    """Add a supervisor to the registry (idempotent on label collision)."""
    label = supervisor.label
    existing = _SUPERVISORS.get(label)
    if existing is supervisor:
        return
    if existing is not None:
        raise ValueError(
            f"Supervisor label '{label}' already registered " f"({existing.__class__.__name__} vs {supervisor.__class__.__name__})"
        )
    _SUPERVISORS[label] = supervisor


def list_supervisors() -> List[BaseSupervisor]:
    return list(_SUPERVISORS.values())


def get_supervisor(label: str) -> BaseSupervisor | None:
    return _SUPERVISORS.get(label)


async def shutdown_all_supervisors() -> None:
    """Stop every registered supervisor. Best-effort, errors logged."""
    for supervisor in list_supervisors():
        if not supervisor.is_running():
            continue
        try:
            await supervisor.stop()
        except Exception as exc:
            logger.warning(
                "[supervisor] %s shutdown failed: %s",
                supervisor.label,
                exc,
            )


def status_snapshot_all() -> List[dict]:
    """Status snapshots for every registered supervisor."""
    return [s.status_snapshot() for s in list_supervisors()]
