"""Declarative REST routing DSL (n8n + Nango pattern).

Attach a :class:`Routing` to an :class:`Operation` and the node needs
no handler body — :func:`execute_routing` walks the model, interpolates
``={{params.x}}`` / ``={{credentials.y}}`` templates, dispatches via the
:class:`Connection`, and runs ``post_receive`` transforms.

Only covers straightforward REST. Escape hatch: leave ``routing=None``
and write the operation method normally.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

_EXPR_RE = re.compile(r"=\{\{\s*(.+?)\s*\}\}")


class RoutingRequest(BaseModel):
    """Declarative HTTP request. String values may contain
    ``={{params.foo}}`` / ``={{credentials.bar}}`` templates resolved at
    call time.
    """

    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "GET"
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    qs: Dict[str, Any] = Field(default_factory=dict)
    body: Optional[Any] = None
    # Body encoding — "json" (default) sends via httpx json=, "form" via data=
    body_encoding: Literal["json", "form"] = "json"


class PostReceiveAction(BaseModel):
    """One transform in the ``post_receive`` chain. Named strategies live
    in :data:`POST_RECEIVE_STRATEGIES` — ``type`` selects one.
    """

    type: Literal["root_property", "limit", "filter", "set"] = "root_property"
    # Type-specific args (e.g. ``property="data"`` for root_property)
    property: Optional[str] = None
    max_items: Optional[int] = None
    where: Optional[Dict[str, Any]] = None
    set_fields: Optional[Dict[str, Any]] = None


class RoutingOutput(BaseModel):
    post_receive: List[PostReceiveAction] = Field(default_factory=list)


class Routing(BaseModel):
    """Full declarative routing spec attached to an ``@Operation``."""

    request: RoutingRequest
    output: RoutingOutput = Field(default_factory=RoutingOutput)


# ---------------------------------------------------------------------------
# Template interpolation


def _resolve_template(value: Any, env: Dict[str, Any]) -> Any:
    """Resolve ``={{expr}}`` templates against ``env``. Non-string values
    pass through. Supports dotted paths — ``params.maxResults``."""
    if not isinstance(value, str):
        return value
    if not value.startswith("="):
        return value

    def replace(match: re.Match) -> str:
        expr = match.group(1).strip()
        parts = expr.split(".")
        current: Any = env
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return ""
        return str(current)

    # Strip leading "=" marker then interpolate.
    stripped = value[1:]
    return _EXPR_RE.sub(replace, stripped)


def _resolve_dict(d: Dict[str, Any], env: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _resolve_template(v, env) for k, v in d.items()}


# ---------------------------------------------------------------------------
# post_receive strategies


def _strategy_root_property(data: Any, action: PostReceiveAction) -> Any:
    if not action.property or not isinstance(data, dict):
        return data
    current: Any = data
    for part in action.property.split("."):
        if not isinstance(current, dict):
            return current
        current = current.get(part)
    return current


def _strategy_limit(data: Any, action: PostReceiveAction) -> Any:
    if not isinstance(data, list) or action.max_items is None:
        return data
    return data[: action.max_items]


def _strategy_filter(data: Any, action: PostReceiveAction) -> Any:
    if not isinstance(data, list) or not action.where:
        return data
    return [item for item in data if all(isinstance(item, dict) and item.get(k) == v for k, v in action.where.items())]


def _strategy_set(data: Any, action: PostReceiveAction) -> Any:
    if not action.set_fields:
        return data
    if isinstance(data, dict):
        return {**data, **action.set_fields}
    if isinstance(data, list):
        return [{**item, **action.set_fields} if isinstance(item, dict) else item for item in data]
    return data


POST_RECEIVE_STRATEGIES = {
    "root_property": _strategy_root_property,
    "limit": _strategy_limit,
    "filter": _strategy_filter,
    "set": _strategy_set,
}


# ---------------------------------------------------------------------------
# Execution


async def execute_routing(
    routing: Routing,
    *,
    params: Dict[str, Any],
    connection,
) -> Any:
    """Walk a :class:`Routing` spec, dispatch via ``connection``, and
    apply ``post_receive`` transforms. ``params`` is the already-validated
    Pydantic params dict (``.model_dump()``). Credentials come from
    ``connection`` — they're not in ``env``; ``={{credentials.x}}``
    templates read the Connection's resolved secrets.
    """
    secrets = await connection.credentials()
    env = {"params": params, "credentials": secrets}

    req = routing.request
    method = req.method
    url = _resolve_template(req.url, env)
    headers = _resolve_dict(req.headers, env)
    qs = {k: v for k, v in _resolve_dict(req.qs, env).items() if v not in (None, "")}

    kwargs: Dict[str, Any] = {"headers": headers, "params": qs}
    if req.body is not None:
        body_resolved = _resolve_dict(req.body, env) if isinstance(req.body, dict) else _resolve_template(req.body, env)
        if req.body_encoding == "form":
            kwargs["data"] = body_resolved
        else:
            kwargs["json"] = body_resolved

    response = await connection.request(method, url, **kwargs)
    response.raise_for_status()
    data: Any = response.json() if response.content else None

    for action in routing.output.post_receive:
        strategy = POST_RECEIVE_STRATEGIES.get(action.type)
        if strategy is None:
            raise ValueError(f"Unknown post_receive strategy: {action.type}")
        data = strategy(data, action)

    return data
