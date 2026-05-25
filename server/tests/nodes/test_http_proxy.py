"""Contract tests for http_proxy nodes: httpRequest, proxyRequest, proxyConfig, proxyStatus.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/http_proxy/`. A refactor that breaks any of
these indicates the docs (and the user-visible contract) need to be updated
too.

All httpx calls are mocked via respx; the ProxyService singleton is patched
so `useProxy=true` branches route through a test-controlled proxy URL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from tests.nodes._mocks import patched_container


pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


def _make_proxy_service_mock(
    *,
    enabled: bool = True,
    proxy_url: str | None = "http://user:pass@proxy.test:8080",
    providers: list | None = None,
    stats: dict | None = None,
) -> MagicMock:
    svc = MagicMock(name="ProxyService")
    svc.is_enabled = MagicMock(return_value=enabled)
    svc.get_proxy_url = AsyncMock(return_value=proxy_url)
    svc.report_result = MagicMock(return_value=None)
    svc.reload_providers = AsyncMock(return_value=None)
    svc.get_providers = MagicMock(return_value=providers or [])
    svc.get_routing_rules = MagicMock(return_value=[])
    svc.get_stats = MagicMock(return_value=stats or {})
    return svc


def _pricing_with_proxy_config() -> MagicMock:
    """Pricing stub that satisfies `_track_proxy_usage`'s `pricing._config.get('proxy', {}).get(...)` chain."""
    pricing = MagicMock(name="PricingService")
    pricing._config = {"proxy": {}}
    return pricing


# ============================================================================
# httpRequest
# ============================================================================


class TestHttpRequest:
    URL = "https://api.example.com/data"

    @respx.mock
    async def test_happy_path_no_proxy(self, harness):
        respx.get(self.URL).mock(return_value=httpx.Response(200, json={"ok": True, "value": 42}))

        result = await harness.execute(
            "httpRequest",
            {"method": "GET", "url": self.URL, "headers": {"X-Test": "1"}},
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["status", "data", "headers", "url", "method", "proxied"])
        payload = result["result"]
        assert payload["status"] == 200
        assert payload["method"] == "GET"
        assert payload["proxied"] is False
        assert payload["data"] == {"ok": True, "value": 42}

        sent = respx.calls.last.request
        assert sent.headers["x-test"] == "1"

    async def test_missing_url_returns_validation_error(self, harness):
        result = await harness.execute("httpRequest", {"method": "GET", "url": ""})

        harness.assert_envelope(result, success=False)
        assert "url is required" in result["error"].lower()

    @respx.mock
    async def test_network_error_returns_envelope_not_raise(self, harness):
        respx.get(self.URL).mock(side_effect=httpx.ConnectError("connection refused"))

        result = await harness.execute("httpRequest", {"method": "GET", "url": self.URL})

        harness.assert_envelope(result, success=False)
        assert "connection refused" in result["error"].lower()

    @respx.mock
    async def test_non_json_response_falls_back_to_text(self, harness):
        respx.get(self.URL).mock(return_value=httpx.Response(200, text="<html>hi</html>"))

        result = await harness.execute("httpRequest", {"method": "GET", "url": self.URL})

        harness.assert_envelope(result, success=True)
        assert result["result"]["data"] == "<html>hi</html>"

    @respx.mock
    async def test_use_proxy_true_routes_through_proxy_service(self, harness):
        # Happy proxy path: ProxyService returns a proxy URL, handler tags proxied=true.
        respx.get(self.URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        proxy_svc = _make_proxy_service_mock(proxy_url="http://u:p@proxy.test:8080")

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc):
            result = await harness.execute(
                "httpRequest",
                {"method": "GET", "url": self.URL, "use_proxy": True, "proxy_country": "US"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["proxied"] is True
        # ProxyService.get_proxy_url was consulted with the target URL + params
        proxy_svc.get_proxy_url.assert_awaited_once()
        call_args = proxy_svc.get_proxy_url.await_args
        assert call_args.args[0] == self.URL
        assert call_args.args[1]["proxy_country"] == "US"

    @respx.mock
    async def test_use_proxy_swallows_proxy_error_and_proceeds(self, harness):
        # Documented behaviour: if proxy lookup raises, the handler logs a
        # warning and falls back to a direct call (proxied=False).
        respx.get(self.URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
        proxy_svc = _make_proxy_service_mock()
        proxy_svc.get_proxy_url = AsyncMock(side_effect=RuntimeError("budget exceeded"))

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc):
            result = await harness.execute(
                "httpRequest",
                {"method": "GET", "url": self.URL, "use_proxy": True},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["proxied"] is False


# ============================================================================
# proxyRequest
# ============================================================================


class TestProxyRequest:
    URL = "https://api.example.com/scrape"

    @respx.mock
    async def test_happy_path_reports_success_and_tracks_cost(self, harness):
        respx.get(self.URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        proxy_svc = _make_proxy_service_mock()
        pricing = _pricing_with_proxy_config()

        with (
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patch("services.pricing.get_pricing_service", return_value=pricing),
            patched_container(),
        ):
            result = await harness.execute(
                "proxyRequest",
                {
                    "method": "GET",
                    "url": self.URL,
                    "proxy_provider": "smart_proxy",
                    "proxy_country": "US",
                },
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result,
            [
                "status",
                "data",
                "headers",
                "url",
                "method",
                "proxy_provider",
                "latency_ms",
                "bytes_transferred",
                "attempt",
            ],
        )
        payload = result["result"]
        assert payload["status"] == 200
        assert payload["proxy_provider"] == "smart_proxy"
        assert payload["attempt"] == 1

        # Health feedback: success reported exactly once for this attempt
        assert proxy_svc.report_result.call_count == 1
        call = proxy_svc.report_result.call_args
        assert call.args[0] == "smart_proxy"
        assert call.args[1].success is True

    async def test_service_disabled_short_circuits(self, harness):
        proxy_svc = _make_proxy_service_mock(enabled=False)

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc), patched_container():
            result = await harness.execute("proxyRequest", {"method": "GET", "url": "https://x.test"})

        harness.assert_envelope(result, success=False)
        assert "proxy service not initialized" in result["error"].lower()

    async def test_missing_url_returns_validation_error(self, harness):
        proxy_svc = _make_proxy_service_mock()

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc), patched_container():
            result = await harness.execute("proxyRequest", {"method": "GET", "url": ""})

        harness.assert_envelope(result, success=False)
        err = result["error"].lower()
        assert "url" in err or "protocol" in err

    async def test_no_provider_available_returns_envelope(self, harness):
        proxy_svc = _make_proxy_service_mock(proxy_url=None)

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc), patched_container():
            result = await harness.execute("proxyRequest", {"method": "GET", "url": "https://x.test"})

        harness.assert_envelope(result, success=False)
        assert "no proxy provider" in result["error"].lower()

    @respx.mock
    async def test_network_error_retries_and_reports_failure(self, harness):
        # All attempts fail -> handler reports each failure and returns error envelope.
        respx.get(self.URL).mock(side_effect=httpx.ConnectError("boom"))
        proxy_svc = _make_proxy_service_mock()

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc), patched_container():
            result = await harness.execute(
                "proxyRequest",
                {
                    "method": "GET",
                    "url": self.URL,
                    "proxy_provider": "p1",
                    "max_retries": 2,
                    "proxy_failover": True,
                },
            )

        harness.assert_envelope(result, success=False)
        assert "all 3 attempts failed" in result["error"].lower()
        # report_result called once per attempt (3 total)
        assert proxy_svc.report_result.call_count == 3
        # every reported result was a failure
        for call in proxy_svc.report_result.call_args_list:
            assert call.args[1].success is False


