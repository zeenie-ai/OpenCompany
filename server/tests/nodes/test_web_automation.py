"""Contract tests for web_automation nodes: browser, crawleeScraper, apifyActor.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/web_automation/`. A refactor that breaks any of
these indicates the docs (and the user-visible contract) need to be updated too.

All external side-effects are mocked:
  - browser: patches `BrowserService._run_sync` so no subprocess is spawned.
  - crawleeScraper: patches the crawlee crawler classes the handler imports
    lazily (`BeautifulSoupCrawler`, `PlaywrightCrawler`).
  - apifyActor: patches the ApifyClientAsync singleton returned from
    `services.handlers.apify._get_apify_client`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import patched_container


pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


class _FakeBrowserService:
    """Drop-in replacement for BrowserService exposing an async `run`.

    Returns a pre-canned JSON-parsed dict on each call. The real service's
    `run()` is also async, so tests can `await` it transparently.
    """

    def __init__(self, canned: dict | None = None, error: Exception | None = None):
        self._canned = canned if canned is not None else {"success": True, "data": {}}
        self._error = error
        self.calls: list[tuple] = []

    async def run(self, args, session, timeout, **kwargs):
        self.calls.append((tuple(args), session, timeout, kwargs))
        if self._error:
            raise self._error
        return self._canned


def _patch_browser_service(svc):
    """Patch get_browser_service to return the fake."""
    return patch("nodes.browser._service.get_browser_service", return_value=svc)


# ============================================================================
# browser
# ============================================================================


class TestBrowser:
    async def test_navigate_happy_path(self, harness):
        fake = _FakeBrowserService(canned={"success": True, "url": "https://example.com"})

        with _patch_browser_service(fake):
            result = await harness.execute(
                "browser",
                {
                    "operation": "navigate",
                    "url": "https://example.com",
                    "session": "test_sess",
                },
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["operation", "data", "session"])
        payload = result["result"]
        assert payload["operation"] == "navigate"
        assert payload["session"] == "test_sess"
        assert payload["data"]["url"] == "https://example.com"

        # Verify the handler mapped navigate -> [open, URL]
        args, session, timeout, _ = fake.calls[-1]
        assert args == ("open", "https://example.com")
        assert session == "test_sess"

    async def test_snapshot_uses_i_flag_and_session_fallback(self, harness):
        # Empty session -> handler must derive machina_<execution_id>.
        fake = _FakeBrowserService(canned={"success": True, "nodes": [{"ref": "@e1", "role": "button"}]})

        with _patch_browser_service(fake):
            result = await harness.execute(
                "browser",
                {"operation": "snapshot"},  # no session, no url
            )

        harness.assert_envelope(result, success=True)
        args, session, _, _ = fake.calls[-1]
        assert args == ("snapshot", "-i")
        assert session.startswith("machina_")

    async def test_session_stable_across_calls_in_one_run(self, harness):
        # Two browser calls sharing one execution context (one agent run)
        # must derive the SAME session -> same browser instance. Regression:
        # the Temporal tool path minted a fresh execution_id per call, so
        # every call spawned a new Chrome.
        fake = _FakeBrowserService(canned={"success": True, "data": {}})
        ctx = harness.build_context(execution_id="run1234")

        with _patch_browser_service(fake):
            r1 = await harness.execute(
                "browser",
                {"operation": "navigate", "url": "https://example.com"},
                context=ctx,
            )
            r2 = await harness.execute("browser", {"operation": "snapshot"}, context=ctx)

        harness.assert_envelope(r1, success=True)
        harness.assert_envelope(r2, success=True)
        sessions = [call[1] for call in fake.calls]
        assert sessions == ["machina_run1234", "machina_run1234"]

    async def test_screenshot_with_jpeg_and_quality(self, harness):
        fake = _FakeBrowserService(canned={"success": True, "base64": "AAAA"})

        with _patch_browser_service(fake):
            result = await harness.execute(
                "browser",
                {
                    "operation": "screenshot",
                    "full_page": True,
                    "annotate": True,
                    "screenshot_format": "jpeg",
                    "screenshot_quality": 80,
                },
            )

        harness.assert_envelope(result, success=True)
        args, _, _, _ = fake.calls[-1]
        # Expect all flags to appear in order
        assert args[0] == "screenshot"
        assert "--full" in args
        assert "--annotate" in args
        assert "--screenshot-format" in args
        assert "jpeg" in args
        assert "--screenshot-quality" in args
        assert "80" in args

    async def test_missing_url_for_navigate_returns_error(self, harness):
        fake = _FakeBrowserService()

        with _patch_browser_service(fake):
            result = await harness.execute("browser", {"operation": "navigate"})

        harness.assert_envelope(result, success=False)
        assert "url is required" in result["error"].lower()
        # handler rejected before calling the service
        assert fake.calls == []

    async def test_service_not_installed(self, harness):
        with patch("nodes.browser._service.get_browser_service", return_value=None):
            result = await harness.execute("browser", {"operation": "navigate", "url": "https://x"})

        harness.assert_envelope(result, success=False)
        assert "agent-browser not installed" in result["error"].lower()

    async def test_subprocess_runtime_error_wrapped_in_envelope(self, harness):
        fake = _FakeBrowserService(error=RuntimeError("agent-browser returned empty output"))

        with _patch_browser_service(fake):
            result = await harness.execute("browser", {"operation": "navigate", "url": "https://x"})

        harness.assert_envelope(result, success=False)
        assert "empty output" in result["error"].lower()


# ============================================================================
# crawleeScraper
# ============================================================================


class _FakeBsCtx:
    """Minimal BeautifulSoupCrawlingContext stub for the handler closure."""

    def __init__(self, url: str, title: str = "Example", body_html: str = "<p>hello</p>"):
        from bs4 import BeautifulSoup

        self.request = MagicMock(url=url)
        self.soup = BeautifulSoup(f"<html><head><title>{title}</title></head><body>{body_html}</body></html>", "html.parser")
        self.enqueue_links = AsyncMock(return_value=None)


class _FakeCrawler:
    """Stand-in for BeautifulSoupCrawler that captures the registered handler
    and invokes it against a canned crawling context when `run()` is awaited.
    """

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self._handler = None
        self.router = MagicMock()
        # `router.default_handler` is used as a decorator. Capture the callable.

        def _register(fn):
            self._handler = fn
            return fn

        self.router.default_handler = _register

    async def run(self, urls):
        for url in urls:
            if self._handler:
                await self._handler(_FakeBsCtx(url))


class TestCrawleeScraper:
    async def test_beautifulsoup_single_happy_path(self, harness):
        fake_cls = _FakeCrawler  # class is instantiated inside the handler
        with (
            patch("crawlee.crawlers.BeautifulSoupCrawler", fake_cls),
            patch("crawlee.storage_clients.MemoryStorageClient", MagicMock()),
            patch("crawlee.ConcurrencySettings", MagicMock()),
        ):
            result = await harness.execute(
                "crawleeScraper",
                {
                    "url": "https://example.com",
                    "crawler_type": "beautifulsoup",
                    "mode": "single",
                },
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["pages", "page_count", "crawler_type", "mode", "proxied"])
        payload = result["result"]
        assert payload["crawler_type"] == "beautifulsoup"
        assert payload["mode"] == "single"
        assert payload["proxied"] is False
        assert payload["page_count"] == 1
        page = payload["pages"][0]
        assert page["url"] == "https://example.com"
        assert page["title"] == "Example"
        assert "hello" in page["content"]

    async def test_missing_url_returns_validation_error(self, harness):
        result = await harness.execute("crawleeScraper", {"url": "", "crawler_type": "beautifulsoup"})

        harness.assert_envelope(result, success=False)
        assert "url is required" in result["error"].lower()

    async def test_unknown_crawler_type_returns_error(self, harness):
        # Handler does not attempt any import for unknown crawler types.
        result = await harness.execute(
            "crawleeScraper",
            {"url": "https://x.test", "crawler_type": "teleportron"},
        )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_crawlee_import_error_rewritten(self, harness):
        # Simulate crawlee not installed by making the runtime import raise.
        # The handler imports `from crawlee.crawlers import BeautifulSoupCrawler`
        # inside _run_beautifulsoup, so we patch the module attribute to raise.
        with patch(
            "crawlee.crawlers.BeautifulSoupCrawler",
            side_effect=ImportError("No module named 'crawlee'"),
        ):
            result = await harness.execute(
                "crawleeScraper",
                {"url": "https://x.test", "crawler_type": "beautifulsoup"},
            )

        harness.assert_envelope(result, success=False)
        # Error message is rewritten to an install hint
        assert "crawlee" in result["error"].lower()

    async def test_runtime_exception_returns_envelope(self, harness):
        class BrokenCrawler(_FakeCrawler):
            async def run(self, urls):
                raise RuntimeError("crawler blew up")

        with (
            patch("crawlee.crawlers.BeautifulSoupCrawler", BrokenCrawler),
            patch("crawlee.storage_clients.MemoryStorageClient", MagicMock()),
            patch("crawlee.ConcurrencySettings", MagicMock()),
        ):
            result = await harness.execute(
                "crawleeScraper",
                {"url": "https://x.test", "crawler_type": "beautifulsoup"},
            )

        harness.assert_envelope(result, success=False)
        assert "crawler blew up" in result["error"]


# ============================================================================
# apifyActor
# ============================================================================


def _make_fake_apify_client(
    *,
    run_info: dict | None = None,
    items: list | None = None,
    call_exc: Exception | None = None,
):
    """Build a MagicMock that mimics ApifyClientAsync surface used by the handler."""
    client = MagicMock(name="ApifyClientAsync")

    actor_client = MagicMock(name="ActorClient")
    if call_exc:
        actor_client.call = AsyncMock(side_effect=call_exc)
    else:
        actor_client.call = AsyncMock(return_value=run_info)
    client.actor = MagicMock(return_value=actor_client)

    list_result = MagicMock()
    list_result.items = items or []
    dataset_client = MagicMock(name="DatasetClient")
    dataset_client.list_items = AsyncMock(return_value=list_result)
    client.dataset = MagicMock(return_value=dataset_client)

    return client


class TestApifyActor:
    async def test_happy_path_returns_dataset_items(self, harness):
        run_info = {
            "id": "run_123",
            "status": "SUCCEEDED",
            "defaultDatasetId": "ds_456",
            "usageTotalUsd": 0.02,
            "startedAt": "2026-04-15T00:00:00Z",
            "finishedAt": "2026-04-15T00:00:05Z",
        }
        items = [{"url": "https://a.example"}, {"url": "https://b.example"}]
        fake_client = _make_fake_apify_client(run_info=run_info, items=items)

        with patched_container(auth_api_keys={"apify": "tk_apify"}), patch("apify_client.ApifyClientAsync", return_value=fake_client):
            result = await harness.execute(
                "apifyActor",
                {
                    "actor_id": "apify/instagram-scraper",
                    "instagram_urls": "https://instagram.com/a, https://instagram.com/b",
                    "max_results": 50,
                },
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result,
            [
                "run_id",
                "actor_id",
                "status",
                "items",
                "item_count",
                "dataset_id",
                "compute_units",
                "started_at",
                "finished_at",
            ],
        )
        payload = result["result"]
        assert payload["run_id"] == "run_123"
        assert payload["actor_id"] == "apify/instagram-scraper"
        assert payload["status"] == "SUCCEEDED"
        assert payload["item_count"] == 2
        assert payload["items"] == items
        assert payload["dataset_id"] == "ds_456"
        assert payload["compute_units"] == 0.02

        # Quick-helper merge: call_args run_input should contain directUrls
        call_kwargs = fake_client.actor.return_value.call.await_args.kwargs
        run_input = call_kwargs["run_input"]
        assert run_input["directUrls"] == [
            "https://instagram.com/a",
            "https://instagram.com/b",
        ]
        assert call_kwargs["memory_mbytes"] == 1024  # default
        # dataset list called with the right limit
        fake_client.dataset.return_value.list_items.assert_awaited_once_with(limit=50)

    async def test_missing_api_token_short_circuits(self, harness):
        # container returns no 'apify' key -> _get_apify_client returns None
        with patched_container(auth_api_keys={}):
            result = await harness.execute(
                "apifyActor",
                {"actor_id": "apify/instagram-scraper"},
            )

        harness.assert_envelope(result, success=False)
        assert "apify api token not configured" in result["error"].lower()

    async def test_missing_actor_id_returns_validation_error(self, harness):
        fake_client = _make_fake_apify_client(run_info={})

        with patched_container(auth_api_keys={"apify": "tk"}), patch("apify_client.ApifyClientAsync", return_value=fake_client):
            result = await harness.execute("apifyActor", {"actor_id": ""})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_failed_run_status_returns_error_envelope(self, harness):
        run_info = {
            "id": "run_err",
            "status": "FAILED",
            "errorMessage": "Actor crashed at step 3",
            "defaultDatasetId": "",
        }
        fake_client = _make_fake_apify_client(run_info=run_info, items=[])

        with patched_container(auth_api_keys={"apify": "tk"}), patch("apify_client.ApifyClientAsync", return_value=fake_client):
            result = await harness.execute("apifyActor", {"actor_id": "apify/instagram-scraper"})

        harness.assert_envelope(result, success=False)
        assert "actor crashed at step 3" in result["error"].lower()

    async def test_unauthorized_exception_rewritten(self, harness):
        fake_client = _make_fake_apify_client(call_exc=RuntimeError("Request failed: 401 Unauthorized"))

        with patched_container(auth_api_keys={"apify": "tk"}), patch("apify_client.ApifyClientAsync", return_value=fake_client):
            result = await harness.execute("apifyActor", {"actor_id": "apify/instagram-scraper"})

        harness.assert_envelope(result, success=False)
        assert "401" in result["error"].lower() or "unauthorized" in result["error"].lower()

    async def test_invalid_actor_input_json_silently_becomes_empty_dict(self, harness):
        run_info = {
            "id": "run_ok",
            "status": "SUCCEEDED",
            "defaultDatasetId": "ds",
            "usageTotalUsd": 0,
            "startedAt": "",
            "finishedAt": "",
        }
        fake_client = _make_fake_apify_client(run_info=run_info, items=[])

        with patched_container(auth_api_keys={"apify": "tk"}), patch("apify_client.ApifyClientAsync", return_value=fake_client):
            result = await harness.execute(
                "apifyActor",
                {
                    "actor_id": "apify/instagram-scraper",
                    "actorInput": "{ this is not json",
                },
            )

        harness.assert_envelope(result, success=True)
        call_kwargs = fake_client.actor.return_value.call.await_args.kwargs
        # malformed JSON becomes {} not a parse error
        assert call_kwargs["run_input"] == {}
