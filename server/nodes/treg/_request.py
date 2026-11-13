"""What a treg tool takes, read from its catalog entry.

A catalog entry's ``input`` lists the tool's parameters in blocks named for
where they go: ``pathParams``, ``queryParams``, ``body`` and ``headers``,
each ``{name: spec}`` (or ``{"properties": {name: spec}}``). A spec carries
``type``, ``required``, ``default``, ``enum``, ``example`` and a ``note``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from services.plugin.base import NodeUserError

#: Every catalog id: a letter or digit, then letters, digits and . _ @ -.
#: Nothing else may reach the CLI as a tool id (no path, query or option).
_TOOL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")

#: Each input block and the place it is reported as.
BLOCKS = (("pathParams", "path"), ("queryParams", "query"), ("body", "body"), ("headers", "header"))
_SPEC_KEYS = ("type", "required", "default", "enum", "example", "note", "min", "max")


def check_tool_id(tool_id: str) -> str:
    tool_id = (tool_id or "").strip()
    if not tool_id:
        raise NodeUserError("tool_id is required: an id from operation=search, such as serper.web.search.")
    if not _TOOL_ID_RE.fullmatch(tool_id):
        raise NodeUserError(f"{tool_id!r} is not a treg tool id. Ids look like serper.web.search; find one with operation=search.")
    return tool_id


def block(inp: Any, name: str) -> Dict[str, Any]:
    """One input block as ``{name: spec}``; empty when the tool has none."""
    value = inp.get(name) if isinstance(inp, dict) else None
    if isinstance(value, dict) and isinstance(value.get("properties"), dict):
        value = value["properties"]
    return value if isinstance(value, dict) else {}


def parameters_of(inp: Any) -> List[Dict[str, Any]]:
    """Every parameter a tool declares, with where it goes."""
    out: List[Dict[str, Any]] = []
    for key, where in BLOCKS:
        for name, spec in block(inp, key).items():
            spec = spec if isinstance(spec, dict) else {}
            out.append({"name": name, "in": where, **{k: spec[k] for k in _SPEC_KEYS if spec.get(k) is not None}})
    return out


__all__ = ["BLOCKS", "block", "check_tool_id", "parameters_of"]
