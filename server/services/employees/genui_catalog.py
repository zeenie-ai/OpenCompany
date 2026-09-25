"""The setup-screen vocabulary (``config/genui_catalog.json``), loaded once.

The prompt is generated from it and the reply salvage check reads its
lists; the client's renderer keeps the same lists in
``client/src/features/home/genui/catalog.ts`` (checked by
``tests/test_genui_catalog_sync.py``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "genui_catalog.json"


@lru_cache(maxsize=1)
def load_genui_catalog() -> Mapping[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def component_types() -> FrozenSet[str]:
    return frozenset(load_genui_catalog()["components"])


def action_types() -> FrozenSet[str]:
    return frozenset(load_genui_catalog()["actions"])


def action_aliases() -> Dict[str, str]:
    return dict(load_genui_catalog().get("action_aliases") or {})


def canonical_action(value: Any) -> Optional[str]:
    """The action a Button runs, with old names mapped; None when unknown."""
    if not isinstance(value, str):
        return None
    name = action_aliases().get(value, value)
    return name if name in action_types() else None


def limit(name: str) -> int:
    return int(load_genui_catalog()["limits"][name])


__all__ = [
    "CONFIG_PATH",
    "action_aliases",
    "action_types",
    "canonical_action",
    "component_types",
    "limit",
    "load_genui_catalog",
]
