"""The model and thinking the owner picks for the employee answering them.

Home's employee chat has a model picker (design handoff Chat v2): Auto, or one
of ``chat_models`` in llm_defaults.json, and how hard the model thinks:
Balanced (the employee's own setting), Quick (effort ``low``) or Thorough
(``high``). The choice is saved with the owner's settings
(``UserSettings.chat_model`` / ``chat_effort``) and sent with every message,
edit and retry as ``options.model`` / ``options.effort``. Admission resolves it
here and keeps the result on the run (``ChatRun.options``), and
``prepare_agent_payload`` applies it to the agent that answers the run only.

- Auto is the owner's default model (Set Global Model). With none set it
  changes nothing: the employee answers on its own model.
- A model whose provider has no key is refused, never swapped for another,
  and so is a model the picker no longer offers.

Every word the picker shows comes from ``chat_defaults.json`` (``choice``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from services.chat.config import choice_setting
from services.llm.config import (
    agent_model,
    chat_models,
    provider_display_name,
    split_chat_model,
    supports_effort,
)

#: The efforts a message may carry; Balanced sends none.
EFFORTS = ("low", "high")
AUTO = "auto"
SETTINGS_USER_ID = "default"


class ChoiceRefused(Exception):
    """The model can't answer: its provider isn't connected, or the picker no
    longer offers it. ``detail`` says so in the owner's terms."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _offered() -> Dict[str, Dict[str, Any]]:
    return {str(entry.get("id")): entry for entry in chat_models()}


def _model_name(provider: str, model: str) -> str:
    """A model's name: the picker's when it offers the model, its id otherwise."""
    entry = _offered().get(f"{provider}::{model}")
    return str(entry["name"]) if entry else model


async def _connected(auth: Any, principal: Optional[str]) -> Set[str]:
    from services.employees.connections import Connections

    return set(await Connections(auth, principal=principal).ai_providers())


async def _settings(database: Any) -> Mapping[str, Any]:
    return await database.get_user_settings(SETTINGS_USER_ID) or {}


def _default_of(settings: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    """The owner's default model (Set Global Model), when one is set."""
    provider = str(settings.get("default_llm_provider") or "")
    model = str(settings.get("default_llm_model") or "")
    return (provider, model) if provider and model else None


def _unavailable(key: str, provider: str, model_name: str) -> str:
    return str(choice_setting(key)).format(model=model_name, provider=provider_display_name(provider))


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
    connected = await _connected(auth, principal)
    if model == AUTO:
        default = _default_of(await _settings(database))
        if default is None:
            return options
        provider, name = default
        if provider not in connected:
            raise ChoiceRefused(_unavailable("default_unavailable", provider, _model_name(provider, name)))
        options["model"] = f"{provider}::{name}"
        return options
    entry = _offered().get(model)
    if entry is None:
        raise ChoiceRefused(str(choice_setting("not_offered")))
    provider, _ = split_chat_model(model)
    if provider not in connected:
        raise ChoiceRefused(_unavailable("unavailable", provider, str(entry["name"])))
    options["model"] = model
    return options


def check_settings(patch: Mapping[str, Any]) -> None:
    """A settings save that sets the picker's choice keeps to what it offers.
    Raises :class:`ChoiceRefused`."""
    if "chat_model" in patch and patch["chat_model"] != AUTO and patch["chat_model"] not in _offered():
        raise ChoiceRefused(str(choice_setting("not_offered")))
    if "chat_effort" in patch and patch["chat_effort"] not in ("", *EFFORTS):
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


def _effort_note(effort: bool, short: str) -> Optional[str]:
    """Said in place of the levels when a model takes no effort choice."""
    return None if effort else str(choice_setting("effort_off")).format(model=short)


def _row(entry: Mapping[str, Any], *, available: bool, reason: Optional[str]) -> Dict[str, Any]:
    provider, model = split_chat_model(str(entry["id"]))
    effort = supports_effort(provider, model)
    return {
        "id": str(entry["id"]),
        "name": str(entry["name"]),
        "short": str(entry["short"]),
        "description": str(entry["description"]),
        "effort": effort,
        "effort_note": _effort_note(effort, str(entry["short"])),
        "available": available,
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
    """The picker for one employee's chat: Auto's row (what it uses now), the
    offered models of connected providers, the saved choice's row even when
    it can't answer, and the thinking levels. The choice itself is read with
    the owner's settings (``chat_model`` / ``chat_effort``)."""
    connected = await _connected(auth, principal)
    settings = await _settings(database)
    auto_words = choice_setting("auto")
    default = _default_of(settings)
    if default is not None:
        provider, model = default
        model_name = _model_name(provider, model)
        available = provider in connected
        effort = supports_effort(provider, model)
        auto = {
            "id": AUTO,
            "name": str(auto_words["name"]),
            "short": str(auto_words["short"]),
            "description": str(choice_setting("default_model")).format(model=model_name),
            "effort": effort,
            "effort_note": _effort_note(effort, model_name),
            "available": available,
            "reason": None if available else _unavailable("default_unavailable", provider, model_name),
        }
    else:
        own = await _answering_model(database, graph)
        effort = supports_effort(*own) if own is not None else False
        auto = {
            "id": AUTO,
            "name": str(auto_words["name"]),
            "short": str(auto_words["short"]),
            "description": (
                str(choice_setting("own_model")).format(model=_model_name(*own), name=name)
                if own is not None
                else str(choice_setting("own")).format(name=name)
            ),
            "effort": effort,
            "effort_note": _effort_note(effort, _model_name(*own) if own is not None else name),
            "available": True,
            "reason": None,
        }
    chosen = str(settings.get("chat_model") or AUTO)
    rows: List[Dict[str, Any]] = []
    for entry in chat_models():
        provider, _ = split_chat_model(str(entry["id"]))
        available = provider in connected
        if available or entry["id"] == chosen:
            rows.append(_row(entry, available=available, reason=None if available else _unavailable("unavailable", provider, str(entry["name"]))))
    if chosen != AUTO and chosen not in _offered():
        # Saved before the picker stopped offering it: shown, not usable.
        rows.append(
            {
                "id": chosen,
                "name": chosen,
                "short": chosen,
                "description": "",
                "effort": False,
                "effort_note": None,
                "available": False,
                "reason": str(choice_setting("not_offered")),
            }
        )
    return {
        "auto": auto,
        "models": rows,
        "efforts": [dict(effort) for effort in choice_setting("efforts")],
    }


__all__ = [
    "AUTO",
    "EFFORTS",
    "ChoiceRefused",
    "check_settings",
    "effort_of",
    "list_models",
    "model_of",
    "run_options",
]
