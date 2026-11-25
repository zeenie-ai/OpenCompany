"""Writing a new employee's setup screen: ``generate_employee_setup``,
``cancel_employee_setup``, and the hire composer's model picker
(``get_hire_models``).

``generate_employee_setup {job, refine?, history?, draft_token, model?}``
asks the AI model picked in the hire composer (``model``, a
``provider::model`` id; none or ``auto`` is Auto, llm.py) for the setup
screen and returns the reply; the client reads it and makes it safe to
render. The thinking level picked there is not sent: a setup is written at
the model's own pace, within its time budget.

While the owner has Generated UI on,
config/genui_catalog.json describes what the screen may contain, and the
server builds every message: the catalogue prompt, the owner's context, and
the "The job:" / "Change the setup:" prefixes. Of the earlier replies it
keeps the latest plus at most two before it, within 40K characters.

While the switch is off, the model fills a fixed form instead and the server
draws the setup card from it (``setup_form.py``). A change sends only the
latest setup back, as the form it describes. The reply the client gets has
the same shape either way.

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
mentions (lower-cased) to an AppRef plus ``can_trigger`` (the app can
start the work), so the client can connect "Gmail" and offer "When
something new arrives in Gmail" without its own copy of the app registry.
Failures: ``{success: false,
error}`` with error one of ``no_ai_provider``, ``timeout``,
``provider_error``, ``unparseable``, ``cancelled``, ``busy``,
``invalid_request``, and ``model_unavailable`` (with a ``detail``) for a
picked model the pickers no longer offer.

``get_hire_models`` answers the picker's rows (services/chat/choice.py
``list_hire_models``): Auto, saying what a hire gets without a pick, each
connected provider's models, and the thinking levels.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.authz.ws_surface import execution_principal
from services.employees.apps import AppSpec, resolve_app
from services.employees.connections import Connections
from services.employees.context import build_setup_prompt_context
from services.employees.llm import USAGE_NODE_ID, LLMChoice, employees_chat, resolve_llm_choice
from services.employees.setup_form import FORM_RETRY_NUDGE, fill_form, form_messages
from services.employees.setup_prompt import RETRY_NUDGE, HistoryTurn, build_messages
from services.employees.setup_reply import app_names, hire_app_names, is_salvageable
from services.employees.team_recipe import preview_from_reply
from services.llm.protocol import LLMResponse, Message, Usage
from services.plugin.base import NodeUserError
from services.plugin.ws import ws_response

logger = get_logger(__name__)

MAX_JOB_CHARS = 2000
MAX_TOKEN_CHARS = 128
#: A picked model's ``provider::model`` id (``UserSettings.hire_model``'s limit).
MAX_MODEL_CHARS = 300
MAX_HISTORY_TURNS = 3
MAX_REPLY_CHARS = 65536
#: Below this many seconds left, a retry would only time out.
MIN_RETRY_SECONDS = 15.0

TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})

ERROR_CODES = frozenset(
    {"no_ai_provider", "timeout", "provider_error", "unparseable", "cancelled", "busy", "invalid_request", "model_unavailable"}
)


class SetupRequestError(ValueError):
    """The request cannot be used (``invalid_request``)."""


@dataclass
class SetupRequest:
    job: str
    token: str
    change: Optional[str] = None
    history: tuple = ()
    #: The model picked in the hire composer; none is Auto.
    model: Optional[str] = None


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
    model = _clean(data.get("model"), MAX_MODEL_CHARS) or None
    return SetupRequest(job=job, token=token, change=change, history=tuple(history), model=model)


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


async def _resolve_apps(connections: Connections, reply: str) -> tuple[Dict[str, Dict[str, Any]], dict[str, AppSpec | None]]:
    """Resolve the apps displayed on the card and the subset selected for the hire."""
    names = app_names(reply)
    if not names:
        return {}, {}
    connected = await connections.connected_app_ids()
    apps = (await connections.apps()).values()
    refs: Dict[str, Dict[str, Any]] = {}
    matched = {}
    for name in names:
        app = resolve_app(name, connected, apps)
        matched[name.lower()] = app
        if app is not None:
            refs[name.lower()] = {**await connections.app_ref(app), "can_trigger": app.trigger is not None}
        else:
            refs[name.lower()] = {
                "app_id": "",
                "provider_id": "",
                "name": name,
                "icon_ref": None,
                "connected": False,
                "supported": False,
                "can_trigger": False,
            }
    trigger_apps = {name for name, ref in refs.items() if ref["can_trigger"]}
    selected = hire_app_names(reply, trigger_apps=trigger_apps)
    return refs, {name: matched.get(name.lower()) for name in selected}


def _with_nudge(messages: List[Message], nudge: str) -> List[Message]:
    """The same conversation, with the retry instruction added to the last
    (user) message so the roles still alternate."""
    last = messages[-1]
    return [*messages[:-1], Message(role=last.role, content=f"{last.content}\n\n{nudge}")]


async def _generate(request: SetupRequest, owner: str) -> Dict[str, Any]:
    from core.container import container
    from services.chat.choice import ChoiceRefused
    from services.usage import record_llm_usage

    database = container.database()
    auth_service = container.auth_service()
    connections = Connections(auth_service, principal=owner)
    try:
        choice: Optional[LLMChoice] = await resolve_llm_choice(database, auth_service, connections, model=request.model)
    except ChoiceRefused as exc:
        return _failure("model_unavailable", request.token, detail=exc.detail)
    if choice is None:
        return _failure("no_ai_provider", request.token)

    context = await build_setup_prompt_context(database, connections)
    if context.generated_ui:
        messages = build_messages(context, request.job, change=request.change, history=request.history)
        nudge = RETRY_NUDGE
    else:
        messages = form_messages(context, request.job, change=request.change, history=request.history)
        nudge = FORM_RETRY_NUDGE
    deadline = time.monotonic() + choice.budget_seconds
    usage = Usage()
    kept: Optional[LLMResponse] = None
    reply = ""
    retried = False
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if attempt == 1:
            if remaining < MIN_RETRY_SECONDS:
                break
            retried = True
        try:
            response = await employees_chat(container.chat_unifier(), auth_service, choice, messages, timeout=remaining, principal=owner)
        except asyncio.TimeoutError:
            if kept is not None:
                break
            logger.warning("Setup timed out", provider=choice.provider, model=choice.model, budget=choice.budget_seconds)
            return _failure("timeout", request.token)
        except NodeUserError as exc:
            if kept is not None:
                break
            logger.warning("Setup model failed", provider=choice.provider, model=choice.model, error=str(exc))
            # detail is the LLM layer's public message ("OpenRouter
            # authentication failed. Check the configured API key."), and
            # requires_user_action says only the owner can fix it (a rejected
            # key, no credits), so Home offers the AI models dialog rather
            # than only Try again.
            return _failure(
                "provider_error",
                request.token,
                detail=str(exc),
                requires_user_action=exc.requires_user_action,
            )
        billed = response.billing_usage or response.usage
        await record_llm_usage(
            provider=choice.provider,
            model=choice.model,
            usage=asdict(billed),
            session_id=owner,
            node_id=USAGE_NODE_ID,
            cost=response.reported_cost,
            database=database,
        )
        usage = usage + billed
        truncated = str(response.finish_reason or "").lower() in TRUNCATED_FINISH_REASONS
        drawn = response.content if context.generated_ui else fill_form(response.content, request.job)
        usable = is_salvageable(drawn)
        if usable:
            kept, reply = response, drawn
        if usable and not truncated:
            break
        if attempt == 0:
            messages = _with_nudge(messages, nudge)

    logger.info(
        "Setup written" if kept is not None else "Setup unreadable",
        provider=choice.provider,
        model=choice.model,
        form=not context.generated_ui,
        retried=retried,
        reply_chars=len(reply),
        finish_reason=kept.finish_reason if kept is not None else None,
    )
    if kept is None:
        return _failure("unparseable", request.token, retried=retried)
    apps, selected_apps = await _resolve_apps(connections, reply)
    return {
        "success": True,
        "draft_token": request.token,
        "reply": reply,
        "provider": choice.provider,
        "model": choice.model,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens},
        "retried": retried,
        "finish_reason": kept.finish_reason,
        "apps": apps,
        "team": preview_from_reply(request.job, reply, selected_apps=selected_apps),
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


@ws_response
async def handle_get_hire_models(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """The hire composer's model picker: ``{auto, models, efforts}``, each row
    as ``get_chat_models`` sends it. The owner's choice is in their settings
    (``hire_model`` / ``hire_effort``)."""
    from core.container import container
    from services.chat.choice import list_hire_models

    database = container.database()
    auth_service = container.auth_service()
    owner = execution_principal(data, websocket)
    auto = await resolve_llm_choice(database, auth_service, Connections(auth_service, principal=owner))
    picker = await list_hire_models(
        database, auth_service, principal=owner, auto=(auto.provider, auto.model) if auto is not None else None
    )
    return {"success": True, **picker}


WS_HANDLERS: Dict[str, Any] = {
    "generate_employee_setup": handle_generate_employee_setup,
    "cancel_employee_setup": handle_cancel_employee_setup,
    "get_hire_models": handle_get_hire_models,
}


__all__ = [
    "ERROR_CODES",
    "SetupRequest",
    "WS_HANDLERS",
    "handle_cancel_employee_setup",
    "handle_generate_employee_setup",
    "handle_get_hire_models",
    "parse_setup_request",
    "reset_for_tests",
]
