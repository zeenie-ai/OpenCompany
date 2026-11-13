"""treg: find a paid data or generation tool, read its price, and check the balance.

The node runs the pinned treg CLI (``_install.py``) as ``treg --json ...``
with the environment ``_service.treg_env`` builds, and shapes what it
prints. Every value reaches the CLI as its own argument, never through a
shell. Operations:

- ``search``: ``treg catalog search`` finds tools for a job, with prices.
- ``describe``: ``treg catalog get`` lists one tool's parameters and where
  each goes, its price and a known-good example request.
- ``balance``: ``treg balance`` reads the team's prepaid balance.

Search and describe read treg's open catalog, so they work before the
owner signs in; balance needs the sign-in (Credentials -> treg).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue, coerce_blank_params

from ._credentials import TregCredential
from ._install import get_installer
from ._request import check_tool_id, parameters_of
from ._result import preview_of
from ._service import run_treg

_SEARCH = {"displayOptions": {"show": {"operation": ["search"]}}}
_TOOL = {"displayOptions": {"show": {"operation": ["describe"]}}}

#: How long an operation waits for a first install of the CLI.
_INSTALL_WAIT_SECONDS = 300.0
#: A catalog read or the balance: one request to treg.to.
_READ_TIMEOUT_SECONDS = 60.0
#: The parts of a catalog price an agent compares tools by.
_PRICE_KEYS = ("type", "usd", "usd_min", "unit", "varies")
_SIGN_IN_HINT = "If treg says the sign-in is missing or rejected, sign in under Credentials -> treg."


class TregActionParams(BaseModel):
    operation: Literal["search", "describe", "balance"] = "search"

    query: str = Field(
        default="",
        description="The job a tool should do, in plain words (e.g. 'instagram reels by keyword').",
        json_schema_extra={"placeholder": "google search results", **_SEARCH},
    )
    limit: int = Field(default=10, ge=1, le=100, description="The most tools to list.", json_schema_extra=_SEARCH)
    tool_id: str = Field(
        default="",
        description="A tool id from operation=search (e.g. 'serper.web.search').",
        json_schema_extra={"placeholder": "serper.web.search", **_TOOL},
    )

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, values: Any) -> Any:
        # The panel stores "" for a cleared number.
        return coerce_blank_params(cls, values)


class TregActionOutput(BaseModel):
    operation: Optional[str] = None
    # search
    query: Optional[str] = None
    count: Optional[int] = None
    total: Optional[int] = None
    verdict: Optional[str] = None
    reason: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    # describe
    tool_id: Optional[str] = None
    name: Optional[str] = None
    summary: Optional[str] = None
    note: Optional[str] = None
    provider: Optional[str] = None
    method: Optional[str] = None
    price: Optional[Dict[str, Any]] = None
    parameters: Optional[List[Dict[str, Any]]] = None
    parameters_note: Optional[str] = None
    example_request: Optional[Any] = None
    example_response: Optional[Any] = None
    task: Optional[Any] = None
    status: Optional[str] = None
    superseded_by: Optional[str] = None
    docs_url: Optional[str] = None
    job: Optional[Any] = None
    # balance
    balance_usd: Optional[float] = None
    holds: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra="allow")


def _price(cost: Any) -> Optional[Dict[str, Any]]:
    """``type`` (per_call, per_result, per_success, free), ``usd`` per unit,
    ``unit``, ``usd_min`` for a price table, ``varies`` for a tool treg routes."""
    if not isinstance(cost, dict):
        return None
    return {key: cost[key] for key in _PRICE_KEYS if cost.get(key) is not None} or None


def _usd(micro: Any) -> Optional[float]:
    return round(micro / 1_000_000, 6) if isinstance(micro, (int, float)) else None


def _parsed(text: Any) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _treg_words(body: Any) -> str:
    """treg's own words in an error body: ``detail`` as text, or its message
    (else its error code), and close ids when it suggests some."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if not isinstance(detail, dict):
        return ""
    words = str(detail.get("message") or detail.get("error") or "")
    close = detail.get("did_you_mean")
    if isinstance(close, list) and close:
        words += f" Did you mean: {', '.join(str(item) for item in close[:5])}?"
    return words.strip()


def _last_line(text: Any) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _answer(result: Dict[str, Any], what: str, *, hint: Optional[str] = None) -> Dict[str, Any]:
    """The JSON object a read printed, else a ``NodeUserError`` in treg's words."""
    if result.get("success"):
        data = result.get("result")
        if not isinstance(data, dict):
            raise NodeUserError(f"treg {what} printed no JSON object.")
        return data
    words = _treg_words(_parsed(result.get("stdout"))) or _last_line(result.get("stderr")) or str(result.get("error") or "no answer")
    raise NodeUserError(f"treg {what} failed: {words}", hint=hint)


