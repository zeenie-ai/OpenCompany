"""Deterministic validation for name-dispatched agent tool surfaces."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional


class DuplicateToolNameError(ValueError):
    """Raised before model/tool exposure when names are ambiguous."""

    error_type = "DuplicateToolNameError"

    def __init__(self, conflicts: Mapping[str, List[Dict[str, str]]]):
        normalized = {
            name: sorted(
                [dict(identity) for identity in identities],
                key=lambda identity: (identity["node_id"], identity["label"]),
            )
            for name, identities in sorted(conflicts.items())
        }
        self.conflicts = normalized
        details = []
        for name, identities in normalized.items():
            nodes = ", ".join(
                f"{identity['label']} ({identity['node_id']})"
                for identity in identities
            )
            details.append(f"{name!r}: {nodes}")
        super().__init__(
            "Duplicate LLM-visible tool names are not allowed: "
            + "; ".join(details)
            + ". Assign a unique Tool Name to each connected tool."
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type,
            "error": str(self),
            "conflicts": self.conflicts,
        }


def duplicate_tool_name_error(
    identities: Iterable[Mapping[str, Any]],
) -> Optional[DuplicateToolNameError]:
    """Return a structured duplicate-name error, if one exists.

    Each identity uses the canonical ``name``, ``node_id``, and ``label``
    fields. Validation is intentionally scoped to one agent surface; the same
    visible name remains valid on different agents.
    """
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for identity in identities:
        name = str(identity.get("name") or "")
        grouped[name].append(
            {
                "node_id": str(identity.get("node_id") or "<missing-node-id>"),
                "label": str(identity.get("label") or identity.get("node_type") or "tool"),
            }
        )
    conflicts = {
        name: entries
        for name, entries in grouped.items()
        if len(entries) > 1
    }
    return DuplicateToolNameError(conflicts) if conflicts else None


def ensure_unique_tool_names(identities: Iterable[Mapping[str, Any]]) -> None:
    """Raise :class:`DuplicateToolNameError` for an ambiguous surface."""
    error = duplicate_tool_name_error(identities)
    if error is not None:
        raise error
