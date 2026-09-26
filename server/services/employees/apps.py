"""The apps an employee can use (``config/employee_apps.json``).

One entry per app a hire can name ("WhatsApp", "Gmail", "Google Calendar"):
the credential provider that decides whether it is connected, and how the
app maps onto workflow nodes (the trigger that starts the employee, the
node that replies to whoever wrote in, the node that reports to the owner,
and the tools handed to the agent). The graph builder reads the templates;
the summaries read connection state and phrases.

Names from the model's setup screen are matched by :func:`resolve_app`:
exact, then prefix, then word, then substring (3+ characters), preferring
a connected app within a tier. A name that matches nothing is kept as an
unsupported app: it never blocks a start and the employee's instructions
name it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from core.logging import get_logger

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "employee_apps.json"

#: Least to most dangerous. A tool's class is the worst thing any of its
#: operations can do, because a tool call lets the model pick the operation.
SIDE_EFFECTS: Tuple[str, ...] = ("read", "write", "send", "money")
AUDIENCES: Tuple[str, ...] = ("public", "owner")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class AppRegistryError(RuntimeError):
    """Malformed employee_apps.json."""


@dataclass(frozen=True)
class NodeTemplate:
    type: str
    params: Mapping[str, Any]
    label: Optional[str] = None


@dataclass(frozen=True)
class TriggerTemplate(NodeTemplate):
    audience: str = "public"
    #: The agent's prompt for one firing, with ${trigger.<field>} placeholders.
    prompt: str = ""


@dataclass(frozen=True)
class ToolTemplate(NodeTemplate):
    side_effects: str = "read"
    #: Params that make the tool safe under ``ask first``. A tool that
    #: declares them stays attached for such an employee, with these
    #: merged over ``params`` (the browser turns read-only and hands any
    #: page change to the owner); a tool without them is left out.
    ask_first_params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: The key under which the builder records the tool's node id in the
    #: employee's ``node_roles``.
    role: Optional[str] = None


@dataclass(frozen=True)
class AppSpec:
    id: str
    name: str
    provider_id: str
    aliases: Tuple[str, ...]
    trigger: Optional[TriggerTemplate]
    reply: Optional[NodeTemplate]
    notify_owner: Optional[NodeTemplate]
    tools: Tuple[ToolTemplate, ...]
    side_effects: str
    phrases: Mapping[str, str]

    @property
    def node_types(self) -> frozenset:
        types = {t.type for t in (self.trigger, self.reply, self.notify_owner) if t is not None}
        types.update(tool.type for tool in self.tools)
        return frozenset(types)

    def phrase(self, key: str, default: str = "") -> str:
        return self.phrases.get(key) or default


def side_effect_rank(value: str) -> int:
    return SIDE_EFFECTS.index(value)


def allowed_when_asking_first(side_effects: str) -> bool:
    """Under the ``ask first`` ground rule nothing may send or spend on the
    owner's behalf without the approval step, so such tools stay off."""
    return side_effect_rank(side_effects) < side_effect_rank("send")


def normalize_name(value: str) -> str:
    return _NON_ALNUM.sub(" ", str(value or "").lower()).strip()


# ----- loading -----


def _template(app_id: str, role: str, raw: Any, cls: type = NodeTemplate) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or not raw["type"]:
        raise AppRegistryError(f"{app_id}.{role}: needs a node `type`")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise AppRegistryError(f"{app_id}.{role}.params must be an object")
    fields: Dict[str, Any] = {"type": raw["type"], "params": MappingProxyType(dict(params)), "label": raw.get("label")}
    if cls is TriggerTemplate:
        audience = raw.get("audience", "public")
        if audience not in AUDIENCES:
            raise AppRegistryError(f"{app_id}.trigger.audience must be one of {AUDIENCES}")
        fields.update(audience=audience, prompt=str(raw.get("prompt") or ""))
    if cls is ToolTemplate:
        effect = raw.get("side_effects")
        if effect not in SIDE_EFFECTS:
            raise AppRegistryError(f"{app_id}.tools[{raw['type']}].side_effects must be one of {SIDE_EFFECTS}")
        fields["side_effects"] = effect
        ask_first = raw.get("ask_first_params") or {}
        if not isinstance(ask_first, dict):
            raise AppRegistryError(f"{app_id}.tools[{raw['type']}].ask_first_params must be an object")
        fields["ask_first_params"] = MappingProxyType(dict(ask_first))
        tool_role = raw.get("role")
        if tool_role is not None and (not isinstance(tool_role, str) or not tool_role):
            raise AppRegistryError(f"{app_id}.tools[{raw['type']}].role must be a non-empty string")
        fields["role"] = tool_role
    return cls(**fields)


