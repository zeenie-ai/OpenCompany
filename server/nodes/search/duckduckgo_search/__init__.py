"""DuckDuckGo Search — Wave 11.C migration.

Free web search, no API key, no credentials. Uses the ``ddgs`` Python
library — a synchronous client wrapped via ``run_in_executor``.

This is a pure ToolNode (the only entry point is AI agents calling it
via input-tools). No usable_as_tool flag needed — being a ToolNode is
enough.
"""

from __future__ import annotations

import asyncio
from typing import List

from pydantic import BaseModel, Field

from services.plugin import NodeContext, Operation, TaskQueue, ToolNode


class DuckDuckGoResult(BaseModel):
    title: str = ""
    snippet: str = ""
    url: str = ""


class DuckDuckGoSearchOutput(BaseModel):
    query: str
    results: List[DuckDuckGoResult] = Field(default_factory=list)
    provider: str = "duckduckgo"


class DuckDuckGoSearchParams(BaseModel):
    query: str = Field(..., description="Search query", min_length=1)
    max_results: int = Field(default=5, ge=1, le=20)


class DuckDuckGoSearchNode(ToolNode):
    type = "duckduckgoSearch"
    display_name = "DuckDuckGo Search"
    subtitle = "Free Web Search"
    group = ("tool", "ai", "search")
    description = "DuckDuckGo web search (free, no API key required)"
    component_kind = "tool"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top",
         "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.REST_API

    Params = DuckDuckGoSearchParams
    Output = DuckDuckGoSearchOutput

    @Operation("search")
    async def search(
        self, ctx: NodeContext, params: DuckDuckGoSearchParams,
    ) -> DuckDuckGoSearchOutput:
        from ddgs import DDGS

        def _search_sync():
            return list(DDGS().text(params.query, max_results=params.max_results))

        raw = await asyncio.get_event_loop().run_in_executor(None, _search_sync)
        results = [
            DuckDuckGoResult(
                title=item.get("title", ""),
                snippet=item.get("body", ""),
                url=item.get("href", ""),
            )
            for item in raw
        ]
        return DuckDuckGoSearchOutput(query=params.query, results=results)
