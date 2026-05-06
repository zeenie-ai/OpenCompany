"""Brave Search — Wave 11.B reference migration.

One file. Declaration + credential + params + output + handler, all
in the class body. Replaces the Wave 10 metadata-only registration in
``server/nodes/services.py:braveSearch`` + the imperative handler in
``server/services/handlers/search.py:handle_brave_search``.

Pure-declarative path isn't quite a fit — the Brave response needs
per-item projection from ``{title, description, url}`` to our
``{title, snippet, url}`` wire format, which the ``post_receive``
strategy set doesn't express yet. So we keep a short imperative
operation body, but the Connection facade still eliminates the
api-key fetch + 30 LOC of try/except boilerplate.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from services.plugin import (
    ActionNode,
    ApiKeyCredential,
    NodeContext,
    Operation,
    TaskQueue,
)


class BraveSearchCredential(ApiKeyCredential):
    id = "brave_search"
    display_name = "Brave Search"
    category = "Search"
    icon = "asset:brave"
    key_name = "X-Subscription-Token"
    key_location = "header"
    docs_url = "https://api.search.brave.com/app/keys"
    # Lightweight probe — minimal query, count=1 just to confirm the
    # token authenticates against the web-search endpoint.
    probe_url = "https://api.search.brave.com/res/v1/web/search"
    probe_params = {"q": "ping", "count": 1}


class BraveSearchResult(BaseModel):
    title: str = ""
    snippet: str = ""
    url: str = ""


class BraveSearchOutput(BaseModel):
    query: str
    results: List[BraveSearchResult] = Field(default_factory=list)
    result_count: int = 0
    provider: Literal["brave_search"] = "brave_search"


class BraveSearchParams(BaseModel):
    tool_name: str = Field(
        default="brave_search",
        description="Override name shown to the LLM when used as a tool.",
    )
    tool_description: str = Field(
        default="Search the web using the Brave Search API. Returns web results with titles, snippets, and URLs.",
        description="Override description shown to the LLM when used as a tool.",
        json_schema_extra={"rows": 3},
    )
    query: str = Field(..., description="Search query", min_length=1)
    max_results: int = Field(default=10, ge=1, le=20)
    country: str = Field(default="", description="ISO country code (e.g. US, GB)")
    search_lang: str = Field(default="en")
    safe_search: Literal["off", "moderate", "strict"] = Field(
        default="moderate"
    )

    model_config = {"extra": "ignore"}


class BraveSearchNode(ActionNode):
    type = "braveSearch"
    display_name = "Brave Search"
    subtitle = "Web Search"
    group = ("search", "tool")
    description = "Search the web using Brave Search API"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (BraveSearchCredential,)
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = BraveSearchParams
    Output = BraveSearchOutput

    @Operation("search", cost={"service": "brave_search", "action": "web_search", "count": 1})
    async def search(self, ctx: NodeContext, params: BraveSearchParams) -> BraveSearchOutput:
        qs: dict = {"q": params.query, "count": min(params.max_results, 20)}
        if params.country:
            qs["country"] = params.country
        if params.search_lang:
            qs["search_lang"] = params.search_lang
        if params.safe_search:
            qs["safesearch"] = params.safe_search

        async with ctx.connection("brave_search") as conn:
            response = await conn.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=qs,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        web_results = (data.get("web") or {}).get("results") or []
        items = [
            BraveSearchResult(
                title=item.get("title", ""),
                snippet=item.get("description", ""),
                url=item.get("url", ""),
            )
            for item in web_results[: params.max_results]
        ]
        return BraveSearchOutput(
            query=params.query, results=items, result_count=len(items),
        )
