"""Skill system prompt builder for AI agents.

Constructs system message text from connected skill nodes.
Personality skills get full SKILL.md instructions injected.
Standard skills get brief registry descriptions.
"""

from typing import Dict, Any, List

from core.logging import get_logger

logger = get_logger(__name__)


def build_skill_system_prompt(skill_data: List[Dict[str, Any]], log_prefix: str = "[Agent]") -> tuple:
    """Build skill injection text for the system message.

    Personality skills (names ending in '-personality') get their FULL SKILL.md
    instructions injected. All other skills get brief registry descriptions only.

    Args:
        skill_data: List of skill entries from _collect_agent_connections.
        log_prefix: Log prefix for debug messages.

    Returns:
        Tuple of (prompt_text, has_personality). prompt_text is the string to
        append to system_message. has_personality indicates whether any
        personality skills were found (used to drop the default system message).
    """
    if not skill_data:
        return "", False

    from services.skill_loader import get_skill_loader

    skill_loader = get_skill_loader()
    skill_loader.scan_skills()

    personality_blocks = []
    non_personality_names = []

    for skill_info in skill_data:
        skill_name = skill_info.get("skill_name") or skill_info.get("node_type", "").replace("Skill", "-skill").lower()
        if skill_name.endswith("skill") and "-" not in skill_name:
            skill_name = skill_name[:-5] + "-skill"

        if skill_name.endswith("-personality"):
            instructions = skill_info.get("parameters", {}).get("instructions", "")
            if instructions:
                personality_blocks.append(instructions)
                logger.debug(f"{log_prefix} Personality skill injected (full): {skill_name}")
            else:
                logger.warning(f"{log_prefix} Personality skill {skill_name} has no instructions")
        else:
            non_personality_names.append(skill_name)
            logger.debug(f"{log_prefix} Skill detected: {skill_name}")

    parts = []

    for block in personality_blocks:
        parts.append(block)

    if non_personality_names:
        registry_prompt = skill_loader.get_registry_prompt(non_personality_names)
        if registry_prompt:
            parts.append(registry_prompt)

    if parts:
        logger.debug(
            f"{log_prefix} Enhanced system message: {len(personality_blocks)} personality, {len(non_personality_names)} standard skills"
        )

    return "\n\n".join(parts), len(personality_blocks) > 0
