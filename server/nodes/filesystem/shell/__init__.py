"""Shell — Wave 11.C migration.

The command runs with Nushell where ``nu`` is on PATH and otherwise with the
host shell (``_backend.WorkspaceBackend.execute``), so its syntax differs
between machines. The tool description says so and gives the forms that work
under every runner: one program with plain arguments per call, inline code
only as one line without backslashes or double quotes, longer code in a file,
file edits through ``file_modify``.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.ansi import strip_ansi
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class ShellParams(BaseModel):
    command: str = Field(..., min_length=1, description="One program with plain arguments, such as `python script.py`.")
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
    description = "Execute short-lived shell commands from the workflow workspace"
    tool_name = "shell_execute"
    tool_description = (
        "Run one program in the workflow workspace and return its output (stdout and stderr together) and exit_code. "
        "Nushell parses the command where it is installed, otherwise the host shell does (cmd.exe on Windows, sh "
        "elsewhere), so pipes, redirection, chaining, quoting and backslash escapes differ between machines. Send one "
        "program with plain arguments per call, such as `python script.py` or `git status`, without pipes, redirection "
        'or chaining. Inline code such as `python -c "..."` must be one line with no backslashes or double quotes '
        "inside; put longer code in a file with file_modify and run the file. Change files with file_modify, not sed. "
        "The program must finish within the timeout. It runs on this machine, not in a sandbox."
    )
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    # OutputPanel renders stdout preformatted (terminal text, not markdown).
    ui_hints = {"outputMode": "terminal"}
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
