"""Pi's optional native Windows PowerShell tool."""

from datetime import timedelta

from .._pi_tools import PiShellParams, PiToolNode


class PiPowerShellNode(PiToolNode):
    type = "piPowerShell"
    display_name = "Pi PowerShell"
    subtitle = "Run Windows Command"
    description = "Run native Windows PowerShell commands with Pi"
    tool_name = "pi_powershell"
    tool_description = "Execute a PowerShell command in the workflow workspace using Pi. Available on Windows only; prefers PowerShell 7 (pwsh), then Windows PowerShell. Runs without profiles or interactive prompts. Returns combined output and exit code. Timeout defaults to 30 seconds, maximum 600. Commands can access outside the workspace. Use Process Manager for persistent services."
    sdk_tool = "powershell"
    chat_step = "Running a PowerShell command"
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    ui_hints = {"outputMode": "terminal"}
    # Leave time beyond the 600-second command limit for startup and cleanup.
    start_to_close_timeout = timedelta(seconds=660)
    Params = PiShellParams
