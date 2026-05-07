"""Proxy Config — Wave 11.E.3 inlined.

10-operation dispatcher for managing proxy providers, credentials,
and routing rules. Both the workflow node and the AI-tool fast-path
land here.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Literal, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

logger = get_logger(__name__)


_PROVIDER_OPS = ["add_provider", "update_provider", "remove_provider", "set_credentials", "test_provider"]
_PROVIDER_EDIT_OPS = ["add_provider", "update_provider"]
_CREDENTIAL_OPS = ["set_credentials"]
_ROUTING_EDIT_OPS = ["add_routing_rule"]
_ROUTING_REMOVE_OPS = ["remove_routing_rule"]


class ProxyConfigParams(BaseModel):
    operation: Literal[
        "list_providers", "add_provider", "update_provider", "remove_provider",
        "set_credentials", "test_provider", "get_stats",
        "add_routing_rule", "list_routing_rules", "remove_routing_rule",
    ] = Field(
        default="list_providers",
        description="Proxy management operation. list_providers / get_stats / list_routing_rules need no extra params.",
    )

    # Provider identity (all provider-scoped ops)
    name: str = Field(
        default="",
        description="Provider name (unique ID).",
        json_schema_extra={"displayOptions": {"show": {"operation": _PROVIDER_OPS}}},
    )

    # Provider definition (add/update only)
    gateway_host: str = Field(
        default="",
        description="Proxy gateway hostname.",
        json_schema_extra={"displayOptions": {"show": {"operation": _PROVIDER_EDIT_OPS}}},
    )
    gateway_port: int = Field(
        default=0,
        description="Proxy gateway port.",
        json_schema_extra={"displayOptions": {"show": {"operation": _PROVIDER_EDIT_OPS}}},
    )
    url_template: Any = Field(
        default="{}",
        description="JSON template for proxy URL formatting (e.g. {\"username\": \"{user}-country-{country}\"}).",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"operation": _PROVIDER_EDIT_OPS}},
        },
    )
    cost_per_gb: float = Field(
        default=0.0,
        description="Cost in USD per GB of traffic (for ranking).",
        json_schema_extra={"displayOptions": {"show": {"operation": _PROVIDER_EDIT_OPS}}},
    )
    enabled: Optional[bool] = Field(
        default=None,
        description="Whether the provider is active.",
        json_schema_extra={"displayOptions": {"show": {"operation": _PROVIDER_EDIT_OPS}}},
    )
    priority: int = Field(
        default=50,
        ge=0, le=100,
        description="Selection priority (higher ranks first).",
        json_schema_extra={"displayOptions": {"show": {"operation": _PROVIDER_EDIT_OPS}}},
    )

    # Credentials (set_credentials only)
    username: str = Field(
        default="",
        description="Proxy account username.",
        json_schema_extra={"displayOptions": {"show": {"operation": _CREDENTIAL_OPS}}},
    )
    password: str = Field(
        default="",
        description="Proxy account password.",
        json_schema_extra={
            "widget": "password",
            "displayOptions": {"show": {"operation": _CREDENTIAL_OPS}},
        },
    )

    # Routing rule definition (add_routing_rule)
    domain_pattern: str = Field(
        default="",
        description="Domain glob pattern (e.g. *.example.com).",
        json_schema_extra={"displayOptions": {"show": {"operation": _ROUTING_EDIT_OPS}}},
    )
    preferred_providers: Any = Field(
        default="[]",
        description="JSON array of preferred provider names.",
        json_schema_extra={
            "rows": 2,
            "displayOptions": {"show": {"operation": _ROUTING_EDIT_OPS}},
        },
    )
    required_country: str = Field(
        default="",
        description="ISO country code required for this route.",
        json_schema_extra={"displayOptions": {"show": {"operation": _ROUTING_EDIT_OPS}}},
    )
    session_type: Literal["rotating", "sticky"] = Field(
        default="rotating",
        description="Rotating rotates IPs each request; sticky holds one IP per session.",
        json_schema_extra={"displayOptions": {"show": {"operation": _ROUTING_EDIT_OPS}}},
    )

    # Routing rule removal (remove_routing_rule)
    rule_id: Optional[int] = Field(
        default=None,
        description="Routing rule ID to remove.",
        json_schema_extra={"displayOptions": {"show": {"operation": _ROUTING_REMOVE_OPS}}},
    )

    model_config = ConfigDict(extra="ignore")


class ProxyConfigOutput(BaseModel):
    operation: Optional[str] = None
    success: Optional[bool] = None
    providers: Optional[list] = None
    rules: Optional[list] = None
    stats: Optional[dict] = None
    name: Optional[str] = None
    updated_fields: Optional[list] = None
    domain_pattern: Optional[str] = None
    rule_id: Optional[int] = None
    ip: Optional[str] = None
    latency_ms: Optional[float] = None
    status_code: Optional[int] = None

    model_config = ConfigDict(extra="allow")


async def _list_providers(proxy_svc) -> Dict[str, Any]:
    providers = proxy_svc.get_providers() if proxy_svc else []
    return {"success": True, "providers": [p.model_dump() for p in providers]}


async def _get_stats(proxy_svc) -> Dict[str, Any]:
    return {"success": True, "stats": proxy_svc.get_stats() if proxy_svc else {}}


async def _list_routing_rules(proxy_svc) -> Dict[str, Any]:
    rules = proxy_svc.get_routing_rules() if proxy_svc else []
    return {"success": True, "rules": [r.model_dump() for r in rules]}


async def _add_provider(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    from services.plugin.deps import get_database

    name = p.get("name", "")
    if not name:
        return {"success": False, "error": "Provider name is required"}

    url_template_raw = p.get("url_template", "{}")
    try:
        url_template = (
            json.loads(url_template_raw) if isinstance(url_template_raw, str) else url_template_raw
        )
    except json.JSONDecodeError:
        return {"success": False, "error": f"Invalid url_template JSON: {url_template_raw}"}

    db = get_database()
    await db.save_proxy_provider({
        "name": name,
        "enabled": p.get("enabled", True),
        "priority": int(p.get("priority", 50)),
        "cost_per_gb": float(p.get("cost_per_gb", 0)),
        "gateway_host": p.get("gateway_host", ""),
        "gateway_port": int(p.get("gateway_port", 0)),
        "url_template": json.dumps(url_template),
    })
    if proxy_svc:
        await proxy_svc.reload_providers()
    return {"success": True, "name": name}


async def _update_provider(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    from services.plugin.deps import get_database

    name = p.get("name", "")
    if not name:
        return {"success": False, "error": "Provider name is required"}
    db = get_database()
    existing = await db.get_proxy_provider(name)
    if not existing:
        return {"success": False, "error": f"Provider '{name}' not found"}

    updates: Dict[str, Any] = {}
    for field in ("gateway_host", "gateway_port", "cost_per_gb", "enabled", "priority"):
        if p.get(field) is not None:
            updates[field] = p[field]

    url_template_raw = p.get("url_template")
    if url_template_raw:
        try:
            url_template = (
                json.loads(url_template_raw) if isinstance(url_template_raw, str) else url_template_raw
            )
            updates["url_template"] = json.dumps(url_template)
        except json.JSONDecodeError:
            return {"success": False, "error": "Invalid url_template JSON"}

    if updates:
        await db.save_proxy_provider({**existing, **updates})
        if proxy_svc:
            await proxy_svc.reload_providers()
    return {"success": True, "name": name, "updated_fields": list(updates.keys())}


async def _remove_provider(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    from services.plugin.deps import get_database

    name = p.get("name", "")
    if not name:
        return {"success": False, "error": "Provider name is required"}
    db = get_database()
    await db.delete_proxy_provider(name)
    if proxy_svc:
        await proxy_svc.reload_providers()
    return {"success": True, "name": name}


async def _set_credentials(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    from services.plugin.deps import get_auth_service

    name = p.get("name", "")
    if not name:
        return {"success": False, "error": "Provider name is required"}
    username, password = p.get("username", ""), p.get("password", "")
    if not username or not password:
        return {"success": False, "error": "Username and password are required"}

    auth_svc = get_auth_service()
    await auth_svc.store_api_key(f"proxy_{name}_username", username, [])
    await auth_svc.store_api_key(f"proxy_{name}_password", password, [])
    if proxy_svc:
        await proxy_svc.reload_providers()
    return {"success": True, "name": name}


async def _test_provider(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    name = p.get("name", "")
    if not name:
        return {"success": False, "error": "Provider name is required"}
    if not proxy_svc:
        return {"success": False, "error": "Proxy service not enabled"}

    try:
        proxy_url = await proxy_svc.get_proxy_url(
            "https://httpbin.org/ip", {"proxyProvider": name},
        )
        if not proxy_url:
            return {"success": False, "error": f"Could not get proxy URL for '{name}'"}

        start = time.monotonic()
        async with httpx.AsyncClient(proxy=proxy_url, timeout=30) as client:
            resp = await client.get("https://httpbin.org/ip")
        latency_ms = (time.monotonic() - start) * 1000

        try:
            ip = resp.json().get("origin", "unknown")
        except Exception:
            ip = "unknown"

        return {
            "success": resp.status_code == 200,
            "name": name,
            "ip": ip,
            "latency_ms": round(latency_ms, 1),
            "status_code": resp.status_code,
        }
    except Exception as e:
        return {"success": False, "name": name, "error": str(e)}


async def _add_routing_rule(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    from services.plugin.deps import get_database

    domain_pattern = p.get("domain_pattern", "")
    if not domain_pattern:
        return {"success": False, "error": "domain_pattern is required"}

    preferred_raw = p.get("preferred_providers", "[]")
    try:
        preferred = (
            json.loads(preferred_raw) if isinstance(preferred_raw, str) else preferred_raw
        )
    except json.JSONDecodeError:
        preferred = []

    db = get_database()
    await db.save_proxy_routing_rule({
        "domain_pattern": domain_pattern,
        "preferred_providers": json.dumps(preferred),
        "required_country": p.get("required_country", ""),
        "session_type": p.get("session_type", "rotating"),
    })
    if proxy_svc:
        await proxy_svc.reload_providers()
    return {"success": True, "domain_pattern": domain_pattern}


async def _remove_routing_rule(p: Dict[str, Any], proxy_svc) -> Dict[str, Any]:
    from services.plugin.deps import get_database

    rule_id = p.get("rule_id")
    if not rule_id:
        return {"success": False, "error": "rule_id is required"}
    db = get_database()
    await db.delete_proxy_routing_rule(int(rule_id))
    if proxy_svc:
        await proxy_svc.reload_providers()
    return {"success": True, "rule_id": rule_id}


_OPS = {
    "list_providers": lambda p, svc: _list_providers(svc),
    "get_stats": lambda p, svc: _get_stats(svc),
    "list_routing_rules": lambda p, svc: _list_routing_rules(svc),
    "add_provider": _add_provider,
    "update_provider": _update_provider,
    "remove_provider": _remove_provider,
    "set_credentials": _set_credentials,
    "test_provider": _test_provider,
    "add_routing_rule": _add_routing_rule,
    "remove_routing_rule": _remove_routing_rule,
}


async def execute_proxy_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run a proxy_config operation. Public entry point used by the
    plugin's ``dispatch`` op AND by the legacy AI-tool fast-path in
    ``handlers/tools.py:execute_tool``.
    """
    from services.proxy.service import get_proxy_service

    operation = payload.get("operation", "list_providers")
    handler = _OPS.get(operation)
    if handler is None:
        return {"success": False, "error": f"Unknown operation: {operation}"}

    proxy_svc = get_proxy_service()
    result = await handler(payload, proxy_svc)
    result["operation"] = operation
    return result


class ProxyConfigNode(ActionNode):
    type = "proxyConfig"
    display_name = "Proxy Config"
    subtitle = "Routing Rules"
    group = ("proxy", "tool")
    description = "Configure proxy providers and routing rules"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = ProxyConfigParams
    Output = ProxyConfigOutput

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: ProxyConfigParams) -> Any:
        result = await execute_proxy_config(params.model_dump())
        if not result.get("success"):
            raise NodeUserError(result.get("error") or "Proxy config failed")
        return result