def _tool_row(row: Dict[str, Any]) -> Dict[str, Any]:
    fields = {
        "id": row.get("id"),
        "name": row.get("name") or None,
        "summary": row.get("summary") or None,
        "provider": row.get("provider_display") or row.get("provider"),
        "price": _price(row.get("cost")),
        "task": True if row.get("async") else None,
        "routed": True if row.get("kind") == "routed" else None,
    }
    return {key: value for key, value in fields.items() if value is not None}


class TregActionNode(ActionNode):
    type = "tregAction"
    display_name = "treg"
    subtitle = "Tool Catalog"
    group = ("api", "tool")
    description = "Find a paid data or generation tool in treg's catalog, read its parameters and price, and check the team balance"
    component_kind = "square"
    tool_name = "treg_action"
    tool_description = (
        "Find paid data and generation tools through treg (treg.to): one sign-in reaches thousands of tools for "
        "search results, SEO data, social profiles and posts, people and company data, web pages, and image, video "
        "and voice generation. operation=search with query (the job in plain words) lists tools with their price; "
        "operation=describe with tool_id lists that tool's parameters and where each goes, its price and an example "
        "request; operation=balance reads the team's prepaid balance in USD. Search and describe are free."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    credentials = (TregCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    # usable_as_tool hides both main handles unless declared. This node stays
    # wirable on the canvas through its declared ``handles``.
    hide_input_handle = False
    hide_output_handle = False

    Params = TregActionParams
    Output = TregActionOutput

    @staticmethod
    def _shape(operation: str, **fields: Any) -> Dict[str, Any]:
        """``{"operation": ...}`` plus every field that is not ``None``."""
        shaped: Dict[str, Any] = {"operation": operation}
        shaped.update({key: value for key, value in fields.items() if value is not None})
        return shaped

    @staticmethod
    async def _cli() -> Path:
        return await get_installer().ensure(wait=_INSTALL_WAIT_SECONDS)

    @Operation("search")
    async def search(self, ctx: NodeContext, params: TregActionParams) -> Any:
        query = params.query.strip()
        if not query:
            raise NodeUserError("query is required: the job a tool should do, such as 'google search results'.")
        if query.startswith("-"):
            raise NodeUserError("query must start with a word, not '-'.")
        result = await run_treg(await self._cli(), ["catalog", "search", query, f"--limit={params.limit}"], timeout=_READ_TIMEOUT_SECONDS)
        data = _answer(result, "search")
        tools = [_tool_row(row) for row in data.get("results") or [] if isinstance(row, dict)]
        return self._shape(
            "search",
            query=query,
            count=len(tools),
            total=data.get("total"),
            verdict=data.get("verdict"),
            reason=data.get("reason"),
            tools=tools,
        )

    @Operation("describe")
    async def describe(self, ctx: NodeContext, params: TregActionParams) -> Any:
        tool_id = check_tool_id(params.tool_id)
        result = await run_treg(await self._cli(), ["catalog", "get", tool_id], timeout=_READ_TIMEOUT_SECONDS)
        data = _answer(result, f"describe of {tool_id}")
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, dict):
            # A job id (what several providers do), not one tool: treg lists the providers.
            return self._shape("describe", tool_id=tool_id, job=data)
        inp = endpoint.get("input")
        example = data.get("example_response")
        return self._shape(
            "describe",
            tool_id=endpoint.get("id") or tool_id,
            name=endpoint.get("name") or None,
            summary=endpoint.get("summary") or None,
            note=endpoint.get("note") or None,
            provider=endpoint.get("provider_display") or endpoint.get("provider"),
            method=endpoint.get("method"),
            price=_price(endpoint.get("cost")),
            parameters=parameters_of(inp),
            parameters_note=(inp.get("note") or None) if isinstance(inp, dict) else None,
            example_request=endpoint.get("test_request") or None,
            example_response=preview_of(example) if example is not None else None,
            task=True if endpoint.get("async") else None,
            status=endpoint.get("status") or None,
            superseded_by=endpoint.get("superseded_by") or None,
            docs_url=endpoint.get("docs_url") or None,
        )

    @Operation("balance")
    async def balance(self, ctx: NodeContext, params: TregActionParams) -> Any:
        result = await run_treg(await self._cli(), ["balance", "--limit=1"], timeout=_READ_TIMEOUT_SECONDS)
        data = _answer(result, "balance", hint=_SIGN_IN_HINT)
        holds = [
            {
                "tool_id": hold.get("endpoint_id"),
                "reserved_usd": _usd(hold.get("amount_micro")),
                "call_id": hold.get("call_id"),
            }
            for hold in data.get("holds") or []
            if isinstance(hold, dict)
        ]
        return self._shape("balance", balance_usd=_usd(data.get("balance_micro")), holds=holds)


__all__ = ["TregActionNode", "TregActionOutput", "TregActionParams"]