def _parse(raw: Mapping[str, Any]) -> Dict[str, AppSpec]:
    apps_raw = raw.get("apps")
    if not isinstance(apps_raw, dict) or not apps_raw:
        raise AppRegistryError("employee_apps.json needs a non-empty `apps` object")
    apps: Dict[str, AppSpec] = {}
    for app_id, entry in apps_raw.items():
        if not isinstance(entry, dict):
            raise AppRegistryError(f"{app_id}: must be an object")
        for key in ("name", "provider_id"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise AppRegistryError(f"{app_id}: needs `{key}`")
        effect = entry.get("side_effects")
        if effect not in SIDE_EFFECTS:
            raise AppRegistryError(f"{app_id}.side_effects must be one of {SIDE_EFFECTS}")
        tools = tuple(_template(app_id, "tools", tool, ToolTemplate) for tool in entry.get("tools") or [])
        apps[app_id] = AppSpec(
            id=app_id,
            name=entry["name"],
            provider_id=entry["provider_id"],
            aliases=tuple(str(alias) for alias in entry.get("aliases") or []),
            trigger=_template(app_id, "trigger", entry.get("trigger"), TriggerTemplate),
            reply=_template(app_id, "reply", entry.get("reply")),
            notify_owner=_template(app_id, "notify_owner", entry.get("notify_owner")),
            tools=tools,
            side_effects=effect,
            phrases=MappingProxyType({str(k): str(v) for k, v in (entry.get("phrases") or {}).items()}),
        )
    return apps


_apps: Optional[Dict[str, AppSpec]] = None
_by_node_type: Optional[Dict[str, AppSpec]] = None


def get_apps() -> Mapping[str, AppSpec]:
    """Every app, in declaration order (which breaks ties in resolve_app)."""
    global _apps, _by_node_type
    if _apps is None:
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppRegistryError(f"cannot read {CONFIG_PATH}: {exc}") from exc
        _apps = _parse(raw)
        index: Dict[str, AppSpec] = {}
        for app in _apps.values():
            for node_type in sorted(app.node_types):
                index.setdefault(node_type, app)
        _by_node_type = index
    return MappingProxyType(_apps)


def reload_apps() -> None:
    global _apps, _by_node_type
    _apps = None
    _by_node_type = None


def get_app(app_id: str) -> Optional[AppSpec]:
    return get_apps().get(app_id)


def app_for_node_type(node_type: str) -> Optional[AppSpec]:
    """The app a node type belongs to (reverse of the templates), if any."""
    get_apps()
    assert _by_node_type is not None
    return _by_node_type.get(node_type)


def _keys(app: AppSpec) -> Iterable[Tuple[str, int]]:
    """(normalized key, preference): the id and name first, aliases after."""
    yield normalize_name(app.id.replace("_", " ")), 0
    yield normalize_name(app.name), 0
    for alias in app.aliases:
        yield normalize_name(alias), 1


def _score(query: str, key: str) -> Optional[Tuple[int, int]]:
    """(tier, within-tier order): lower is a better match."""
    if not key:
        return None
    if query == key:
        return (0, 0)
    if key.startswith(query):
        return (1, len(key) - len(query))  # the shortest completion
    if query.startswith(key + " "):
        return (2, -len(key))  # the longest leading word run
    if len(query) >= 3 and len(key) >= 3:
        if key in query:
            return (3, -len(key))
        if query in key:
            return (3, len(key))
    return None


def resolve_app(name: str, connected: Optional[Sequence[str]] = None) -> Optional[AppSpec]:
    """The app a free-text name refers to, or None when nothing matches.

    Within the best tier a connected app wins, then a name match over an
    alias match, then declaration order: "email" picks Gmail when Gmail is
    connected and IMAP is not, and "calendar" picks whichever calendar is
    connected."""
    query = normalize_name(name)
    if not query:
        return None
    connected_ids = set(connected or ())
    best: Optional[Tuple[Tuple[int, int, int, int, int], AppSpec]] = None
    for index, app in enumerate(get_apps().values()):
        for key, preference in _keys(app):
            score = _score(query, key)
            if score is None:
                continue
            rank = (score[0], 0 if app.id in connected_ids else 1, preference, score[1], index)
            if best is None or rank < best[0]:
                best = (rank, app)
    return best[1] if best else None


__all__ = [
    "AUDIENCES",
    "AppRegistryError",
    "AppSpec",
    "CONFIG_PATH",
    "NodeTemplate",
    "SIDE_EFFECTS",
    "ToolTemplate",
    "TriggerTemplate",
    "allowed_when_asking_first",
    "app_for_node_type",
    "get_app",
    "get_apps",
    "normalize_name",
    "reload_apps",
    "resolve_app",
    "side_effect_rank",
]
