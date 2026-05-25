"""CloudEvents factories + broadcaster wrappers for skill lifecycle.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder. This file owns every skill-mutation wire frame so
``routers/websocket.py`` (and any future caller) doesn't hand-build
envelopes with drifting shapes.

Wire-format key: ``skill_lifecycle`` — the frontend's
``WebSocketContext`` routes on this key and invalidates the
``userSkills`` / ``folderSkills`` / ``skillContent`` TanStack queries
so the Master Skill panel and any agent's Connected Skills view pick
up changes live across every connected client.

Replaces four legacy raw-dict broadcasts:
  - ``{type: "user_skill_created", skill: ..., timestamp: ...}``
  - ``{type: "user_skill_updated", skill: ..., timestamp: ...}``
  - ``{type: "user_skill_deleted", name: ..., timestamp: ...}``
  - (silent — ``save_skill_content`` previously emitted nothing)
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from services.events.envelope import WorkflowEvent


_WIRE_KEY = "skill_lifecycle"

SkillStage = Literal["created", "updated", "deleted", "content_saved"]


def skill_lifecycle_event(
    stage: SkillStage,
    *,
    name: str,
    data: Optional[Mapping[str, Any]] = None,
) -> WorkflowEvent:
    """Build the CloudEvents v1.0 envelope for a skill-registry change.

    ``subject`` carries the skill name (the canonical identifier
    consumers key on); ``data`` carries the full skill record for
    ``created`` / ``updated`` and the minimal ``{name}`` for ``deleted``
    and ``content_saved``. ``content_saved`` additionally carries
    ``is_builtin`` so the FE can distinguish SKILL.md vs database
    writes without re-querying.
    """
    return WorkflowEvent(
        source="machinaos://nodes/master_skill",
        type=f"com.machinaos.skill.{stage}",
        subject=name,
        data=dict(data) if data else {"name": name},
    )


async def broadcast_skill_lifecycle(
    stage: SkillStage,
    *,
    name: str,
    **data_extra: Any,
) -> None:
    """Emit a CloudEvents-typed ``skill.<stage>`` envelope.

    Single source of truth for every skill-mutation wire frame —
    callers pass the stage + name + any extra fields and this wrapper
    handles envelope construction and broadcast.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    event = skill_lifecycle_event(stage, name=name, data=data_extra or None)
    await broadcaster.broadcast(
        {
            "type": _WIRE_KEY,
            "data": event.model_dump(mode="json"),
        }
    )


__all__ = [
    "SkillStage",
    "broadcast_skill_lifecycle",
    "skill_lifecycle_event",
]
