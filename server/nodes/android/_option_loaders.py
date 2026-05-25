"""Android ``loadOptionsMethod`` loaders.

Wave 11.I, milestone M.3. Returns the list of actions supported by each
Android service node. The frontend passes ``node_type`` in params; the
loader maps it to the service's ``action`` enum advertised by the
running Android bridge.

``SERVICE_ID_MAP`` is the single source of truth -- imported from
``_base.py`` instead of being duplicated here (the pre-Wave-11.I
``services/node_option_loaders/android_loaders.py`` carried its own
copy under a different name; that copy is gone now).
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._base import SERVICE_ID_MAP


async def load_service_actions(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return ``[{value, label}]`` for the given Android service node.

    Each service's ``execute_action`` endpoint validates the action at
    call time; the dropdown is convenience UX, not a correctness
    boundary. The frontend falls back to a free-text input if the
    loader returns an empty list (service offline / discovery failed).
    """
    node_type = params.get("node_type") or ""
    service_id = SERVICE_ID_MAP.get(node_type)
    if not service_id:
        return []

    try:
        from services.plugin.deps import get_android_service

        android_svc = get_android_service()
        actions = await android_svc.list_actions(service_id)  # type: ignore[attr-defined]
        return [{"value": a, "label": a.replace("_", " ").title()} for a in actions or []]
    except Exception:
        return []
