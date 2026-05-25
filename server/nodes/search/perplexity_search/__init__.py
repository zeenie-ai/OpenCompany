"""Perplexity Sonar Search — Wave 11.C migration.

AI-powered search returning markdown answer + citations. The
Connection facade injects ``Authorization: Bearer <api_key>`` via
``ApiKeyCredential.key_location="bearer"``. Single-op declarative-ish:
imperative body is short enough that a routing DSL roundtrip would
add more lines than it saves.
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


class PerplexityCredential(ApiKeyCredential):
    id = "perplexity"
    display_name = "Perplexity"
    category = "Search"
    key_name = "Authorization"
    key_location = "bearer"
    docs_url = "https://docs.perplexity.ai/guides/getting-started"
    # Cheapest valid request: minimal completion, max_tokens=1.
    # Sonar is the always-available default model -- no entitlement gate.
    probe_url = "https://api.perplexity.ai/chat/completions"
    probe_method = "POST"
    probe_json = {
        "model": "sonar",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }


class PerplexityResult(BaseModel):
    url: str = ""


class PerplexitySearchOutput(BaseModel):
    query: str
    answer: str = ""
    citations: List[str] = Field(default_factory=list)
    results: List[PerplexityResult] = Field(default_factory=list)
    images: Optional[List[dict]] = None
    related_questions: Optional[List[str]] = None
    model: str = "sonar"
    provider: Literal["perplexity"] = "perplexity"


class PerplexitySearchParams(BaseModel):
    tool_name: str = Field(
        default="perplexity_search",
        description="Override name shown to the LLM when used as a tool.",
    )
    tool_description: str = Field(
        default="AI-powered search using Perplexity Sonar. Returns a markdown-formatted answer with inline citation references and source URLs.",
        description="Override description shown to the LLM when used as a tool.",
        json_schema_extra={"rows": 3},
    )
    query: str = Field(..., min_length=1)
    model: Literal["sonar", "sonar-pro", "sonar-reasoning", "sonar-reasoning-pro"] = "sonar"
    search_recency_filter: Literal["all", "month", "week", "day", "hour"] = Field(
        default="all",
    )
    return_images: bool = Field(default=False)
    return_related_questions: bool = Field(default=False)

    model_config = {"extra": "ignore"}


class PerplexitySearchNode(ActionNode):
    type = "perplexitySearch"
    display_name = "Perplexity Search"
    subtitle = "AI Search"
    group = ("search", "tool")
    description = "AI-powered search using Perplexity Sonar with citations"
    component_kind = "square"
    tool_name = "perplexity_search"
    tool_description = "Search the web using Perplexity Sonar AI. Returns an AI-generated answer with citations and source URLs."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (PerplexityCredential,)
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = PerplexitySearchParams
    Output = PerplexitySearchOutput

    @Operation("search", cost={"service": "perplexity", "action": "sonar_search", "count": 1})
    async def search(self, ctx: NodeContext, params: PerplexitySearchParams) -> PerplexitySearchOutput:
        body = {
            "model": params.model,
            "messages": [{"role": "user", "content": params.query}],
        }
        if params.search_recency_filter and params.search_recency_filter != "all":
            body["search_recency_filter"] = params.search_recency_filter
        if params.return_images:
            body["return_images"] = True
        if params.return_related_questions:
            body["return_related_questions"] = True

        async with ctx.connection("perplexity") as conn:
            response = await conn.post(
                "https://api.perplexity.ai/chat/completions",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        answer = ""
        if choices:
            answer = (choices[0].get("message") or {}).get("content", "")
        citations = data.get("citations") or []
        results = [PerplexityResult(url=u) for u in citations]
        return PerplexitySearchOutput(
            query=params.query,
            answer=answer,
            citations=citations,
            results=results,
            images=data.get("images"),
            related_questions=data.get("related_questions"),
            model=params.model,
        )
