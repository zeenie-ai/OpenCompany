"""Pi's complete-file writer."""

from pydantic import Field

from .._pi_tools import PiParams, PiToolNode


class PiWriteParams(PiParams):
    path: str = Field(..., min_length=1, description="File path inside the workflow workspace.")
    content: str = Field(..., description="Complete new file contents; replaces an existing file.")


class PiWriteNode(PiToolNode):
    type = "piWrite"
    display_name = "Pi Write File"
    subtitle = "Create or Replace"
    description = "Create or replace a workspace file with Pi's built-in writer"
    tool_name = "pi_write"
    tool_description = "Write complete contents to a file inside the workflow workspace with Pi. Creates missing parent directories and replaces an existing file. Use pi_edit for targeted changes and workspace-relative paths."
    sdk_tool = "write"
    chat_step = "Writing a file"
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    Params = PiWriteParams
