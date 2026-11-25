"""The model and thinking the owner picks in Home's two model pickers.

Home's employee chat has a model picker: Auto, or a model of a provider the
owner can run on now, and how hard the model thinks: Balanced (no effort
chosen: the model's ``default_effort`` in llm_defaults.json, else its own
default), Quick (effort ``low``) or Thorough (``high``). The picker offers
what the editor's Set Global Model offers, by provider: each connected
provider's ``offered`` models (services/llm/connected.py), so a provider such
as OpenRouter, a local server or a named endpoint lists its models too. The
choice is saved with the owner's settings (``UserSettings.chat_model`` /
``chat_effort``) and sent with every message, edit and retry as
``options.model`` / ``options.effort``. Admission resolves it here and keeps
the result on the run (``ChatRun.options``), and ``prepare_agent_payload``
applies it to the agent that answers the run only.

- Auto is the owner's default model (Set Global Model), else the OpenCompany
  Gateway's default while the gateway serves it (``gateway_default``). With
  neither it changes nothing: the employee answers on its own model.
- A model whose provider can't run is refused, never swapped for another,
  and so is a model the picker no longer offers.

The hire composer has the same picker (``list_hire_models``, saved as
``hire_model`` / ``hire_effort``): the model it picks writes a new
employee's setup and is the one the employee runs on, and its level becomes
each new agent's Effort (services/employees/llm.py, builder.py). Its Auto is
what a new hire gets without a pick (``resolve_llm_choice``).

The pickers' words come from ``chat_defaults.json`` (``choice``); a model is
named by its id, under its provider's name.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.chat.config import choice_setting
from services.llm.config import agent_model, provider_display_name, split_chat_model, supports_effort

#: The efforts a message may carry; Balanced sends none.
EFFORTS = ("low", "high")
AUTO = "auto"
SETTINGS_USER_ID = "default"
#: Where each picker's choice is saved: (model, effort) settings keys.
CHOICE_SETTINGS = (("chat_model", "chat_effort"), ("hire_model", "hire_effort"))


class ChoiceRefused(Exception):
    """The model can't answer: its provider isn't connected, or the picker no
    longer offers it. ``detail`` says so in the owner's terms."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


async def _providers(database: Any, auth: Any, principal: Optional[str]) -> List[Dict[str, Any]]:
    """The providers the owner can run on now, with their models."""
    from services.llm.connected import connected_providers

    return await connected_providers(auth, database, principal=principal)


async def _settings(database: Any) -> Mapping[str, Any]:
    return await database.get_user_settings(SETTINGS_USER_ID) or {}


