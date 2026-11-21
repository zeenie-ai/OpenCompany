"""Skill domain WebSocket handlers.

Extracted from ``routers/websocket.py`` (Wave 13). The 13 handlers below
cover:

  - Built-in skill content I/O (``get_skill_content`` / ``save_skill_content``
    / ``scan_skill_folder`` / ``list_skill_folders`` / ``lookup_skill_metadata``
    / ``reset_skill``).
  - User-skill CRUD (``get_user_skills`` / ``get_user_skill`` /
    ``create_user_skill`` / ``update_user_skill`` / ``delete_user_skill``).
  - Auto-skill edge policy (``evaluate_auto_skill``).
  - Memory clearing (``clear_memory``).

All handlers preserve their pre-Wave-13 wire shape (request and response).
The dispatch path was renamed but the WS message-type strings are
untouched — frontend handlers keep working without modification.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from fastapi import WebSocket

from core.container import container
from core.logging import get_logger
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)


# ============================================================================
# Built-in + user skill content I/O
# ============================================================================


@ws_handler("skill_name")
async def handle_get_skill_content(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get skill content (instructions) by skill name.

    Works for both built-in skills (from SKILL.md files) and user skills (from database).
    """
    from services.skill_loader import get_skill_loader_async

    skill_name = data["skill_name"]
    skill_loader = await get_skill_loader_async()

    skill = await skill_loader.load_skill_async(skill_name)
    if data.get("master_skill_node_id"):
        from services.skill_defaults import scoped_defaults_version, v1_default
        if await scoped_defaults_version(container.database(), websocket, data["master_skill_node_id"]) == 1:
            skill = await asyncio.to_thread(v1_default, skill_name, skill)
    if skill:
        return {
            "success": True,
            "skill_name": skill_name,
            "instructions": skill.instructions,
            "description": skill.metadata.description,
            "allowed_tools": skill.metadata.allowed_tools,
            "is_builtin": skill.metadata.path is not None,
            "timestamp": time.time(),
        }

    return {"success": False, "error": f"Skill '{skill_name}' not found"}


