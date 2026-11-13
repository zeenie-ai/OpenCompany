"""treg: find a paid data or generation tool, read its price, call it, check the balance.

The node runs the pinned treg CLI (``_install.py``) as ``treg --json ...``
with the environment ``_service.treg_env`` builds, and shapes what it
prints. Every value reaches the CLI as its own argument, never through a
shell. Operations:

- ``search``: ``treg catalog search`` finds tools for a job, with prices.
- ``describe``: ``treg catalog get`` lists one tool's parameters and where
  each goes, its price and a known-good example request.
- ``call``: looks the tool up (free; an unknown tool stops there), places
  the parameters where the catalog declares them (``_request.py``) and
  runs ``treg call`` with the node's price ceiling. treg refuses a dearer
  call before charging. What it charged is recorded in
  ``api_usage_metrics`` before the answer is saved.
- ``balance``: ``treg balance`` reads the team's prepaid balance.

Search and describe read treg's open catalog, so they work before the
owner signs in; call and balance need the sign-in (Credentials -> treg).

A call spends money and some catalog tools post or change things through
accounts connected in treg, so ``call`` is ``money`` in the approval class
table and is refused while the owner asks first (the Stripe pattern: an
approved call would run later, and its paid answer would never reach the
employee). The ceiling is the owner's: ``max_cost_usd`` is a
server-controlled field, so a model cannot raise it. A call is never
repeated: a broken-off one may already have been charged.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.logging import get_logger
from services.media import write_media
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue, coerce_blank_params
from services.plugin.approval import ApprovalSpec

from ._credentials import TregCredential
from ._install import get_installer
from ._request import build_request, check_tool_id, parameters_of
from ._result import RESULT_SUBDIR, deliver, preview_of
from ._service import run_treg

logger = get_logger(__name__)

_SEARCH = {"displayOptions": {"show": {"operation": ["search"]}}}
_TOOL = {"displayOptions": {"show": {"operation": ["describe", "call"]}}}
_CALL = {"displayOptions": {"show": {"operation": ["call"]}}}

#: How long an operation waits for a first install of the CLI.
_INSTALL_WAIT_SECONDS = 300.0
#: A catalog read or the balance: one request to treg.to.
_READ_TIMEOUT_SECONDS = 60.0
#: A call: the CLI waits up to 190 s for treg's answer.
_CALL_TIMEOUT_SECONDS = 200.0
#: The most one call may cost unless the owner sets another ceiling.
DEFAULT_MAX_COST_USD = 0.10
#: treg's per-call ceiling: a call that would reserve more is refused, uncharged.
_CEILING_HEADER = "X-Treg-Route-Max-Cost"
#: The parts of a catalog price an agent compares tools by.
_PRICE_KEYS = ("type", "usd", "usd_min", "unit", "varies")
_SIGN_IN_HINT = "If treg says the sign-in is missing or rejected, sign in under Credentials -> treg."


class TregActionParams(BaseModel):
    operation: Literal["search", "describe", "call", "balance"] = "search"

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
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="The tool's parameters by name, as describe lists them, as a JSON object. The node puts each one where the tool takes it.",
        json_schema_extra={"editor": "json", "rows": 6, **_CALL},
    )
    body: Optional[Any] = Field(
        default=None,
        description="The whole request body as JSON, only when params cannot express it (a list of several items, or a body the tool wants as is). Leave empty otherwise.",
        json_schema_extra={"editor": "json", "rows": 4, **_CALL},
    )
    max_cost_usd: float = Field(
        default=DEFAULT_MAX_COST_USD,
        ge=0,
        description="The most one call may cost, in USD; treg refuses a dearer call before charging. Set by the owner on the node: a value in a tool call is ignored.",
        json_schema_extra=_CALL,
    )

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, values: Any) -> Any:
        # The panel stores "" for a cleared field and renders params as text;
        # a model may send the object as a JSON string.
        return coerce_blank_params(cls, values, object_fields=("params",))

    @field_validator("body", mode="before")
    @classmethod
    def _body(cls, value: Any) -> Any:
        """An empty body is no body; a body given as text is JSON."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ValueError(f"body must be JSON: {exc}") from exc


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
    # call: the answer is ``data`` when small, else ``file`` + ``preview`` + ``note``
    data: Optional[Any] = None
    file: Optional[Dict[str, Any]] = None
    preview: Optional[Any] = None
    http_status: Optional[int] = None
    cost_usd: Optional[float] = None
    reserved_usd: Optional[float] = None
    call_id: Optional[str] = None
    routed_tool: Optional[str] = None
    upstream_failed: Optional[str] = None
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


