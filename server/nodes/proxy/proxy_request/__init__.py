"""Proxy Request — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class ProxyRequestParams(BaseModel):
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "GET"
    url: str = Field(...)
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[Any] = Field(
        default=None,
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"method": ["POST", "PUT", "PATCH"]}},
        },
    )
    timeout: int = Field(default=30, ge=1, le=600)
    proxy_provider: str = Field(default="auto")
    proxy_country: str = Field(default="")
    session_type: Literal["rotating", "sticky"] = Field(default="rotating")
    sticky_duration: int = Field(default=600, ge=1)
    max_retries: int = Field(default=3, ge=0, le=10)
    follow_redirects: bool = Field(default=True)

    model_config = ConfigDict(extra="allow")


class ProxyRequestOutput(BaseModel):
    status: Optional[int] = None
    body: Optional[Any] = None
    proxy_used: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ProxyRequestNode(ActionNode):
    type = "proxyRequest"
    display_name = "Proxy Request"
    subtitle = "Routed HTTP"
    group = ("proxy", "tool")
    description = "Make HTTP requests through residential proxy providers"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = ProxyRequestParams
    Output = ProxyRequestOutput

    @Operation("request")
    async def request(self, ctx: NodeContext, params: ProxyRequestParams) -> Any:
        """Inlined from handlers/proxy.py (Wave 11.D.3).

        Retry/failover loop: on each attempt asks ProxyService for a
        URL, executes the request, and reports result (success +
        latency_ms / bytes for success, error for failure) back so
        the health scorer updates. Usage is persisted per request.
        """
        import json as json_module
        import time
        from core.logging import get_logger
        import httpx

        from services.proxy.models import ProxyResult
        from services.proxy.service import get_proxy_service
        from .._usage import track_proxy_usage

        log = get_logger(__name__)
        svc = get_proxy_service()
        if not svc or not svc.is_enabled():
            raise NodeUserError(
                "Proxy service not initialized. Use proxy_config tool to add a "
                "provider first.",
            )

        raw = params.model_dump()
        proxy_url = await svc.get_proxy_url(params.url, raw)
        if not proxy_url:
            raise NodeUserError("No proxy provider available")

        max_retries = params.max_retries
        failover = raw.get("proxy_failover", True)
        provider_name = params.proxy_provider or ""

        last_error: str = ""
        for attempt in range(max_retries + 1):
            req_start = time.monotonic()
            try:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=float(params.timeout)) as client:
                    kwargs = {
                        "method": params.method,
                        "url": params.url,
                        "headers": params.headers,
                    }
                    if params.method in ("POST", "PUT", "PATCH") and params.body is not None:
                        body = params.body
                        if isinstance(body, str):
                            try:
                                kwargs["json"] = json_module.loads(body)
                            except json_module.JSONDecodeError:
                                kwargs["content"] = body
                        else:
                            kwargs["json"] = body
                    response = await client.request(**kwargs)

                latency_ms = (time.monotonic() - req_start) * 1000
                bytes_transferred = len(response.content) if response.content else 0

                svc.report_result(provider_name, ProxyResult(
                    success=response.status_code < 400,
                    latency_ms=latency_ms,
                    bytes_transferred=bytes_transferred,
                    status_code=response.status_code,
                ))
                await track_proxy_usage(
                    ctx.node_id, provider_name, bytes_transferred,
                    workflow_id=ctx.workflow_id, session_id=ctx.session_id,
                )
                try:
                    response_data = response.json()
                except Exception:
                    response_data = response.text

                if response.status_code >= 400:
                    raise NodeUserError(f"HTTP {response.status_code}: {response_data!r}")
                return {
                    "status": response.status_code,
                    "data": response_data,
                    "headers": dict(response.headers),
                    "url": str(response.url),
                    "method": params.method,
                    "proxy_provider": provider_name,
                    "latency_ms": round(latency_ms, 1),
                    "bytes_transferred": bytes_transferred,
                    "attempt": attempt + 1,
                }

            except Exception as e:
                latency_ms = (time.monotonic() - req_start) * 1000
                last_error = str(e)
                svc.report_result(provider_name, ProxyResult(
                    success=False, latency_ms=latency_ms, error=last_error,
                ))
                log.warning(
                    "Proxy request attempt failed",
                    node_id=ctx.node_id, attempt=attempt + 1,
                    max_retries=max_retries, error=last_error,
                )
                if not failover or attempt >= max_retries:
                    break
                try:
                    proxy_url = await svc.get_proxy_url(params.url, raw)
                except Exception:
                    break

        raise RuntimeError(
            f"All {max_retries + 1} attempts failed. Last error: {last_error}",
        )