@ws_handler("skill_name", "instructions")
async def handle_save_skill_content(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save skill content (instructions) by skill name.

    For built-in skills, writes to the SKILL.md file.
    For user skills, updates the database.
    """
    from services.skill_loader import get_skill_loader_async

    skill_name = data["skill_name"]
    new_instructions = data["instructions"]
    skill_loader = await get_skill_loader_async()

    registry = skill_loader.get_registry()
    if skill_name in registry:
        metadata = registry[skill_name]
        if metadata.path is not None:
            skill_md_path = metadata.path / "SKILL.md"

            if not await asyncio.to_thread(_save_builtin_skill, skill_loader, skill_md_path, new_instructions):
                return {"success": False, "error": f"SKILL.md not found for '{skill_name}'"}

            from nodes.skill.master_skill._events import broadcast_skill_lifecycle

            await broadcast_skill_lifecycle(
                "content_saved",
                name=skill_name,
                is_builtin=True,
            )
            logger.info(f"[Skills] Updated built-in skill: {skill_name}")
            return {
                "success": True,
                "skill_name": skill_name,
                "is_builtin": True,
                "message": f"Skill '{skill_name}' saved to SKILL.md",
                "timestamp": time.time(),
            }

    database = container.database()
    user_skill = await database.get_user_skill(skill_name)
    if user_skill:
        updated = await database.update_user_skill(
            name=skill_name,
            instructions=new_instructions,
        )
        if updated:
            from nodes.skill.master_skill._events import broadcast_skill_lifecycle

            skill_loader.clear_cache()
            await broadcast_skill_lifecycle(
                "content_saved",
                name=skill_name,
                is_builtin=False,
            )
            logger.info(f"[Skills] Updated user skill: {skill_name}")
            return {
                "success": True,
                "skill_name": skill_name,
                "is_builtin": False,
                "message": f"Skill '{skill_name}' saved to database",
                "timestamp": time.time(),
            }

    return {"success": False, "error": f"Skill '{skill_name}' not found"}


def _save_builtin_skill(skill_loader, path, instructions: str) -> bool:
    """Publish complete content and invalidate even if the async caller exits."""
    import re
    from nodes.filesystem._backend import atomic_write_text

    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    frontmatter = re.match(r"^(---\s*\n.*?\n---\s*\n)", content, re.DOTALL)
    atomic_write_text(path, (frontmatter.group(1) if frontmatter else "") + instructions)
    skill_loader.clear_cache()
    return True


@ws_handler("action", "source_type", "target_type", "target_handle")
async def handle_evaluate_auto_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Decide what to do when a tool->agent edge is connected/disconnected.

    Owns the auto-add-skill policy: visuals.json reverse map +
    plugin-registry agent classification + canonical SkillConfig
    shape. Frontend forwards minimal edge details and the current
    Master Skill state; this returns a standard workflow-ops batch
    (see docs-internal/workflow_ops_protocol.md). The wired Master
    Skill's saved row is read here so the returned
    ``set_node_parameters`` op replaces it with every other setting kept.
    """
    from services import auto_skill

    master_skill_id = data.get("master_skill_id")
    master_skill_parameters = None
    if master_skill_id:
        master_skill_parameters = await container.database().get_node_parameters(master_skill_id)

    result = auto_skill.evaluate(
        action=data["action"],
        source_type=data["source_type"],
        target_type=data["target_type"],
        target_handle=data["target_handle"],
        target_node_id=data.get("target_node_id"),
        master_skill_id=master_skill_id,
        master_skill_config=data.get("master_skill_config"),
        master_skill_parameters=master_skill_parameters,
    )
    return {"success": True, **result}


@ws_handler()
async def handle_list_skill_folders(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """List top-level subdirectories under server/skills/.

    Returns folder names for the skill folder dropdown in MasterSkillEditor.
    """
    return await asyncio.to_thread(_list_skill_folders)


def _list_skill_folders() -> Dict[str, Any]:
    from pathlib import Path

    server_dir = Path(__file__).parent.parent.parent
    skills_dir = server_dir / "skills"

    folders = []
    if skills_dir.exists():
        for item in sorted(skills_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                skill_count = len(list(item.rglob("SKILL.md")))
                folders.append(
                    {
                        "name": item.name,
                        "skill_count": skill_count,
                    }
                )

    return {"success": True, "folders": folders}


@ws_handler("folder")
async def handle_scan_skill_folder(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Scan a subfolder under server/skills/ for SKILL.md files.

    Returns list of discovered skills with their metadata.
    Used by MasterSkillEditor when skillFolder is set.
    Also registers discovered skills in the global registry for get_skill_content.
    """
    from pathlib import Path
    from services.skill_loader import get_skill_loader_async

    folder = data["folder"]
    server_dir = Path(__file__).parent.parent.parent
    target_dir = server_dir / "skills" / folder

    if not await asyncio.to_thread(target_dir.exists):
        return {"success": False, "error": f"Folder not found: skills/{folder}"}

    skill_loader = await get_skill_loader_async()
    registry = await asyncio.to_thread(skill_loader.scan_skill_folder, target_dir)
    skills = [
        {"name": metadata.name, "description": metadata.description, "metadata": metadata.metadata}
        for metadata in registry.values()
    ]

    return {"success": True, "skills": skills, "folder": folder}


@ws_handler()
async def handle_lookup_skill_metadata(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Look up SKILL.md metadata for a list of skill names across every
    folder (and the user-skills DB).

    The Master Skill editor lets users keep a skill enabled even after
    switching the node's `skill_folder`, so the AI agent's Connected
    Skills panel needs to resolve metadata for skills outside the
    currently-selected folder. `scan_skill_folder` is folder-scoped;
    this handler is name-scoped and queries the shared registry.
    """
    from services.skill_loader import get_skill_loader_async

    names = data.get("names") or []
    if not isinstance(names, list):
        return {"success": False, "error": "names must be a list", "skills": []}

    skill_loader = await get_skill_loader_async()
    registry = skill_loader.get_registry()

    skills: List[Dict[str, Any]] = []
    for name in names:
        meta = registry.get(name)
        if meta:
            skills.append(
                {
                    "name": meta.name,
                    "description": meta.description,
                    "metadata": meta.metadata,
                }
            )

    return {"success": True, "skills": skills}


# ============================================================================
# User-skill CRUD
# ============================================================================


@ws_handler()
async def handle_get_user_skills(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all user-created skills."""
    database = container.database()
    active_only = data.get("active_only", True)
    skills = await database.get_all_user_skills(active_only=active_only)
    return {"skills": skills, "count": len(skills), "timestamp": time.time()}


@ws_handler("name")
async def handle_get_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get a specific user skill by name."""
    database = container.database()
    skill = await database.get_user_skill(data["name"])
    if skill:
        return {"skill": skill, "timestamp": time.time()}
    return {"success": False, "error": f"Skill '{data['name']}' not found"}


@ws_handler("name", "display_name", "instructions")
async def handle_create_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Create a new user skill."""
    from nodes.skill.master_skill._events import broadcast_skill_lifecycle

    database = container.database()

    skill = await database.create_user_skill(
        name=data["name"],
        display_name=data["display_name"],
        description=data.get("description", ""),
        instructions=data["instructions"],
        allowed_tools=data.get("allowed_tools"),
        category=data.get("category", "custom"),
        icon=data.get("icon", "star"),
        color=data.get("color", "#6366F1"),
        metadata_json=data.get("metadata"),
        created_by=data.get("created_by"),
    )

    if skill:
        await broadcast_skill_lifecycle("created", name=data["name"], skill=skill)
        return {"skill": skill, "timestamp": time.time()}
    return {"success": False, "error": f"Failed to create skill. Name '{data['name']}' may already exist."}


@ws_handler("name")
async def handle_update_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Update an existing user skill."""
    from nodes.skill.master_skill._events import broadcast_skill_lifecycle
    from services.skill_loader import get_skill_loader_async

    database = container.database()
    skill_loader = await get_skill_loader_async()

    skill = await database.update_user_skill(
        name=data["name"],
        display_name=data.get("display_name"),
        description=data.get("description"),
        instructions=data.get("instructions"),
        allowed_tools=data.get("allowed_tools"),
        category=data.get("category"),
        icon=data.get("icon"),
        color=data.get("color"),
        metadata_json=data.get("metadata"),
        is_active=data.get("is_active"),
    )

    if skill:
        skill_loader.clear_cache()
        await broadcast_skill_lifecycle("updated", name=data["name"], skill=skill)
        return {"skill": skill, "timestamp": time.time()}
    return {"success": False, "error": f"Skill '{data['name']}' not found"}


@ws_handler("name")
async def handle_delete_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete a user skill."""
    from nodes.skill.master_skill._events import broadcast_skill_lifecycle
    from services.skill_loader import get_skill_loader_async

    database = container.database()
    skill_loader = await get_skill_loader_async()

    deleted = await database.delete_user_skill(data["name"])

    if deleted:
        skill_loader.clear_cache()
        await broadcast_skill_lifecycle("deleted", name=data["name"])
        logger.info(f"[Skills] Deleted user skill: {data['name']}")
        return {"success": True, "deleted": True, "name": data["name"], "timestamp": time.time()}
    return {"success": False, "error": f"Skill '{data['name']}' not found"}


# ============================================================================
# Memory + skill reset
# ============================================================================


@ws_handler()
async def handle_clear_memory(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear conversation memory and sibling agent session state.

    Business logic lives in :func:`services.memory.clear_agent_session_state`
    — this handler only decodes the request and shapes the response.

    When ``memory_node_id`` is provided (claude_code_agent JSONL bridge
    surface), the simpleMemory node's ``memory_content`` is reset and
    ``memory_jsonl`` + ``last_session_id`` are wiped server-side. Legacy
    callers that omit it still get ``default_content`` for the
    frontend's existing markdown reset path.
    """
    from services.memory import clear_agent_session_state

    session_id = data.get("session_id", "default")
    workflow_id = data.get("workflow_id")
    clear_long_term = data.get("clear_long_term", False)
    memory_node_id = data.get("memory_node_id")

    cleared = await clear_agent_session_state(
        session_id=session_id,
        workflow_id=workflow_id,
        clear_long_term=clear_long_term,
        memory_node_id=memory_node_id,
    )

    return {
        "success": True,
        "default_content": "# Conversation History\n\n*No messages yet.*\n",
        "cleared_vector_store": cleared["cleared_vector_store"],
        "cleared_todo_keys": cleared["cleared_todo_keys"],
        "cleared_memory_node": cleared["cleared_memory_node"],
        "session_id": session_id,
    }


@ws_handler("skill_name")
async def handle_reset_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get original SKILL.md content for resetting a built-in skill."""
    import re
    from services.skill_loader import get_skill_loader_async

    skill_name = data["skill_name"]
    skill_loader = await get_skill_loader_async()
    registry = skill_loader.get_registry()

    if skill_name not in registry:
        return {"success": False, "error": f"Skill '{skill_name}' not found"}

    metadata = registry[skill_name]

    if data.get("master_skill_node_id"):
        from services.skill_defaults import scoped_defaults_version, v1_default
        if await scoped_defaults_version(container.database(), websocket, data["master_skill_node_id"]) == 1:
            variant = await asyncio.to_thread(v1_default, skill_name)
            if variant:
                return {"success": True, "skill_name": skill_name, "original_content": variant.instructions, "is_builtin": variant.metadata.path is not None}

    if metadata.path is None:
        return {"success": False, "error": f"Cannot reset user skill '{skill_name}' - no default exists"}

    skill_md_path = metadata.path / "SKILL.md"
    if not await asyncio.to_thread(skill_md_path.exists):
        return {"success": False, "error": f"SKILL.md not found for '{skill_name}'"}

    content = await asyncio.to_thread(skill_md_path.read_text, encoding="utf-8")

    frontmatter_match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
    if frontmatter_match:
        original_instructions = content[frontmatter_match.end() :]
    else:
        original_instructions = content

    logger.info(f"[Skill] Reset skill '{skill_name}' to default content")

    return {
        "success": True,
        "skill_name": skill_name,
        "original_content": original_instructions,
        "is_builtin": True,
    }


# ============================================================================
# Registry export — consumed by services/skills/__init__.py
# ============================================================================


WS_HANDLERS: Dict[str, Any] = {
    # Built-in + user skill content
    "get_skill_content": handle_get_skill_content,
    "save_skill_content": handle_save_skill_content,
    "scan_skill_folder": handle_scan_skill_folder,
    "list_skill_folders": handle_list_skill_folders,
    "evaluate_auto_skill": handle_evaluate_auto_skill,
    "lookup_skill_metadata": handle_lookup_skill_metadata,
    # User-skill CRUD
    "get_user_skills": handle_get_user_skills,
    "get_user_skill": handle_get_user_skill,
    "create_user_skill": handle_create_user_skill,
    "update_user_skill": handle_update_user_skill,
    "delete_user_skill": handle_delete_user_skill,
    # Memory + skill reset
    "clear_memory": handle_clear_memory,
    "reset_skill": handle_reset_skill,
}


__all__ = [
    "WS_HANDLERS",
    "handle_clear_memory",
    "handle_create_user_skill",
    "handle_delete_user_skill",
    "handle_evaluate_auto_skill",
    "handle_get_skill_content",
    "handle_get_user_skill",
    "handle_get_user_skills",
    "handle_list_skill_folders",
    "handle_lookup_skill_metadata",
    "handle_reset_skill",
    "handle_save_skill_content",
    "handle_scan_skill_folder",
    "handle_update_user_skill",
]
