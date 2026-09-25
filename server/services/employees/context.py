"""What the setup model is told about the owner's world.

Built fresh for every setup request: which apps are connected and which
could be, who is already on the team (so a new hire gets a new name), the
owner's profile, and their local time. Nothing here is the owner's job
text; that goes in the conversation, not the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.logging import get_logger
from services.employees.apps import get_apps
from services.employees.connections import Connections

logger = get_logger(__name__)

#: Settings rows are keyed by user id; single-owner installs use "default".
SETTINGS_USER_ID = "default"


@dataclass
class SetupContext:
    connected_apps: List[str] = field(default_factory=list)
    available_apps: List[str] = field(default_factory=list)
    team_names: List[str] = field(default_factory=list)
    owner_name: str = ""
    owner_role: str = ""
    owner_preferences: str = ""
    timezone: str = "UTC"
    local_time: str = ""
    has_ai: bool = False
    prefer_local_ai: bool = True


def _zone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_time_text(now: datetime, zone: ZoneInfo) -> str:
    """"Thursday 25 September 2026, 14:05" in the owner's zone."""
    moment = now.astimezone(zone)
    return f"{moment:%A} {moment.day} {moment:%B %Y}, {moment:%H:%M}"


async def build_setup_prompt_context(
    database: Any,
    connections: Connections,
    *,
    user_id: str = SETTINGS_USER_ID,
    now: Optional[datetime] = None,
) -> SetupContext:
    settings = {}
    try:
        settings = await database.get_user_settings(user_id) or {}
    except Exception:
        logger.warning("Could not read the owner's settings for setup", exc_info=True)

    connected: List[str] = []
    available: List[str] = []
    for app in get_apps().values():
        (connected if await connections.is_connected(app.provider_id) else available).append(app.name)

    team: List[str] = []
    try:
        team = [str(workflow.name) for workflow in await database.get_all_workflows() if getattr(workflow, "name", None)]
    except Exception:
        logger.warning("Could not list the team for setup", exc_info=True)

    zone = _zone(settings.get("profile_timezone"))
    call_name = str(settings.get("profile_call_name") or "").strip()
    full_name = str(settings.get("profile_full_name") or "").strip()
    return SetupContext(
        connected_apps=connected,
        available_apps=available,
        team_names=team,
        owner_name=call_name or full_name,
        owner_role=str(settings.get("profile_role") or "").strip(),
        owner_preferences=str(settings.get("profile_preferences") or "").strip(),
        timezone=zone.key,
        local_time=local_time_text(now or datetime.now(timezone.utc), zone),
        has_ai=await connections.has_ai(),
        prefer_local_ai=settings.get("prefer_local_ai") is not False,
    )


def render_context_block(context: SetupContext) -> str:
    """The context lines at the end of the system prompt."""
    lines = [
        f"Connected apps: {', '.join(context.connected_apps) or 'none'}.",
        f"Apps that can be connected: {', '.join(context.available_apps) or 'none'}.",
        f"Names already on the team (don't reuse): {', '.join(context.team_names) or 'none'}.",
    ]
    if context.owner_name:
        lines.append(f"The owner is {context.owner_name}.")
    if context.owner_role:
        lines.append(f"They are: {context.owner_role}.")
    if context.owner_preferences:
        lines.append(f"Their preferences: {context.owner_preferences}")
    lines.append(f"It is now {context.local_time} ({context.timezone}).")
    return "\n".join(lines)


__all__ = ["SETTINGS_USER_ID", "SetupContext", "build_setup_prompt_context", "local_time_text", "render_context_block"]
