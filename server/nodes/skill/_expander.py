"""Master-Skill expander (registered with services.plugin.edge_walker).

Wave 11.I, X3: extracted from ``services.plugin.edge_walker._append_skill_entries``
so the framework-side edge walker no longer imports
``services.skill_loader`` directly. The skill plugin package owns the
expansion logic and registers this callback from its ``__init__.py``;
edge_walker calls the registered callback through the
:func:`services.plugin.edge_walker.get_master_skill_expander` lookup.

Expansion is metadata-only for standard skills.  The authoritative body is
kept on the descriptor and is returned only by the scoped ``Skill`` tool.
Personality skills remain eager because they define agent identity.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.logging import get_logger

logger = get_logger(__name__)


async def expand_master_skill(
    source_node_id: str,
    skills_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Expand a Master-Skill node's enabled-skills config into per-skill entries.

    Args:
        source_node_id: The Master-Skill node's id; used to mint
            per-skill child node_ids (``{source}_{skill_key}``) so
            downstream agent dispatch can address each skill
            independently.
        skills_config: The ``skills_config`` dict from the Master-Skill
            node's parameters: ``{skill_key: {enabled: bool,
            instructions: str}}``.

    Returns:
        A list of ``{node_id, node_type, skill_name, parameters,
        label}`` dicts matching the shape ``edge_walker._append_skill_entries``
        produces for non-master skills.
    """
    from services.skill_loader import get_skill_loader

    registry = get_skill_loader().scan_skills()
    entries: List[Dict[str, Any]] = []

    for skill_key, skill_cfg in skills_config.items():
        if not skill_cfg.get("enabled", False):
            continue

        instructions = skill_cfg.get("instructions", "")
        metadata = registry.get(skill_key)
        if skill_key.endswith("-personality") and not instructions:
            try:
                personality = get_skill_loader().load_skill(skill_key)
                instructions = personality.instructions if personality else ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MasterSkill] Failed to load personality skill %s: %s", skill_key, exc)
        description = (
            skill_cfg.get("description")
            or (metadata.description if metadata else "")
            or f"Instructions for {skill_key}"
        )

        entries.append(
            {
                "node_id": f"{source_node_id}_{skill_key}",
                "master_skill_node_id": source_node_id,
                "node_type": "masterSkill",
                "skill_name": skill_key,
                "description": description,
                "parameters": {
                    "instructions": instructions,
                    "skillName": skill_key,
                    "description": description,
                },
                "label": skill_key,
            }
        )

    return entries


__all__ = ["expand_master_skill"]
