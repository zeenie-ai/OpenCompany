"""Trusted per-binding facts. Never edits saved schemas or legacy bindings."""
from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import socket
import sys
from copy import deepcopy
from pathlib import Path

from core.paths import safe_path_component


async def prepare_native_runtime(database, node_id, parameters, context, skills, tools, *, workflow_id=None):
    from services.agent_runtime_policy import runtime_context
    from services.plugin.base import saved_settings
    context = dict(context or {})
    snapshot = context.get("parameter_snapshot")
    saved = (await saved_settings(database, node_id, snapshot)
             if isinstance(snapshot, dict) and node_id in snapshot or callable(getattr(database, "get_node_parameters", None)) else {})
    frozen = runtime_context(saved)
    parameters = dict(parameters)
    parameters.pop("runtime_behavior_version", None)
    parameters.update(frozen)
    context.pop("runtime_behavior_version", None)
    context.update(frozen)
    if not frozen:
        return parameters, context, skills, tools
    context.setdefault("workflow_id", workflow_id)
    await fill_runtime_workspace(database, context)
    from services.browser_resources import validate_saved_browser_resources
    await validate_saved_browser_resources(database, str(context.get("workflow_id") or ""),
        str(context.get("user_id") or "owner"), {"nodes": context.get("nodes") or [], "edges": context.get("edges") or []},
        {**(context.get("parameter_snapshot") or {}), node_id: saved})
    tools = [bind_runtime_context(info, context, node_id) for info in (tools or [])]
    return parameters, context, skills, tools


def native_observer_options(context, node_id, workflow_id, provider, model):
    if (context or {}).get("runtime_behavior_version") != 1:
        return {}
    from services.llm.observation import warning_seconds
    return {"observer_payload": {"runtime_behavior_version": 1, "llm_warning_seconds": warning_seconds(), "node_id": node_id, "workflow_id": workflow_id,
        "agent_label": next((str((node.get("data") or {}).get("label") or "Agent") for node in context.get("nodes", []) if node.get("id") == node_id), "Agent"),
        "provider": provider, "model": model, "execution_id": context.get("execution_id"),
        "root_execution_id": context.get("root_execution_id"),
        "runtime_scope": {key: context[key] for key in ("generation", "native_workspace_version", "workspace_epoch", "invocation_id", "thread_id", "turn_id") if key in context}}}


async def fill_runtime_workspace(database, context):
    if context.get("workspace_dir"):
        return
    workflow_id = context.get("workflow_id")
    workflow = await database.get_workflow(workflow_id) if workflow_id else None
    if workflow is not None:
        from core.container import container
        context["workspace_dir"] = container.workflow_service()._get_workspace_dir(workflow.slug)


def process_cwd(workspace: str, node_id: str, cwd: str = "", *, version: int = 0) -> str:
    default = os.path.join(workspace, safe_path_component(node_id, "node")) if workspace else ""
    if not cwd:
        return default
    if version == 1 and not Path(cwd).is_absolute():
        if not workspace:
            raise ValueError("Workflow workspace is unavailable; use an absolute cwd")
        return str((Path(workspace) / cwd).resolve())
    return cwd


def runtime_facts(workspace: str, node_id: str, command: str = "", *, kind: str = "processManager") -> dict:
    try:
        argv = shlex.split(command, posix=os.name != "nt") if command else []
    except ValueError:
        argv = []
    executable = shutil.which(argv[0].strip('"')) if argv else None
    effective = process_cwd(workspace, node_id) if kind == "processManager" else os.getcwd() if kind == "pythonExecutor" else workspace
    return {
        "platform": platform.system(), "execution_host": socket.gethostname(),
        "workflow_root": workspace or None,
        "default_cwd": effective, "effective_cwd": effective,
        "relative_cwd_base": os.getcwd() if kind == "pythonExecutor" else workspace or None,
        "backend_python": sys.executable,
        "path_python": shutil.which("python"), "resolved_command_executable": executable,
        "shell": shutil.which("nu") or (os.environ.get("COMSPEC") if os.name == "nt" else "/bin/sh"),
        "process_list_scope": "managed processes in this workflow on this backend worker only",
        "registry_lifetime": "backend worker process", "list_includes_os_listeners": False,
        "localhost_preview_requires_same_browser_host": True,
    }


def bind_runtime_context(info: dict, context: dict, agent_id: str) -> dict:
    if context.get("runtime_behavior_version") != 1:
        return info
    result = deepcopy(info)
    result["agent_runtime"] = {**(result.get("agent_runtime") or {}), "runtime_behavior_version": 1,
        "workspace_dir": context.get("workspace_dir") or "", "agent_node_id": agent_id}
    return result


def enrich_runtime_tools(built: list, info: dict) -> list:
    runtime = info.get("agent_runtime") or {}
    if runtime.get("runtime_behavior_version") != 1:
        return built
    from dataclasses import replace
    for tool, config in built:
        config["runtime_behavior_version"] = 1
        kind = info.get("node_type")
        suffix = ""
        if kind in {"processManager", "shell", "pythonExecutor"}:
            facts = runtime_facts(str(runtime.get("workspace_dir") or ""), str(info.get("node_id") or ""),
                                  str((info.get("parameters") or {}).get("command") or ""), kind=kind)
            facts["binding_host"] = facts.pop("execution_host")
            facts["snapshot_scope"] = "tool binding worker; Activity execution can use another worker"
            if kind == "processManager" and (info.get("parameters") or {}).get("cwd"):
                try:
                    facts["effective_cwd"] = process_cwd(str(runtime.get("workspace_dir") or ""),
                        str(info.get("node_id") or ""), str(info["parameters"]["cwd"]), version=1)
                except ValueError:
                    facts["effective_cwd"] = None
            suffix = "\n\nTrusted runtime context: " + json.dumps(facts, ensure_ascii=False)
            if kind == "processManager":
                suffix += (" Omitted cwd uses this tool's own directory. Set cwd='.' for shared workflow artifacts; "
                    "explicit relative cwd is workflow-root relative. Commands are argv, not a shell expression. "
                    "An empty list does not mean a port is free. Stop/restart only authorized named managed processes; "
                    "never terminate an unrelated PID. For an unknown listener choose another permitted port or ask the owner.")
            elif kind == "pythonExecutor":
                suffix += " Python execution is in-process; there is no child interpreter or per-tool chdir."
            else:
                suffix += " Shell cwd is the workflow workspace; use the reported shell/platform rather than assume Bash."
        elif kind == "browser":
            from services.browser_resources import browser_runtime_guidance
            suffix = "\n\n" + browser_runtime_guidance(info.get("browser_readiness"))
        if suffix:
            tool.definition = replace(tool.definition, description=tool.definition.description + suffix)
    return built