def _excerpt(answer: Any, limit: int = 500) -> str:
    text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _at(value: Any, path: Any) -> Any:
    """The value at a dotted path such as ``tasks.0.id``; None when absent."""
    if not isinstance(path, str) or not path:
        return None
    for part in path.split("."):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _task(endpoint: Dict[str, Any], answer: Any) -> Optional[Dict[str, Any]]:
    """How to check an asynchronous task (a generation), from the tool's
    catalog descriptor: its id and the call that reads its state."""
    descriptor = endpoint.get("async") if isinstance(endpoint.get("async"), dict) else {}
    task_id = _at(answer, descriptor.get("id_from"))
    poll = descriptor.get("poll") if isinstance(descriptor.get("poll"), dict) else {}
    param = poll.get("param") if isinstance(poll.get("param"), dict) else {}
    check = None
    if poll.get("endpoint") and param.get("name") and task_id is not None:
        check = {"operation": "call", "tool_id": poll["endpoint"], "params": {param["name"]: task_id}}
    status = descriptor.get("status") if isinstance(descriptor.get("status"), dict) else {}
    result = descriptor.get("result") if isinstance(descriptor.get("result"), dict) else {}
    fields = {
        "task_id": task_id,
        "check": check,
        "status_path": status.get("path"),
        "done_when": status.get("success"),
        "failed_when": status.get("failure"),
        "result_path": result.get("path"),
        "interval_seconds": descriptor.get("interval"),
        "note": descriptor.get("ttl_note"),
    }
    return {key: value for key, value in fields.items() if value not in (None, [], {})}


async def _record_cost(ctx: NodeContext, tool_id: str, micro: int, operation: str) -> None:
    """One ``api_usage_metrics`` row for what treg charged (or reserved, for a
    task). Never raises: the call is already paid for."""
    try:
        from services.plugin.deps import get_database

        raw = ctx.raw if isinstance(ctx.raw, dict) else {}
        await get_database().save_api_usage_metric(
            {
                "session_id": ctx.session_id or raw.get("session_id", "default"),
                "node_id": ctx.node_id,
                "workflow_id": ctx.workflow_id or raw.get("workflow_id"),
                "service": "treg",
                "operation": operation,
                "endpoint": tool_id[:100],
                "resource_count": 1,
                "cost": micro / 1_000_000,
            }
        )
    except Exception as exc:  # noqa: BLE001 - recording must never fail a paid call
        logger.warning("treg usage recording failed", tool_id=tool_id, error=str(exc))


def _call_failed(tool_id: str, status: Any, answer: Any, cost_usd: Optional[float]) -> NodeUserError:
    """A ``NodeUserError`` for a call treg or the provider refused, in their words."""
    detail = answer.get("detail") if isinstance(answer, dict) else None
    code = detail.get("error") if isinstance(detail, dict) else None
    charged = f" treg charged ${cost_usd:g} for it." if cost_usd else ""
    if code == "insufficient_balance":
        need, have = _usd(detail.get("estimated_cost_micro")), _usd(detail.get("balance_micro"))
        return NodeUserError(
            f"The treg balance is too low for {tool_id}: it needs about ${need} and the balance is ${have}. "
            "Add funds to the treg team, then try again.",
            requires_user_action=True,
        )
    if code == "route_max_cost":
        need, ceiling = _usd(detail.get("estimated_cost_micro")), _usd(detail.get("max_cost_micro"))
        return NodeUserError(
            f"{tool_id} would cost about ${need}, more than this node's ceiling of ${ceiling} per call "
            "(max_cost_usd, set by the owner). Nothing was charged. Choose a cheaper tool or ask for fewer results."
        )
    words = _treg_words(answer) or _excerpt(answer)
    return NodeUserError(
        f"{tool_id} answered HTTP {status}: {words}.{charged}",
        hint=_SIGN_IN_HINT if status == 401 else None,
        requires_user_action=status in (401, 402, 403),
    )


def _delivered(ctx: NodeContext, tool_id: str, answer: Any) -> Dict[str, Any]:
    """The answer as output fields: a binary file saved to the workspace,
    anything else whole when small, else saved as JSON with a preview."""
    if isinstance(answer, dict) and set(answer) == {"base64", "content_type"}:
        content_type = str(answer["content_type"])
        extension = (mimetypes.guess_extension(content_type) or ".bin").lstrip(".")
        ref = write_media(
            base64.b64decode(answer["base64"]),
            ctx=ctx,
            stem=tool_id,
            ext=extension,
            mime_type=content_type,
            subdir=RESULT_SUBDIR,
        )
        return {"file": ref.model_dump(mode="json"), "note": f"The answer is a {content_type} file, saved in the workspace at {ref.path}."}
    return deliver(ctx, tool_id, answer)


