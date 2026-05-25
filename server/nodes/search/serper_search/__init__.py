"""Serper Search — Wave 11.C migration. Google SERP via Serper API."""

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


class SerperCredential(ApiKeyCredential):
    id = "serper"
    display_name = "Serper"
    category = "Search"
    key_name = "X-API-KEY"
    key_location = "header"
    docs_url = "https://serper.dev/api-key"
    probe_url = "https://google.serper.dev/search"
    probe_method = "POST"
    probe_json = {"q": "ping", "num": 1}


class SerperSearchResult(BaseModel):
    title: str = ""
    snippet: str = ""
    url: str = ""
    position: Optional[int] = None


class SerperSearchOutput(BaseModel):
    query: str
    results: List[SerperSearchResult] = Field(default_factory=list)
    result_count: int = 0
    search_type: str = "search"
    knowledge_graph: Optional[dict] = None
    provider: Literal["serper"] = "serper"


class SerperSearchParams(BaseModel):
    tool_name: str = Field(
        default="serper_search",
        description="Override name shown to the LLM when used as a tool.",
    )
    tool_description: str = Field(
        default="Search the web using Google via Serper API. Supports web, news, images, and places search with knowledge graph.",
        description="Override description shown to the LLM when used as a tool.",
        json_schema_extra={"rows": 3},
    )
    query: str = Field(..., min_length=1)
    search_type: Literal["search", "news", "images", "places"] = Field(
        default="search",
    )
    max_results: int = Field(default=10, ge=1, le=100)
    country: str = Field(default="")
    language: str = Field(default="en")

    model_config = {"extra": "ignore"}


_ENDPOINTS = {
    "search": "https://google.serper.dev/search",
    "news": "https://google.serper.dev/news",
    "images": "https://google.serper.dev/images",
    "places": "https://google.serper.dev/places",
}


class SerperSearchNode(ActionNode):
    type = "serperSearch"
    display_name = "Serper Search"
    subtitle = "Google SERP"
    group = ("search", "tool")
    description = "Search the web using Google via Serper API (web/news/images/places)"
    component_kind = "square"
    tool_name = "serper_search"
    tool_description = "Search the web using Google via Serper API. Returns web results with titles, snippets, and URLs."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (SerperCredential,)
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = SerperSearchParams
    Output = SerperSearchOutput

    @Operation("search", cost={"service": "serper", "action": "web_search", "count": 1})
    async def search(self, ctx: NodeContext, params: SerperSearchParams) -> SerperSearchOutput:
        body: dict = {"q": params.query, "num": min(params.max_results, 100)}
        if params.country:
            body["gl"] = params.country
        if params.language:
            body["hl"] = params.language

        async with ctx.connection("serper") as conn:
            response = await conn.post(
                _ENDPOINTS.get(params.search_type, _ENDPOINTS["search"]),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        results: List[SerperSearchResult] = []
        if params.search_type == "search":
            for item in (data.get("organic") or [])[: params.max_results]:
                results.append(
                    SerperSearchResult(
                        title=item.get("title", ""),
                        snippet=item.get("snippet", ""),
                        url=item.get("link", ""),
                        position=item.get("position"),
                    )
                )
        elif params.search_type == "news":
            for item in (data.get("news") or [])[: params.max_results]:
                results.append(
                    SerperSearchResult(
                        title=item.get("title", ""),
                        snippet=item.get("snippet", ""),
                        url=item.get("link", ""),
                    )
                )
        elif params.search_type == "images":
            for item in (data.get("images") or [])[: params.max_results]:
                results.append(
                    SerperSearchResult(
                        title=item.get("title", ""),
                        url=item.get("imageUrl") or item.get("link", ""),
                    )
                )
        elif params.search_type == "places":
            for item in (data.get("places") or [])[: params.max_results]:
                results.append(
                    SerperSearchResult(
                        title=item.get("title", ""),
                        url=item.get("website", ""),
                    )
                )
        return SerperSearchOutput(
            query=params.query,
            results=results,
            result_count=len(results),
            search_type=params.search_type,
            knowledge_graph=data.get("knowledgeGraph"),
        )
