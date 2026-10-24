"""Contract tests for web_automation nodes: crawleeScraper, apifyActor.

The Browser node has its own suite in ``tests/nodes/browser/``.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/web_automation/`. A refactor that breaks any of
these indicates the docs (and the user-visible contract) need to be updated too.

All external side-effects are mocked:
  - crawleeScraper: patches the crawlee crawler classes the handler imports
    lazily (`BeautifulSoupCrawler`, `PlaywrightCrawler`).
  - apifyActor: patches `apify_client.ApifyClientAsync` with a client whose
    resource clients are autospecs of the installed SDK's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from tests.nodes._mocks import patched_container


pytestmark = pytest.mark.node_contract


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


def _run(**fields):
    """An SDK ``Run`` holding the fields the node reads; the API sends many more."""
    from apify_client._models import Run

    values = {
        "id": "run_1",
        "status": "SUCCEEDED",
        "status_message": None,
        "default_dataset_id": "",
        "usage_total_usd": None,
        "started_at": datetime(2026, 4, 15, tzinfo=timezone.utc),
        "finished_at": None,
    }
    values.update(fields)
    return Run.model_construct(**values)


def _make_fake_apify_client(
    *,
    run=None,
    items: list | None = None,
    call_exc: Exception | None = None,
):
    """A client whose resource clients carry the installed SDK's signatures.

    ``create_autospec`` fails a call with a keyword the SDK does not take, as
    the SDK itself would: apify-client 3.x dropped 1.x's ``timeout_secs``, and
    every run raised TypeError while these fakes accepted anything.
    """
    from apify_client._resource_clients import ActorClientAsync, DatasetClientAsync
    from apify_client._resource_clients.dataset import DatasetItemsPage

    client = MagicMock(name="ApifyClientAsync")

    actor_client = create_autospec(ActorClientAsync, instance=True)
    if call_exc:
        actor_client.call.side_effect = call_exc
    else:
        actor_client.call.return_value = run
    client.actor = MagicMock(return_value=actor_client)

    page_items = items or []
    dataset_client = create_autospec(DatasetClientAsync, instance=True)
    dataset_client.list_items.return_value = DatasetItemsPage(
        items=page_items, total=len(page_items), offset=0, count=len(page_items), limit=len(page_items), desc=False,
    )
    client.dataset = MagicMock(return_value=dataset_client)

    return client


class TestApifyActor:
    async def test_happy_path_returns_dataset_items(self, harness):
        run = _run(
            id="run_123",
            default_dataset_id="ds_456",
            usage_total_usd=0.02,
            started_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 15, 0, 0, 5, tzinfo=timezone.utc),
        )
        items = [{"url": "https://a.example"}, {"url": "https://b.example"}]
        fake_client = _make_fake_apify_client(run=run, items=items)

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
        assert payload["started_at"] == "2026-04-15T00:00:00+00:00"
        assert payload["finished_at"] == "2026-04-15T00:00:05+00:00"

        # Quick-helper merge: call_args run_input should contain directUrls
        call_kwargs = fake_client.actor.return_value.call.await_args.kwargs
        run_input = call_kwargs["run_input"]
        assert run_input["directUrls"] == [
            "https://instagram.com/a",
            "https://instagram.com/b",
        ]
        assert call_kwargs["memory_mbytes"] == 1024  # default
        assert call_kwargs["run_timeout"] == timedelta(seconds=300)  # default
        assert call_kwargs["logger"] is None  # the actor's log is not streamed into ours
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
        fake_client = _make_fake_apify_client(run=_run())

        with patched_container(auth_api_keys={"apify": "tk"}), patch("apify_client.ApifyClientAsync", return_value=fake_client):
            result = await harness.execute("apifyActor", {"actor_id": ""})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_failed_run_status_returns_error_envelope(self, harness):
        run = _run(id="run_err", status="FAILED", status_message="Actor crashed at step 3")
        fake_client = _make_fake_apify_client(run=run, items=[])

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
        run = _run(id="run_ok", default_dataset_id="ds", usage_total_usd=0)
        fake_client = _make_fake_apify_client(run=run, items=[])

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


class TestApifyTokenValidation:
    """The Credentials modal's key check. On apify-client 3.x the SDK
    returns models, not dicts, so a valid key read as invalid."""

    async def _validate(self, user_info):
        from apify_client._resource_clients import UserClientAsync
        from nodes.scraper.apify_actor import validate_apify_token

        user_client = create_autospec(UserClientAsync, instance=True)
        user_client.get.return_value = user_info
        client = MagicMock(name="ApifyClientAsync")
        client.user = MagicMock(return_value=user_client)
        with patch("apify_client.ApifyClientAsync", return_value=client):
            return await validate_apify_token("tk")

    async def test_private_info_gives_username_email_and_plan(self):
        from apify_client._models import Plan, UserPrivateInfo

        info = UserPrivateInfo.model_construct(
            username="ada", email="ada@example.com", plan=Plan.model_construct(id="PERSONAL"),
        )
        assert await self._validate(info) == {
            "valid": True, "username": "ada", "email": "ada@example.com", "plan": "PERSONAL",
        }

    async def test_public_info_has_no_email_or_plan(self):
        from apify_client._models import UserPublicInfo

        info = UserPublicInfo.model_construct(username="ada")
        assert await self._validate(info) == {"valid": True, "username": "ada", "email": "", "plan": "free"}

    async def test_missing_user_info_is_invalid(self):
        assert (await self._validate(None))["valid"] is False
