"""Pi's cross-platform Bash tool."""

from datetime import timedelta

from .._pi_tools import PiShellParams, PiToolNode


class PiBashNode(PiToolNode):
    type = "piBash"
    display_name = "Pi Bash"
    subtitle = "Run Bash Command"
    description = "Run Bash commands with Pi's timeout and process cleanup"
    tool_name = "pi_bash"
    tool_description = "Execute a Bash command in the workflow workspace using Pi. Windows requires Git Bash or another installed Bash; Linux/macOS use Bash with sh fallback. Returns combined output and exit code. Timeout defaults to 30 seconds, maximum 600. Commands run on this machine and can access outside the workspace. Use Process Manager for persistent services."
    sdk_tool = "bash"
    chat_step = "Running a Bash command"
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    ui_hints = {"outputMode": "terminal"}
    # Leave time beyond the 600-second command limit for startup and cleanup.
    start_to_close_timeout = timedelta(seconds=660)
    Params = PiShellParams