# ============================================================================
# proxyConfig
# ============================================================================


class TestProxyConfig:
    async def test_list_providers_happy_path(self, harness):
        stub_provider = MagicMock()
        stub_provider.model_dump = MagicMock(return_value={"name": "p1", "enabled": True, "score": 0.9})
        proxy_svc = _make_proxy_service_mock(providers=[stub_provider])

        # proxy.py imports get_proxy_service at module load (line 17),
        # so we must patch it in the handler module too. tools.py re-imports
        # it inside the function, so patching the source is sufficient there.
        with (
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patched_container(),
        ):
            result = await harness.execute("proxyConfig", {"operation": "list_providers"})

        harness.assert_envelope(result, success=True)
        # Node handler wraps the tool result under result={...} and copies
        # tool's success into the envelope.
        tool_payload = result["result"]
        assert tool_payload["success"] is True
        assert tool_payload["operation"] == "list_providers"
        assert tool_payload["providers"] == [{"name": "p1", "enabled": True, "score": 0.9}]

    async def test_add_provider_missing_name_returns_validation_error(self, harness):
        proxy_svc = _make_proxy_service_mock()

        with (
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patched_container(),
        ):
            result = await harness.execute("proxyConfig", {"operation": "add_provider", "name": ""})

        harness.assert_envelope(result, success=False)
        assert "provider name is required" in result["error"].lower()

    async def test_unknown_operation_returns_error(self, harness):
        proxy_svc = _make_proxy_service_mock()

        with (
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patch("services.proxy.service.get_proxy_service", return_value=proxy_svc),
            patched_container(),
        ):
            result = await harness.execute("proxyConfig", {"operation": "teleport_provider"})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# proxyStatus
# ============================================================================


class TestProxyStatus:
    async def test_happy_path_returns_providers_and_stats(self, harness):
        stub_provider = MagicMock()
        stub_provider.model_dump = MagicMock(return_value={"name": "p1", "score": 0.75, "success_rate": 0.9})
        stats = {"total_requests": 100, "total_bytes": 12345}
        proxy_svc = _make_proxy_service_mock(providers=[stub_provider], stats=stats)

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc):
            result = await harness.execute("proxyStatus", {})

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["enabled", "providers", "stats"])
        payload = result["result"]
        assert payload["enabled"] is True
        assert payload["providers"][0]["name"] == "p1"
        assert payload["stats"] == stats

    async def test_disabled_service_returns_empty_success_envelope(self, harness):
        proxy_svc = _make_proxy_service_mock(enabled=False)

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc):
            result = await harness.execute("proxyStatus", {})

        harness.assert_envelope(result, success=True)
        assert result["result"]["enabled"] is False
        assert result["result"]["providers"] == []
        assert result["result"]["stats"] == {}

    async def test_get_stats_raising_returns_error_envelope(self, harness):
        proxy_svc = _make_proxy_service_mock()
        proxy_svc.get_stats = MagicMock(side_effect=RuntimeError("kaboom"))

        with patch("services.proxy.service.get_proxy_service", return_value=proxy_svc):
            result = await harness.execute("proxyStatus", {})

        harness.assert_envelope(result, success=False)
        assert "kaboom" in result["error"]