class TregActionNode(ActionNode):
    type = "tregAction"
    display_name = "treg"
    subtitle = "Tool Catalog"
    group = ("api", "tool")
    description = "Find a paid data or generation tool in treg's catalog, read its parameters and price, call it, and check the team balance"
    component_kind = "square"
    tool_name = "treg_action"
    tool_description = (
        "Find and call paid data and generation tools through treg (treg.to): one sign-in reaches thousands of tools "
        "for search results, SEO data, social profiles and posts, people and company data, web pages, and image, video "
        "and voice generation. Flow: (1) operation=search with query (the job in plain words) lists tools with their "
        "price; (2) operation=describe with tool_id lists that tool's parameters, where each goes, its price and an "
        "example request; (3) operation=call with tool_id and params (the tool's parameters by name, as describe lists "
        "them) runs it; use body only for a whole JSON body params cannot express, such as a list of several items. "
        "Each call is billed to the team's treg balance: cost_usd says what it cost, and is absent when treg did not "
        "bill it. The owner caps what one call may cost; a dearer call is refused before anything is charged. A large "
        "answer is saved to the workspace: file.path is where, and preview shows its shape; read the fields you need "
        "from that file instead of calling again. A generation tool answers with task: run task.check (a call) until "
        "its status_path shows a done_when value. operation=balance reads the team balance in USD. Search and describe "
        "are free."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    credentials = (TregCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    # usable_as_tool hides both main handles unless declared. This node stays
    # wirable on the canvas through its declared ``handles``.
    hide_input_handle = False
    hide_output_handle = False
    # The owner's ceiling: a model's tool call cannot raise it.
    server_controlled_fields = frozenset({"max_cost_usd"})
    # A call spends money and cannot wait for a card (its paid answer would
    # never reach the employee): refused while the owner asks first.
    approval = ApprovalSpec(
        channel="treg",
        action="Call a paid treg tool",
        operations=frozenset({"call"}),
        refuse_while_asking=True,
        details=(("Tool", "tool_id"), ("Most it may cost (USD)", "max_cost_usd")),
        outcome_labels=("Called", "Not called"),
    )

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

    @Operation("call")
    async def call(self, ctx: NodeContext, params: TregActionParams) -> Any:
        tool_id = check_tool_id(params.tool_id)
        cli = await self._cli()
        # The catalog entry: the method, where each parameter goes, how a
        # task is checked. Free, and an unknown tool stops here.
        endpoint = _answer(await run_treg(cli, ["catalog", "get", tool_id], timeout=_READ_TIMEOUT_SECONDS), f"lookup of {tool_id}").get(
            "endpoint"
        )
        if not isinstance(endpoint, dict):
            raise NodeUserError(f"{tool_id} names a job that several tools do, not one tool. Pick one with operation=describe.")
        request = build_request(endpoint, params.params, params.body)
        argv = ["call", tool_id, f"--method={request.method}"]
        argv += [f"--query={name}={value}" for name, value in request.query]
        argv.append(f"--header={_CEILING_HEADER}: {params.max_cost_usd:.6f}")
        if request.body is not None:
            argv += ["--data=@-", f"--content-type={request.content_type}"]
        result = await run_treg(cli, argv, timeout=_CALL_TIMEOUT_SECONDS, body=request.body)

        envelope = result.get("result") if result.get("success") else _parsed(result.get("stdout"))
        meta = envelope.get("_treg") if isinstance(envelope, dict) else None
        if not isinstance(meta, dict):
            # No answer: the CLI could not reach treg, or the call ran out of time.
            words = _last_line(result.get("stderr")) or str(result.get("error") or "no answer")
            raise NodeUserError(
                f"The call to {tool_id} did not come back ({words}). It may have gone through and been charged: "
                "check operation=balance before calling it again."
            )
        answer = envelope.get("result")
        status = meta.get("http_status")
        reserved = "reserved_micro" in meta
        micro = meta.get("reserved_micro" if reserved else "charged_micro")
        if isinstance(micro, int) and micro > 0:
            # Recorded before the answer is saved: the call is already paid for.
            await _record_cost(ctx, tool_id, micro, "task" if reserved else "call")
        cost_usd = None if reserved else _usd(micro)
        if not isinstance(status, int) or status >= 400:
            raise _call_failed(tool_id, status, answer, cost_usd)
        try:
            delivered = _delivered(ctx, tool_id, answer)
        except NodeUserError as exc:
            raise NodeUserError(f"{exc} The call itself succeeded (call id {meta.get('call_id')}).") from exc
        return self._shape(
            "call",
            tool_id=tool_id,
            http_status=status,
            cost_usd=cost_usd,
            reserved_usd=_usd(micro) if reserved else None,
            call_id=meta.get("call_id"),
            routed_tool=meta.get("routed_tool"),
            upstream_failed=meta.get("upstream_failed"),
            task=_task(endpoint, answer) if meta.get("async") else None,
            **delivered,
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


__all__ = ["DEFAULT_MAX_COST_USD", "TregActionNode", "TregActionOutput", "TregActionParams"]
