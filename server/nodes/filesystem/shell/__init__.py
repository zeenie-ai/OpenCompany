"""Shell — Wave 11.C migration."""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.ansi import strip_ansi
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


# Bash chain-operators (` && ` / ` || `) are explicitly rejected by the
# Nushell parser. Detect them up-front so the LLM gets a corrective
# message ("use `;` or `try { … }`") instead of nu's
# ``shell_andand`` / ``shell_oror`` parse error two layers down.
# Surrounding spaces ensure we don't flag valid nu syntax accidentally
# (closure params `|x|` never carry spaces around the pipe pair).
_BASH_CHAIN_RE = re.compile(r"\s(\&\&|\|\|)\s")


class ShellParams(BaseModel):
    command: str = Field(..., min_length=1)
    cwd: str = Field(default="")
    timeout: int = Field(default=30, ge=1, le=600)

    model_config = ConfigDict(extra="ignore")


class ShellOutput(BaseModel):
    stdout: Optional[str] = None
    exit_code: Optional[int] = None
    truncated: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class ShellNode(ActionNode):
    type = "shell"
    display_name = "Shell"
    subtitle = "Run Command"
    group = ("filesystem", "tool")
    description = "Execute shell commands (sandboxed; no system PATH)"
    tool_name = "shell_execute"
    tool_description = "Execute a shell command. Returns stdout, stderr, and exit code."
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = ShellParams
    Output = ShellOutput

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: ShellParams) -> Any:
        """Inlined from handlers/filesystem.py (Wave 11.D.1)."""
        import asyncio
        from core.logging import get_logger
        from .._backend import get_backend

        log = get_logger(__name__)

        # Pre-flight: catch the most common bash-style chain mistake before
        # Nushell's parser does, so the LLM sees an actionable hint instead
        # of ``nu::parser::shell_andand``. Documented in
        # ``server/skills/terminal/shell-skill/SKILL.md``.
        if m := _BASH_CHAIN_RE.search(params.command):
            op = m.group(1)
            replacement = "; (sequential)" if op == "&&" else "try { … } catch { … }"
            raise NodeUserError(
                f"Nushell does not support `{op}`. Use `{replacement}` instead. "
                "See shell-skill: https://www.nushell.sh/book/control_flow.html"
            )

        backend = get_backend(params.model_dump(), ctx.raw)
        # "non-blocking" here only meant the asyncio event loop isn't
        # blocked (the call is offloaded via ``to_thread``). The
        # subprocess itself is still awaited — long-running commands
        # are killed at ``timeout`` and must use ``process_manager``
        # instead. Logging the bare command + timeout to avoid
        # misleading users into thinking shell runs daemons.
        log.info(
            "[Shell] Executing: %s (timeout=%ds)",
            params.command[:200],
            params.timeout,
        )
        result = await asyncio.to_thread(
            backend.execute,
            params.command,
            timeout=params.timeout,
        )
        # Strip ANSI colour/cursor codes — commands like ``vite build`` / ``npm``
        # emit them and they render as garbage ("[36m…[39m") in the Output panel.
        clean_output = strip_ansi(result.output)

        if result.exit_code == 124:
            log.warning("[Shell] Timed out after %ds: %s", params.timeout, params.command[:100])
        elif result.exit_code != 0:
            log.warning(
                "[Shell] Non-zero exit (%d): %s -> %s",
                result.exit_code,
                params.command[:100],
                clean_output[:300],
            )
        else:
            log.info("[Shell] Completed: exit=%d len=%d", result.exit_code, len(clean_output))

        return {
            "stdout": clean_output,
            "exit_code": result.exit_code,
            "truncated": result.truncated,
            "command": params.command,
        }