def _default_of(settings: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    """The owner's default model (Set Global Model), when one is set."""
    provider = str(settings.get("default_llm_provider") or "")
    model = str(settings.get("default_llm_model") or "")
    return (provider, model) if provider and model else None


async def _default(settings: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    """Auto's model: the owner's default, else the OpenCompany Gateway's."""
    from services.gateway.routing import gateway_default

    return _default_of(settings) or await gateway_default()


def _unavailable(key: str, provider: str, model: str) -> str:
    return str(choice_setting(key)).format(model=model, provider=provider_display_name(provider))


def _refusal(model_id: str, providers: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """Why ``model_id`` (``provider::model``) can't be picked now, or ``None``:
    its provider can't run, or the picker doesn't offer it."""
    provider, model = split_chat_model(model_id)
    if not provider or not model:
        return str(choice_setting("not_offered"))
    entry = next((entry for entry in providers if entry["provider"] == provider), None)
    if entry is None:
        return _unavailable("unavailable", provider, model)
    if model not in entry["offered"]:
        return str(choice_setting("not_offered"))
    return None


async def run_options(database: Any, auth: Any, raw: Any, *, principal: Optional[str]) -> Dict[str, Any]:
    """What a message's run keeps (``ChatRun.options``): ``web`` as sent, the
    model resolved to ``provider::model`` (Auto read now; nothing when Auto
    has no default), and ``effort``. A message without ``model`` changes no
    model. Raises :class:`ChoiceRefused` for a model that can't answer and
    ``ValueError`` for a malformed choice."""
    options: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return options
    if isinstance(raw.get("web"), bool):
        options["web"] = raw["web"]
    effort = raw.get("effort")
    if effort is not None:
        if effort not in EFFORTS:
            raise ValueError(str(choice_setting("bad_effort")))
        options["effort"] = effort
    model = raw.get("model")
    if model is None:
        return options
    if not isinstance(model, str):
        raise ValueError("model must be text")
    providers = await _providers(database, auth, principal)
    if model == AUTO:
        default = await _default(await _settings(database))
        if default is None:
            return options
        provider, name = default
        if not any(entry["provider"] == provider for entry in providers):
            raise ChoiceRefused(_unavailable("default_unavailable", provider, name))
        options["model"] = f"{provider}::{name}"
        return options
    refusal = _refusal(model, providers)
    if refusal is not None:
        raise ChoiceRefused(refusal)
    options["model"] = model
    return options


async def offered_model(database: Any, auth: Any, model_id: str, *, principal: Optional[str]) -> Tuple[str, str]:
    """``model_id`` (``provider::model``) as ``(provider, model)``, when the
    pickers offer it now. Raises :class:`ChoiceRefused`."""
    refusal = _refusal(model_id, await _providers(database, auth, principal))
    if refusal is not None:
        raise ChoiceRefused(refusal)
    return split_chat_model(model_id)


async def check_settings(database: Any, auth: Any, patch: Mapping[str, Any], *, principal: Optional[str]) -> None:
    """A settings save that sets a picker's choice keeps to what it offers
    now. Raises :class:`ChoiceRefused`."""
    for model_key, effort_key in CHOICE_SETTINGS:
        if model_key in patch and patch[model_key] != AUTO:
            chosen = patch[model_key]
            refusal = (
                _refusal(chosen, await _providers(database, auth, principal))
                if isinstance(chosen, str)
                else str(choice_setting("not_offered"))
            )
            if refusal is not None:
                raise ChoiceRefused(refusal)
        if effort_key in patch and patch[effort_key] not in ("", *EFFORTS):
            raise ChoiceRefused(str(choice_setting("bad_effort")))


def model_of(options: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    """The ``(provider, model)`` a run's options chose, if any."""
    chosen = options.get("model")
    return split_chat_model(chosen) if isinstance(chosen, str) and "::" in chosen else None


def effort_of(options: Mapping[str, Any]) -> Optional[str]:
    effort = options.get("effort")
    return effort if effort in EFFORTS else None


async def _answering_model(database: Any, graph: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    """The model of the agent that answers the owner in ``graph``."""
    from services.employees.talk import talk_state

    agent = talk_state(graph).agent_node_id
    if agent is None:
        return None
    parameters = await database.get_node_parameters(agent) or {}
    return await agent_model(parameters, database)


def _effort_note(effort: bool, model: str) -> Optional[str]:
    """Said in place of the levels when a model takes no effort choice."""
    return None if effort else str(choice_setting("effort_off")).format(model=model)


def _row(provider: str, provider_name: str, model: str, *, available: bool, reason: Optional[str]) -> Dict[str, Any]:
    """One model's row, named by its id under its provider's name. The
    button shows the id's last part (``anthropic/claude-sonnet-5.5`` on
    OpenRouter shows ``claude-sonnet-5.5``)."""
    short = model.rsplit("/", 1)[-1] or model
    effort = supports_effort(provider, model)
    return {
        "id": f"{provider}::{model}",
        "name": model,
        "short": short,
        "description": "",
        "provider": provider,
        "provider_name": provider_name,
        "effort": effort,
        "effort_note": _effort_note(effort, short),
        "available": available,
        "reason": reason,
    }


def _auto_row(description: str, *, effort: bool, effort_note: Optional[str], reason: Optional[str]) -> Dict[str, Any]:
    words = choice_setting("auto")
    return {
        "id": AUTO,
        "name": str(words["name"]),
        "short": str(words["short"]),
        "description": description,
        "provider": None,
        "provider_name": None,
        "effort": effort,
        "effort_note": effort_note,
        "available": reason is None,
        "reason": reason,
    }


async def list_models(
    database: Any,
    auth: Any,
    *,
    principal: Optional[str],
    graph: Mapping[str, Any],
    name: str,
) -> Dict[str, Any]:
    """The picker for one employee's chat: Auto's row (what it uses now),
    each connected provider's offered models (each row names its provider),
    the saved choice's row even when it can't answer, and the thinking
    levels. The choice itself is read with the owner's settings
    (``chat_model`` / ``chat_effort``)."""
    providers = await _providers(database, auth, principal)
    settings = await _settings(database)
    default = await _default(settings)
    if default is not None:
        provider, model = default
        effort = supports_effort(provider, model)
        connected = any(entry["provider"] == provider for entry in providers)
        auto = _auto_row(
            str(choice_setting("default_model")).format(model=model),
            effort=effort,
            effort_note=_effort_note(effort, model),
            reason=None if connected else _unavailable("default_unavailable", provider, model),
        )
    else:
        own = await _answering_model(database, graph)
        effort = supports_effort(*own) if own is not None else False
        auto = _auto_row(
            str(choice_setting("own_model")).format(model=own[1], name=name) if own is not None else str(choice_setting("own")).format(name=name),
            effort=effort,
            effort_note=_effort_note(effort, own[1] if own is not None else name),
            reason=None,
        )
    return {
        "auto": auto,
        "models": _offered_rows(providers, str(settings.get("chat_model") or AUTO)),
        "efforts": [dict(effort) for effort in choice_setting("efforts")],
    }


def _offered_rows(providers: Sequence[Mapping[str, Any]], chosen: str) -> List[Dict[str, Any]]:
    """Each connected provider's offered models in ``connected_providers``
    order, and the saved choice's row when the picker no longer offers it."""
    rows = [
        _row(str(entry["provider"]), str(entry["display_name"]), model, available=True, reason=None)
        for entry in providers
        for model in entry["offered"]
    ]
    if chosen != AUTO and all(row["id"] != chosen for row in rows):
        # Saved while its provider could run and the picker offered it:
        # shown, under its provider, and not usable.
        provider, model = split_chat_model(chosen)
        named = next((str(entry["display_name"]) for entry in providers if entry["provider"] == provider), None)
        saved = _row(provider, named or provider_display_name(provider), model or chosen, available=False, reason=_refusal(chosen, providers))
        rows.append({**saved, "id": chosen})
    return rows


async def list_hire_models(
    database: Any,
    auth: Any,
    *,
    principal: Optional[str],
    auto: Optional[Tuple[str, str]],
) -> Dict[str, Any]:
    """The hire composer's picker: Auto's row (``auto``, the ``(provider,
    model)`` a new hire gets without a pick; none while no AI model is set
    up), each connected provider's offered models, the saved choice's row
    even when it can't answer, and the thinking levels. The choice itself is
    read with the owner's settings (``hire_model`` / ``hire_effort``)."""
    providers = await _providers(database, auth, principal)
    settings = await _settings(database)
    if auto is None:
        auto_row = _auto_row("", effort=False, effort_note=None, reason=None)
    else:
        provider, model = auto
        effort = supports_effort(provider, model)
        auto_row = _auto_row(
            str(choice_setting("hire_auto")).format(model=model),
            effort=effort,
            effort_note=_effort_note(effort, model),
            reason=None,
        )
    return {
        "auto": auto_row,
        "models": _offered_rows(providers, str(settings.get("hire_model") or AUTO)),
        "efforts": [dict(effort) for effort in choice_setting("efforts")],
    }


__all__ = [
    "AUTO",
    "CHOICE_SETTINGS",
    "EFFORTS",
    "ChoiceRefused",
    "check_settings",
    "effort_of",
    "list_hire_models",
    "list_models",
    "model_of",
    "offered_model",
    "run_options",
]
