"""Scoped progressive-disclosure runtime for connected agent skills."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Iterable, List, Tuple

from core.logging import get_logger

logger = get_logger(__name__)

MAX_RESOURCE_CHARS = 16_000
MAX_SEARCH_MATCHES = 50
_loaded: Dict[Tuple[str, str, str, str], str] = {}
_turn_activity: Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]] = {}


class SkillRuntimeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_connected_skills(skills: Iterable[Dict[str, Any]]) -> None:
    by_name: Dict[str, List[Dict[str, str]]] = {}
    for item in skills:
        name = str(item.get("skill_name") or "").strip()
        if not name:
            continue
        by_name.setdefault(name, []).append(
            {
                "node_id": str(item.get("master_skill_node_id") or item.get("node_id") or ""),
                "label": str(item.get("label") or name),
            }
        )
    duplicate = next(((name, refs) for name, refs in by_name.items() if len(refs) > 1), None)
    if duplicate:
        name, refs = duplicate
        raise SkillRuntimeError(
            "DUPLICATE_CONNECTED_SKILL_NAME",
            f"Connected skill {name!r} is ambiguous across Master Skill nodes: {refs}",
        )


def standard_skill_descriptors(skills: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    validate_connected_skills(skills)
    return [item for item in skills if not str(item.get("skill_name") or "").endswith("-personality")]


def skill_tool_info(skills: Iterable[Dict[str, Any]], agent_node_id: str) -> Dict[str, Any] | None:
    descriptors = standard_skill_descriptors(skills)
    if not descriptors:
        return None
    rows = []
    for item in descriptors:
        name = str(item.get("skill_name") or "")
        description = str(item.get("description") or (item.get("parameters") or {}).get("description") or "Connected skill")
        rows.append(f"- {name}: {description}")
    # Tool descriptions are part of the normal provider tool contract. Keep
    # the surface bounded for providers with small function-description limits.
    catalogue = "\n".join(rows)
    if len(catalogue) > 7000:
        catalogue = catalogue[:6950] + "\n[additional connected skills omitted from this description]"
    tool_description = (
        "Load the instructions for a connected skill when its description matches the task. "
        "After loading, declared text resources can be read or searched with this same tool. "
        "Do not guess skill instructions from their descriptions.\nConnected skills:\n"
        f"{catalogue}"
    )
    return {
        "node_type": "_builtin_skill",
        "node_id": f"{agent_node_id}_skill_runtime",
        "label": "Skill",
        "parameters": {
            "skill_descriptors": descriptors,
            "agent_node_id": agent_node_id,
            "tool_description": tool_description,
        },
    }


def _resolve(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    descriptors = config.get("parameters", {}).get("skill_descriptors") or []
    validate_connected_skills(descriptors)
    matches = [item for item in descriptors if item.get("skill_name") == name]
    if len(matches) != 1:
        raise SkillRuntimeError("SKILL_NOT_CONNECTED", f"Skill {name!r} is not enabled and connected to this agent")
    return matches[0]


def _load_authoritative(descriptor: Dict[str, Any]):
    from services.skill_loader import get_skill_loader

    name = descriptor["skill_name"]
    configured = str((descriptor.get("parameters") or {}).get("instructions") or "")
    skill = get_skill_loader().load_skill(name)
    if configured:
        instructions = configured
    elif skill:
        instructions = skill.instructions
    else:
        raise SkillRuntimeError("SKILL_NOT_FOUND", f"Skill {name!r} could not be loaded")
    return instructions, skill


def _manifest(skill: Any) -> List[Dict[str, Any]]:
    if not skill:
        return []
    result = []
    for folder, values in (("references", skill.references), ("scripts", skill.scripts)):
        for name, content in sorted(values.items()):
            result.append({"path": f"{folder}/{name}", "characters": len(content)})
    return result


def _resource(skill: Any, path: str) -> str:
    if not skill or not re.fullmatch(r"(?:references|scripts)/[^/\\]+", path or "") or ".." in path:
        raise SkillRuntimeError("INVALID_SKILL_RESOURCE", "Resource must be a declared text reference or script")
    folder, name = path.split("/", 1)
    content = (skill.references if folder == "references" else skill.scripts).get(name)
    if content is None:
        raise SkillRuntimeError("SKILL_RESOURCE_NOT_FOUND", f"Undeclared skill resource: {path}")
    return content


async def _event(config: Dict[str, Any], descriptor: Dict[str, Any], action: str, state: str, **extra: Any) -> None:
    from services.status_broadcaster import get_status_broadcaster

    workflow_id = str(config.get("workflow_id") or "")
    execution_id = str(config.get("execution_id") or "")
    agent_id = str(config.get("parent_node_id") or (config.get("parameters") or {}).get("agent_node_id") or "")
    master_id = str(descriptor.get("master_skill_node_id") or "")
    name = str(descriptor.get("skill_name") or "")
    key = (workflow_id, execution_id, agent_id)
    activities = _turn_activity.setdefault(key, {})
    if state == "loading":
        activities[name] = {"name": name, "state": state, "master_skill_node_id": master_id}
    else:
        activities[name] = {"name": name, "state": state, "master_skill_node_id": master_id,
                            **{k: v for k, v in extra.items() if k in {"duration_ms", "error_code"}}}
    public = [{k: v for k, v in item.items() if k != "master_skill_node_id"} for item in activities.values()]
    broadcaster = get_status_broadcaster()
    payload = {
        "phase": "loading_skill" if state == "loading" else "skill_loaded",
        "active_skills": public,
        "last_capability": {"kind": "skill", "name": name, "state": state},
    }
    if agent_id:
        await broadcaster.update_node_status(agent_id, "executing", payload, workflow_id=workflow_id)
    if master_id:
        master_public = [
            {k: v for k, v in item.items() if k != "master_skill_node_id"}
            for item in activities.values()
            if item.get("master_skill_node_id") == master_id
        ]
        # Master Skill follows the same lifecycle as an ordinary tool node:
        # glow only while content is being fetched. Loaded/resource-read
        # results remain inspectable as badges but must not keep the node
        # visually executing for the rest of the agent turn.
        master_status = "executing" if state == "loading" else ("error" if state == "failed" else "success")
        await broadcaster.update_node_status(
            master_id,
            master_status,
            {"active_skills": master_public},
            workflow_id=workflow_id,
        )
    tool_call_id = str(config.get("tool_call_id") or "")
    event_id = None
    if tool_call_id:
        identity = "|".join(
            (
                workflow_id,
                execution_id,
                agent_id,
                tool_call_id,
                name,
                action,
                state,
            )
        )
        event_id = f"agent-capability-{hashlib.sha256(identity.encode()).hexdigest()}"
    await broadcaster.broadcast_agent_capability(
        agent_id,
        capability_kind="skill",
        capability_name=name,
        state=state,
        workflow_id=workflow_id,
        execution_id=execution_id,
        root_execution_id=str(config.get("root_execution_id") or "") or None,
        target_node_id=master_id or None,
        action=action,
        provider=str(config.get("provider") or "") or None,
        invocation_source=str(config.get("skill_invocation_source") or "internal"),
        tool_call_id=tool_call_id or None,
        duration_ms=extra.get("duration_ms"),
        returned_characters=extra.get("returned_characters"),
        token_estimate=extra.get("token_estimate"),
        content_hash=extra.get("content_hash"),
        error_code=extra.get("error_code"),
        event_id=event_id,
    )


async def execute_skill_tool(args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "load")
    name = str(args.get("skill_name") or "")
    descriptor = _resolve(config, name)
    started = time.monotonic()
    await _event(config, descriptor, action, "loading")
    try:
        instructions, skill = _load_authoritative(descriptor)
        content_hash = hashlib.sha256(instructions.encode()).hexdigest()
        scope = (str(config.get("workflow_id") or ""), str(config.get("execution_id") or ""),
                 str(config.get("parent_node_id") or ""), name)
        if action == "load":
            if _loaded.get(scope) == content_hash:
                result = {"status": "already_loaded", "skill_name": name, "content_hash": content_hash}
            else:
                _loaded[scope] = content_hash
                result = {"status": "loaded", "skill_name": name, "content_hash": content_hash,
                          "instructions": instructions, "resources": _manifest(skill)}
            state = "loaded"
        else:
            if _loaded.get(scope) != content_hash:
                raise SkillRuntimeError("SKILL_NOT_LOADED", "Load the skill before reading its resources")
            content = _resource(skill, str(args.get("path") or ""))
            if action == "read_resource":
                cursor = max(0, int(args.get("cursor") or 0)); limit = min(MAX_RESOURCE_CHARS, max(1, int(args.get("limit") or 4000)))
                result = {"skill_name": name, "path": args.get("path"), "content": content[cursor:cursor + limit],
                          "cursor": cursor, "next_cursor": cursor + limit if cursor + limit < len(content) else None}
            elif action == "search_resource":
                query = str(args.get("query") or "")
                if not query: raise SkillRuntimeError("INVALID_SKILL_QUERY", "query is required")
                matches = [{"line": i, "text": line[:500]} for i, line in enumerate(content.splitlines(), 1) if query.lower() in line.lower()]
                cursor = max(0, int(args.get("cursor") or 0)); limit = min(MAX_SEARCH_MATCHES, max(1, int(args.get("limit") or 20)))
                result = {"skill_name": name, "path": args.get("path"), "matches": matches[cursor:cursor + limit],
                          "next_cursor": cursor + limit if cursor + limit < len(matches) else None}
            else:
                raise SkillRuntimeError("INVALID_SKILL_ACTION", f"Unsupported Skill action: {action}")
            state = "resource_read"
        duration = int((time.monotonic() - started) * 1000)
        await _event(config, descriptor, action, state, content_hash=content_hash, duration_ms=duration,
                     returned_characters=len(str(result)), token_estimate=max(1, len(str(result)) // 4))
        return result
    except Exception as exc:
        await _event(config, descriptor, action, "failed", duration_ms=int((time.monotonic() - started) * 1000),
                     error_code=getattr(exc, "code", "SKILL_RUNTIME_ERROR"))
        raise


async def clear_skill_turn(workflow_id: str, execution_id: str, agent_node_id: str) -> None:
    key = (str(workflow_id or ""), str(execution_id or ""), str(agent_node_id or ""))
    activities = _turn_activity.pop(key, {})
    if not activities:
        return
    from services.status_broadcaster import get_status_broadcaster
    broadcaster = get_status_broadcaster()
    if agent_node_id:
        current = broadcaster.get_node_status(agent_node_id) or {}
        current_data = dict(current.get("data") or {})
        current_data["last_skills"] = [
            {"name": name, "state": "used" if item.get("state") != "failed" else "failed"}
            for name, item in activities.items()
        ]
        current_data["active_skills"] = []
        await broadcaster.update_node_status(
            agent_node_id,
            str(current.get("status") or "executing"),
            current_data,
            workflow_id=workflow_id,
        )
    for item in activities.values():
        master_id = item.get("master_skill_node_id")
        if master_id:
            same_master = [
                {"name": name, "state": "used" if value.get("state") != "failed" else "failed"}
                for name, value in activities.items()
                if value.get("master_skill_node_id") == master_id
            ]
            await broadcaster.update_node_status(
                master_id,
                "idle",
                {"active_skills": [], "last_skills": same_master},
                workflow_id=workflow_id,
            )
    for name, item in activities.items():
        event_id = None
        if execution_id:
            identity = "|".join(
                (
                    str(workflow_id or ""),
                    str(execution_id or ""),
                    str(agent_node_id or ""),
                    name,
                    "cleared",
                )
            )
            event_id = f"agent-capability-{hashlib.sha256(identity.encode()).hexdigest()}"
        await broadcaster.broadcast_agent_capability(
            agent_node_id,
            capability_kind="skill",
            capability_name=name,
            state="cleared",
            workflow_id=workflow_id,
            execution_id=execution_id,
            target_node_id=str(item.get("master_skill_node_id") or "") or None,
            invocation_source="internal",
            event_id=event_id,
        )
