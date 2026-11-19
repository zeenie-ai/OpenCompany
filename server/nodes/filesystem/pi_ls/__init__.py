"""Pi's directory listing tool."""

from pydantic import Field

from .._pi_tools import PiParams, PiToolNode


class PiLsParams(PiParams):
    path: str = Field(default=".", min_length=1, description="Directory inside the workflow workspace.")
    limit: int = Field(default=500, ge=1, description="Maximum directory entries to return.")


class PiLsNode(PiToolNode):
    type = "piLs"
    display_name = "Pi List Directory"
    subtitle = "List Files and Folders"
    description = "List workspace directory entries with Pi"
    tool_name = "pi_ls"
    tool_description = "List files and folders in a workflow workspace directory using Pi. Includes hidden entries, marks directories with a trailing slash and truncates large listings. Use workspace-relative paths; path defaults to the workspace root."
    sdk_tool = "ls"
    chat_step = "Listing a directory"
    Params = PiLsParams
