"""HTTP Scraper — Wave 11.D.7 inlined.

Scrapes links from web pages with optional date/page pagination.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

logger = get_logger(__name__)


class HttpScraperParams(BaseModel):
    url: str = Field(
        default="",
        description="URL template. Use {date} for date mode, {page} for page mode.",
    )
    iteration_mode: Literal["single", "date", "page"] = Field(
        default="single",
        description="single: one URL. date: iterate dates. page: iterate page numbers.",
    )
    link_selector: str = Field(
        default='a[href$=".pdf"]',
        description="CSS selector for links to extract from each fetched page.",
    )
    headers: str = Field(
        default="{}",
        description="JSON string of request headers.",
        json_schema_extra={"rows": 3},
    )

    # Date-mode fields
    start_date: str = Field(
        default="",
        description="Start date (YYYY-MM-DD).",
        json_schema_extra={"displayOptions": {"show": {"iteration_mode": ["date"]}}},
    )
    end_date: str = Field(
        default="",
        description="End date (YYYY-MM-DD).",
        json_schema_extra={"displayOptions": {"show": {"iteration_mode": ["date"]}}},
    )
    date_placeholder: str = Field(
        default="{date}",
        description="Placeholder token in URL that gets replaced with each date.",
        json_schema_extra={"displayOptions": {"show": {"iteration_mode": ["date"]}}},
    )

    # Page-mode fields
    start_page: int = Field(
        default=1, ge=1,
        description="First page number.",
        json_schema_extra={"displayOptions": {"show": {"iteration_mode": ["page"]}}},
    )
    end_page: int = Field(
        default=10, ge=1,
        description="Last page number (inclusive).",
        json_schema_extra={"displayOptions": {"show": {"iteration_mode": ["page"]}}},
    )
    max_pages: int = Field(
        default=10, ge=1, le=1000,
        description="Safety cap on pages fetched.",
    )

    # Proxy
    use_proxy: bool = Field(
        default=False,
        description="Route through residential proxy provider.",
    )
    proxy_provider: str = Field(
        default="auto",
        description="Provider name ('auto' selects by health score).",
        json_schema_extra={"displayOptions": {"show": {"use_proxy": [True]}}},
    )
    proxy_country: str = Field(
        default="",
        description="ISO country code for geo-targeting.",
        json_schema_extra={"displayOptions": {"show": {"use_proxy": [True]}}},
    )
    session_type: Literal["rotating", "sticky"] = Field(
        default="rotating",
        json_schema_extra={"displayOptions": {"show": {"use_proxy": [True]}}},
    )
    sticky_duration: int = Field(
        default=600, ge=1,
        json_schema_extra={
            "displayOptions": {"show": {"use_proxy": [True], "session_type": ["sticky"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class HttpScraperOutput(BaseModel):
    items: Optional[list] = None
    item_count: Optional[int] = None
    errors: Optional[list] = None

    model_config = ConfigDict(extra="allow")


class HttpScraperNode(ActionNode):
    type = "httpScraper"
    display_name = "HTTP Scraper"
    subtitle = "Page Pagination"
    group = ("document",)
    description = "Scrape links from web pages with date/page pagination support"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.REST_API

    Params = HttpScraperParams
    Output = HttpScraperOutput

    @Operation("scrape")
    async def scrape(self, ctx: NodeContext, params: HttpScraperParams) -> HttpScraperOutput:
        url = params.url
        if not url:
            raise NodeUserError("URL is required")

        iteration_mode = params.iteration_mode
        link_selector = params.link_selector or 'a[href$=".pdf"]'
        headers_str = params.headers or '{}'
        headers = json.loads(headers_str) if isinstance(headers_str, str) and headers_str else {}

        urls_to_fetch = []
        if iteration_mode == 'date':
            if not params.start_date or not params.end_date:
                raise NodeUserError("start_date/end_date required for date mode")
            placeholder = params.date_placeholder or '{date}'
            start = datetime.strptime(params.start_date, "%Y-%m-%d")
            end = datetime.strptime(params.end_date, "%Y-%m-%d")
            current = start
            while current <= end:
                urls_to_fetch.append((
                    url.replace(placeholder, current.strftime("%Y-%m-%d")),
                    {'date': current.isoformat()},
                ))
                current += timedelta(days=1)
        elif iteration_mode == 'page':
            for page in range(params.start_page, params.end_page + 1):
                urls_to_fetch.append((url.replace('{page}', str(page)), {'page': page}))
        else:
            urls_to_fetch.append((url, {}))

        proxy_url = None
        if params.use_proxy:
            try:
                from services.proxy.service import get_proxy_service
                proxy_svc = get_proxy_service()
                if proxy_svc and proxy_svc.is_enabled():
                    proxy_url = await proxy_svc.get_proxy_url(url, params.model_dump())
            except Exception as e:
                logger.warning("[httpScraper] Proxy lookup failed", error=str(e))

        items, errors = [], []
        client_kwargs: dict = {"timeout": 30, "follow_redirects": True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(**client_kwargs) as client:
            for fetch_url, meta in urls_to_fetch:
                try:
                    response = await client.get(fetch_url, headers=headers)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for el in soup.select(link_selector):
                        href = el.get('href', '')
                        if href:
                            items.append({
                                'url': urljoin(fetch_url, href),
                                'text': el.get_text(strip=True),
                                'source_url': fetch_url,
                                **meta,
                            })
                except Exception as e:
                    errors.append(f"{fetch_url}: {e}")

        return HttpScraperOutput(items=items, item_count=len(items), errors=errors)
