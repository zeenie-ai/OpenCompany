"""Skill system prompt builder for AI agents.

Constructs system message text from connected skill nodes.
Personality skills get full SKILL.md instructions injected.
Standard skills are represented only by the connected ``Skill`` tool.
"""

from typing import Dict, Any, List

from core.logging import get_logger

logger = get_logger(__name__)


def build_skill_system_prompt(skill_data: List[Dict[str, Any]], log_prefix: str = "[Agent]") -> tuple:
    """Build skill injection text for the system message.

    Personality skills (names ending in '-personality') get their FULL SKILL.md
    instructions injected. Standard skills do not alter the system prompt; their
    names and descriptions live on the dynamically bound ``Skill`` tool, exactly
    like other connected tool nodes.

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

    personality_blocks = []
    standard_count = 0

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
            standard_count += 1
            logger.debug(f"{log_prefix} Skill detected: {skill_name}")

    parts = []

    for block in personality_blocks:
        parts.append(block)

    if parts:
        logger.debug(
            f"{log_prefix} Enhanced system message: {len(personality_blocks)} personality; "
            f"{standard_count} standard skills exposed through the Skill tool"
        )

    return "\n\n".join(parts), len(personality_blocks) > 0
