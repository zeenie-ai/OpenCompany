"""Crawlee Scraper — Wave 11.D.8 inlined.

Thin wrapper over ``crawlee.BeautifulSoupCrawler`` (static HTML) and
``crawlee.PlaywrightCrawler`` (JS-rendered). Crawlee handles concurrency,
retries, storage, and anti-bot internally.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

logger = get_logger(__name__)

_MAX_CONTENT_LENGTH = 100_000


async def _get_proxy_config(parameters: Dict[str, Any], url: str):
    """Bridge MachinaOs ProxyService to Crawlee ProxyConfiguration."""
    if not parameters.get("use_proxy", False):
        return None
    try:
        from services.proxy import get_proxy_service

        proxy_svc = get_proxy_service()
        proxy_url = await proxy_svc.get_proxy_url(url, parameters)
        if not proxy_url:
            return None
        from crawlee.proxy_configuration import ProxyConfiguration

        return ProxyConfiguration(proxy_urls=[proxy_url])
    except Exception as e:
        logger.warning(f"[Crawlee] Proxy setup failed, proceeding without: {e}")
        return None


def _extract_text(soup, css_selector: str, output_format: str) -> str:
    target = soup.select(css_selector) if css_selector else [soup]
    if output_format == "html":
        parts = [str(el) for el in target]
    else:
        parts = [el.get_text(separator="\n", strip=True) for el in target]
    text = "\n\n".join(parts)
    if output_format == "markdown":
        try:
            import html2text

            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = False
            h.body_width = 0
            raw_html = "\n\n".join(str(el) for el in target)
            text = h.handle(raw_html)
        except ImportError:
            pass
    return text[:_MAX_CONTENT_LENGTH]


def _extract_links(soup, base_url: str) -> List[str]:
    return [urljoin(base_url, a["href"]) for a in soup.find_all("a", href=True)]


async def _run_beautifulsoup(
    url, pages, p, proxy_config, css_selector, extract_links, output_format, mode, max_pages, max_depth, max_concurrency, timeout_secs
):
    from crawlee import ConcurrencySettings
    from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
    from crawlee.storage_clients import MemoryStorageClient

    crawler = BeautifulSoupCrawler(
        max_requests_per_crawl=max_pages,
        max_crawl_depth=max_depth if mode == "crawl" else 0,
        request_handler_timeout=timedelta(seconds=timeout_secs),
        concurrency_settings=ConcurrencySettings(
            max_concurrency=max_concurrency,
            desired_concurrency=min(max_concurrency, 10),
        ),
        storage_client=MemoryStorageClient(),
        proxy_configuration=proxy_config,
        configure_logging=False,
    )
    link_selector = p.get("link_selector", "") or "a[href]"
    url_pattern = p.get("url_pattern", "")

    @crawler.router.default_handler
    async def handler(ctx: BeautifulSoupCrawlingContext) -> None:
        title = ctx.soup.title.string if ctx.soup.title else ""
        content = _extract_text(ctx.soup, css_selector, output_format)
        page_data: Dict[str, Any] = {
            "url": ctx.request.url,
            "title": title,
            "content": content,
        }
        if extract_links:
            page_data["links"] = _extract_links(ctx.soup, ctx.request.url)
        pages.append(page_data)

        if mode == "crawl":
            kwargs: Dict[str, Any] = {"selector": link_selector}
            if url_pattern:
                import re
                from fnmatch import translate

                kwargs["include"] = [re.compile(translate(url_pattern))]
            await ctx.enqueue_links(**kwargs)

    await asyncio.wait_for(crawler.run([url]), timeout=timeout_secs)


async def _run_playwright(
    url, pages, p, proxy_config, css_selector, extract_links, output_format, mode, max_pages, max_depth, max_concurrency, timeout_secs
):
    from crawlee import ConcurrencySettings
    from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
    from crawlee.storage_clients import MemoryStorageClient

    browser_type = p.get("browser_type", "chromium")
    wait_for_selector = p.get("wait_for_selector", "")
    wait_timeout = p.get("wait_timeout", 30000)
    take_screenshot = p.get("screenshot", False) or p.get("take_screenshot", False)

    crawler = PlaywrightCrawler(
        browser_type=browser_type,
        headless=True,
        max_requests_per_crawl=max_pages,
        max_crawl_depth=max_depth if mode == "crawl" else 0,
        request_handler_timeout=timedelta(seconds=timeout_secs),
        concurrency_settings=ConcurrencySettings(
            max_concurrency=max_concurrency,
            desired_concurrency=min(max_concurrency, 10),
        ),
        storage_client=MemoryStorageClient(),
        proxy_configuration=proxy_config,
        configure_logging=False,
    )
    link_selector = p.get("link_selector", "") or "a[href]"
    url_pattern = p.get("url_pattern", "")

    @crawler.router.default_handler
    async def handler(ctx: PlaywrightCrawlingContext) -> None:
        page = ctx.page
        if wait_for_selector:
            await page.wait_for_selector(wait_for_selector, timeout=wait_timeout)
        title = await page.title()
        html = await page.content()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        content = _extract_text(soup, css_selector, output_format)
        page_data: Dict[str, Any] = {
            "url": ctx.request.url,
            "title": title,
            "content": content,
        }
        if extract_links:
            page_data["links"] = _extract_links(soup, ctx.request.url)
        if take_screenshot:
            import base64

            screenshot_bytes = await page.screenshot(type="png")
            page_data["screenshot"] = base64.b64encode(screenshot_bytes).decode()
        pages.append(page_data)

        if mode == "crawl":
            kwargs: Dict[str, Any] = {"selector": link_selector}
            if url_pattern:
                import re
                from fnmatch import translate

                kwargs["include"] = [re.compile(translate(url_pattern))]
            await ctx.enqueue_links(**kwargs)

    await asyncio.wait_for(crawler.run([url]), timeout=timeout_secs)


class CrawleeScraperParams(BaseModel):
    tool_name: str = Field(
        default="web_scraper",
        description="Name shown to the LLM when used as a tool.",
    )
    tool_description: str = Field(
        default="Scrape a web page and extract text, HTML, or links. Supports single-page and multi-page crawling.",
        description="Description shown to the LLM when used as a tool.",
        json_schema_extra={"rows": 3},
    )
    url: str = Field(default="", description="Target URL.")
    crawler_type: Literal["beautifulsoup", "playwright", "adaptive"] = Field(
        default="beautifulsoup",
        description="beautifulsoup: fast static HTML. playwright: JS-rendered. adaptive: auto-detect.",
    )
    mode: Literal["single", "crawl"] = Field(
        default="single",
        description="single: scrape only the given URL. crawl: follow links.",
    )
    css_selector: str = Field(
        default="",
        description="Optional CSS selector to narrow extraction.",
    )
    extract_links: bool = Field(default=False, description="Include hrefs in output.")
    output_format: Literal["text", "html", "markdown"] = Field(default="text")
    max_pages: int = Field(default=10, ge=1, le=1000)
    max_concurrency: int = Field(default=5, ge=1, le=50, description="Concurrent requests.")
    timeout: int = Field(default=60, ge=1, le=3600, description="Per-page timeout in seconds.")

    # Crawl-mode fields
    link_selector: str = Field(
        default="",
        description="Selector for links to follow.",
        json_schema_extra={"displayOptions": {"show": {"mode": ["crawl"]}}},
    )
    url_pattern: str = Field(
        default="",
        description="Glob pattern for URLs to include (e.g. https://example.com/*).",
        json_schema_extra={"displayOptions": {"show": {"mode": ["crawl"]}}},
    )
    max_depth: int = Field(
        default=2,
        ge=0,
        le=50,
        description="Max link-following depth.",
        json_schema_extra={"displayOptions": {"show": {"mode": ["crawl"]}}},
    )

    # Playwright-only fields
    browser_type: Literal["chromium", "firefox", "webkit"] = Field(
        default="chromium",
        json_schema_extra={"displayOptions": {"show": {"crawler_type": ["playwright"]}}},
    )
    wait_for_selector: str = Field(
        default="",
        description="CSS selector to wait for before extracting (JS-rendered).",
        json_schema_extra={"displayOptions": {"show": {"crawler_type": ["playwright"]}}},
    )
    wait_timeout: int = Field(
        default=30000,
        ge=0,
        le=600000,
        description="Wait-for-selector timeout in milliseconds.",
        json_schema_extra={"displayOptions": {"show": {"crawler_type": ["playwright"]}}},
    )
    take_screenshot: bool = Field(
        default=False,
        description="Capture a base64 PNG screenshot per page.",
        json_schema_extra={"displayOptions": {"show": {"crawler_type": ["playwright"]}}},
    )
    block_resources: bool = Field(
        default=False,
        description="Block images/fonts/media to speed up rendering.",
        json_schema_extra={"displayOptions": {"show": {"crawler_type": ["playwright"]}}},
    )

    # Proxy
    use_proxy: bool = Field(default=False)
    proxy_provider: str = Field(
        default="auto",
        json_schema_extra={"displayOptions": {"show": {"use_proxy": [True]}}},
    )
    proxy_country: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"use_proxy": [True]}}},
    )
    session_type: Literal["rotating", "sticky"] = Field(
        default="rotating",
        json_schema_extra={"displayOptions": {"show": {"use_proxy": [True]}}},
    )
    sticky_duration: int = Field(
        default=600,
        ge=1,
        json_schema_extra={
            "displayOptions": {"show": {"use_proxy": [True], "session_type": ["sticky"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class CrawleeScraperOutput(BaseModel):
    pages: Optional[list] = None
    page_count: Optional[int] = None
    crawler_type: Optional[str] = None
    mode: Optional[str] = None
    proxied: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class CrawleeScraperNode(ActionNode):
    type = "crawleeScraper"
    display_name = "Web Scraper"
    subtitle = "Crawlee"
    group = ("scraper", "tool")
    description = "Web scraper supporting static HTML (BeautifulSoup) and JS-rendered (Playwright)"
    component_kind = "square"
    tool_name = "web_reader"
    tool_description = "Read and extract content from web pages. Fetches page text, links, and data. Use beautifulsoup for static pages or playwright for JS-rendered pages. You MUST use this tool when the user asks to read, fetch, or get content from any URL."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.BROWSER
    usable_as_tool = True

    Params = CrawleeScraperParams
    Output = CrawleeScraperOutput

    @Operation("scrape")
    async def scrape(self, ctx: NodeContext, params: CrawleeScraperParams) -> CrawleeScraperOutput:
        url = (params.url or "").strip()
        if not url:
            raise NodeUserError("URL is required")

        crawler_type = params.crawler_type
        mode = params.mode
        css_selector = params.css_selector
        extract_links = params.extract_links
        output_format = params.output_format
        max_pages = params.max_pages if mode == "crawl" else 1
        max_depth = params.max_depth if mode == "crawl" else 0
        max_concurrency = params.max_concurrency
        timeout_secs = params.timeout

        pages: List[Dict[str, Any]] = []
        p = params.model_dump()
        proxy_config = await _get_proxy_config(p, url)

        try:
            if crawler_type in ("beautifulsoup", "adaptive"):
                await _run_beautifulsoup(
                    url,
                    pages,
                    p,
                    proxy_config,
                    css_selector,
                    extract_links,
                    output_format,
                    mode,
                    max_pages,
                    max_depth,
                    max_concurrency,
                    timeout_secs,
                )
            elif crawler_type == "playwright":
                await _run_playwright(
                    url,
                    pages,
                    p,
                    proxy_config,
                    css_selector,
                    extract_links,
                    output_format,
                    mode,
                    max_pages,
                    max_depth,
                    max_concurrency,
                    timeout_secs,
                )
            else:
                raise NodeUserError(f"Unknown crawler type: {crawler_type}")
        except ImportError as e:
            msg = str(e).lower()
            if "playwright" in msg:
                raise NodeUserError(
                    "Playwright not installed. Run: " "pip install 'crawlee[playwright]' && playwright install chromium",
                )
            if "crawlee" in msg:
                raise NodeUserError(
                    "Crawlee not installed. Run: pip install 'crawlee[beautifulsoup]'",
                )
            raise

        logger.info(f"[Crawlee] Scraped {len(pages)} page(s) from {url}")
        return CrawleeScraperOutput(
            pages=pages,
            page_count=len(pages),
            crawler_type=crawler_type,
            mode=mode,
            proxied=proxy_config is not None,
        )
