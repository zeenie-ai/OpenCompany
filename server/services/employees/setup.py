"""Writing a new employee's setup screen: ``generate_employee_setup`` and
``cancel_employee_setup``.

``generate_employee_setup {job, refine?, history?, draft_token}`` asks the
owner's AI model for the setup screen (config/genui_catalog.json describes
what it may contain) and returns the raw reply; the client reads it and
makes it safe to render. The server builds every message: the catalogue
prompt, the owner's context, and the "The job:" / "Change the setup:"
prefixes. Of the earlier replies it keeps the latest plus at most two
before it, within 40K characters.

Time: 90 s for a cloud model, 240 s for a local one, for the whole
request including its one retry. The client waits up to 300 s.

One retry, when the reply was cut off (the model hit its token limit) or
the client would find nothing to render in it. Every call's tokens are
booked, retry included.

One request per owner: a newer ``draft_token`` cancels the older call (the
owner has moved on), and ``cancel_employee_setup`` cancels one outright.

Neither the job text nor the model's reply is ever logged.

Response: ``{success, draft_token, reply, provider, model, usage, retried,
finish_reason, apps}`` where ``apps`` maps each app name the reply
mentions (lower-cased) to an AppRef, so the client can connect "Gmail"
without its own copy of the app registry. Failures: ``{success: false,
error}`` with error one of ``no_ai_provider``, ``timeout``,
``provider_error``, ``unparseable``, ``cancelled``, ``busy``,
``invalid_request``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.authz.ws_surface import execution_principal
from services.employees.apps import resolve_app
from services.employees.connections import Connections
from services.employees.context import build_setup_prompt_context
from services.employees.llm import LLMChoice, employees_chat, record_llm_usage, resolve_llm_choice
from services.employees.setup_prompt import RETRY_NUDGE, HistoryTurn, build_messages
from services.employees.setup_reply import app_names, is_salvageable
from services.llm.protocol import LLMResponse, Message, Usage
from services.plugin.base import NodeUserError
from services.plugin.ws import ws_response

logger = get_logger(__name__)

MAX_JOB_CHARS = 2000
MAX_TOKEN_CHARS = 128
MAX_HISTORY_TURNS = 3
MAX_REPLY_CHARS = 65536
#: Below this many seconds left, a retry would only time out.
MIN_RETRY_SECONDS = 15.0

TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})

ERROR_CODES = frozenset(
    {"no_ai_provider", "timeout", "provider_error", "unparseable", "cancelled", "busy", "invalid_request"}
)


class SetupRequestError(ValueError):
    """The request cannot be used (``invalid_request``)."""


@dataclass
class SetupRequest:
    job: str
    token: str
    change: Optional[str] = None
    history: tuple = ()


def _clean(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def parse_setup_request(data: Dict[str, Any]) -> SetupRequest:
    job = _clean(data.get("job"), MAX_JOB_CHARS)
    token = _clean(data.get("draft_token"), MAX_TOKEN_CHARS)
    if not job or not token:
        raise SetupRequestError("job and draft_token are required")
    change = _clean(data.get("refine"), MAX_JOB_CHARS) or None
    history: List[HistoryTurn] = []
    raw_history = data.get("history")
    if change and isinstance(raw_history, list):
        for item in raw_history[-MAX_HISTORY_TURNS:]:
            if not isinstance(item, dict) or not isinstance(item.get("reply"), str):
                continue
            history.append(
                HistoryTurn(
                    reply=item["reply"][:MAX_REPLY_CHARS],
                    change=_clean(item.get("change"), MAX_JOB_CHARS) or None,
                )
            )
    return SetupRequest(job=job, token=token, change=change, history=tuple(history))


def _failure(code: str, token: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": code, "draft_token": token, **extra}


# ----- one request per owner -----


@dataclass(eq=False)
class _ActiveSetup:
    token: str
    task: "asyncio.Task[Dict[str, Any]]"


_active: Dict[str, _ActiveSetup] = {}


def reset_for_tests() -> None:
    for entry in _active.values():
        entry.task.cancel()
    _active.clear()


async def _resolve_apps(connections: Connections, reply: str) -> Dict[str, Dict[str, Any]]:
    """AppRefs for the app names the reply mentions."""
    names = app_names(reply)
    if not names:
        return {}
    connected = await connections.connected_app_ids()
    refs: Dict[str, Dict[str, Any]] = {}
    for name in names:
        app = resolve_app(name, connected)
        if app is not None:
            refs[name.lower()] = await connections.app_ref(app)
        else:
            refs[name.lower()] = {
                "app_id": "",
                "provider_id": "",
                "name": name,
                "icon_ref": None,
                "connected": False,
                "supported": False,
            }
    return refs


def _with_nudge(messages: List[Message]) -> List[Message]:
    """The same conversation, with the retry instruction added to the last
    (user) message so the roles still alternate."""
    last = messages[-1]
    return [*messages[:-1], Message(role=last.role, content=f"{last.content}\n\n{RETRY_NUDGE}")]


async def _generate(request: SetupRequest, owner: str) -> Dict[str, Any]:
    from core.container import container

    database = container.database()
    auth_service = container.auth_service()
    connections = Connections(auth_service)
    choice: Optional[LLMChoice] = await resolve_llm_choice(database, auth_service, connections)
    if choice is None:
        return _failure("no_ai_provider", request.token)

    context = await build_setup_prompt_context(database, connections)
    messages = build_messages(context, request.job, change=request.change, history=request.history)
    deadline = time.monotonic() + choice.budget_seconds
    usage = Usage()
    kept: Optional[LLMResponse] = None
    retried = False
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if attempt == 1 and remaining < MIN_RETRY_SECONDS:
            break
        try:
            response = await employees_chat(container.chat_unifier(), auth_service, choice, messages, timeout=remaining)
        except asyncio.TimeoutError:
            if kept is not None:
                break
            logger.warning("Setup timed out", provider=choice.provider, model=choice.model, budget=choice.budget_seconds)
            return _failure("timeout", request.token)
        except NodeUserError as exc:
            if kept is not None:
                break
            logger.warning("Setup model failed", provider=choice.provider, model=choice.model, error=str(exc))
            return _failure("provider_error", request.token, detail=str(exc))
        await record_llm_usage(database, choice, response, session_id=owner)
        usage = usage + (response.billing_usage or response.usage)
        truncated = str(response.finish_reason or "").lower() in TRUNCATED_FINISH_REASONS
        usable = is_salvageable(response.content)
        if usable:
            kept = response
        if usable and not truncated:
            break
        if attempt == 0:
            retried = True
            messages = _with_nudge(messages)

    logger.info(
        "Setup written" if kept is not None else "Setup unreadable",
        provider=choice.provider,
        model=choice.model,
        retried=retried,
        reply_chars=len(kept.content) if kept is not None else 0,
        finish_reason=kept.finish_reason if kept is not None else None,
    )
    if kept is None:
        return _failure("unparseable", request.token, retried=retried)
    return {
        "success": True,
        "draft_token": request.token,
        "reply": kept.content,
        "provider": choice.provider,
        "model": choice.model,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens},
        "retried": retried,
        "finish_reason": kept.finish_reason,
        "apps": await _resolve_apps(connections, kept.content),
    }


@ws_response
async def handle_generate_employee_setup(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    try:
        request = parse_setup_request(data)
    except SetupRequestError:
        return _failure("invalid_request", _clean(data.get("draft_token"), MAX_TOKEN_CHARS))
    owner = execution_principal(data, websocket)

    current = _active.get(owner)
    if current is not None and not current.task.done():
        if current.token == request.token:
            return _failure("busy", request.token)
        current.task.cancel()  # the owner has moved on to a newer request

    task = asyncio.ensure_future(_generate(request, owner))
    entry = _ActiveSetup(token=request.token, task=task)
    _active[owner] = entry
    try:
        return await task
    except asyncio.CancelledError:
        this = asyncio.current_task()
        if this is not None and this.cancelling():
            task.cancel()  # the socket went away: stop the model call too
            raise
        return _failure("cancelled", request.token)
    finally:
        if _active.get(owner) is entry:
            del _active[owner]


@ws_response
async def handle_cancel_employee_setup(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    token = _clean(data.get("draft_token"), MAX_TOKEN_CHARS)
    owner = execution_principal(data, websocket)
    current = _active.get(owner)
    if token and current is not None and current.token == token and not current.task.done():
        current.task.cancel()
        return {"success": True, "cancelled": True, "draft_token": token}
    return {"success": True, "cancelled": False, "draft_token": token}


WS_HANDLERS: Dict[str, Any] = {
    "generate_employee_setup": handle_generate_employee_setup,
    "cancel_employee_setup": handle_cancel_employee_setup,
}


__all__ = [
    "ERROR_CODES",
    "SetupRequest",
    "WS_HANDLERS",
    "handle_cancel_employee_setup",
    "handle_generate_employee_setup",
    "parse_setup_request",
    "reset_for_tests",
]
