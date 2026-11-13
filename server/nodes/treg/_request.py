"""What a treg tool takes, read from its catalog entry, and where a call puts it.

A catalog entry's ``input`` lists the tool's parameters in blocks named for
where they go: ``pathParams``, ``queryParams``, ``body`` and ``headers``,
each ``{name: spec}`` (or ``{"properties": {name: spec}}``). A spec carries
``type``, ``required``, ``default``, ``enum``, ``example`` and a ``note``.
``bodyType`` is ``json`` (the default), ``form`` or ``multipart``, and
``queryArrayEncoding`` says how a list goes in the query string.

:func:`build_request` places a call's parameters the way the catalog
declares them. treg fills a path parameter from the query string, so path
and query parameters both go there; a name the catalog does not declare
follows the method (query string for GET, HEAD and DELETE, body otherwise),
which is what treg itself does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

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


@dataclass(frozen=True)
class TregRequest:
    method: str
    query: List[Tuple[str, str]]
    body: Optional[bytes]
    content_type: Optional[str]


_QUERY_METHODS = frozenset({"GET", "HEAD", "DELETE"})


def _wire(value: Any) -> str:
    """One query value as HTTP spells it: JSON's booleans, compact JSON for an object."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def query_values(inp: Any, value: Any) -> List[str]:
    """The query-string values for one parameter: a list as the tool's
    ``queryArrayEncoding`` says (repeated keys unless ``json`` or ``comma``)."""
    if not isinstance(value, (list, tuple)):
        return [_wire(value)]
    encoding = inp.get("queryArrayEncoding") if isinstance(inp, dict) else None
    if encoding == "json":
        return [_wire(value)]
    items = [item for item in value if item is not None]
    if encoding == "comma":
        return [",".join(_wire(item) for item in items)]
    return [_wire(item) for item in items]


def _nested(fields: Dict[str, Any]) -> Dict[str, Any]:
    """A dotted name (``params.domain``) is a field of a nested object."""
    out: Dict[str, Any] = {}
    for name, value in fields.items():
        cursor = out
        *parents, leaf = name.split(".")
        for part in parents:
            child = cursor.get(part)
            if not isinstance(child, dict):
                child = cursor[part] = {}
            cursor = child
        cursor[leaf] = value
    return out


def build_request(endpoint: Dict[str, Any], params: Dict[str, Any], body: Any) -> TregRequest:
    """Where a call's ``params`` go, or ``body`` sent whole.

    A tool whose example request has a list body takes a list: its fields
    are sent as a list of one. A tool that needs a file upload or a request
    header of its own is refused before anything is sent.
    """
    tool_id = str(endpoint.get("id") or "this tool")
    method = str(endpoint.get("method") or "GET").upper()
    inp = endpoint.get("input") if isinstance(endpoint.get("input"), dict) else {}
    body_type = str(inp.get("bodyType") or "json").lower()
    if body_type == "multipart":
        raise NodeUserError(f"{tool_id} takes a file upload, which the treg node cannot send yet.")
    headers = sorted(name for name, spec in block(inp, "headers").items() if isinstance(spec, dict) and spec.get("required"))
    if headers:
        raise NodeUserError(f"{tool_id} needs the request header {', '.join(headers)}, which the treg node cannot send yet.")

    in_query = set(block(inp, "pathParams")) | set(block(inp, "queryParams"))
    in_body = set(block(inp, "body"))
    query: List[Tuple[str, str]] = []
    fields: Dict[str, Any] = {}
    for name, value in (params or {}).items():
        if value is None:
            continue
        if name in in_query or (name not in in_body and method in _QUERY_METHODS):
            query += [(name, item) for item in query_values(inp, value)]
        else:
            fields[name] = value
    if body is not None and fields:
        raise NodeUserError(
            f"Put {', '.join(sorted(fields))} inside body, or leave body empty and pass them in params: the two are not merged."
        )
    if body is None and (fields or method not in _QUERY_METHODS):
        body = _nested(fields)
        example = (endpoint.get("test_request") or {}).get("body") if isinstance(endpoint.get("test_request"), dict) else None
        if isinstance(example, list):
            body = [body]
    if body is None:
        return TregRequest(method, query, None, None)
    if body_type == "form":
        if not isinstance(body, dict):
            raise NodeUserError(f"{tool_id} takes form fields: give them by name in params or as an object in body.")
        pairs = [(key, _wire(item)) for key, value in body.items() for item in (value if isinstance(value, list) else [value])]
        return TregRequest(method, query, urlencode(pairs).encode("utf-8"), "application/x-www-form-urlencoded")
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return TregRequest(method, query, payload, "application/json")


__all__ = ["BLOCKS", "TregRequest", "block", "build_request", "check_tool_id", "parameters_of", "query_values"]
