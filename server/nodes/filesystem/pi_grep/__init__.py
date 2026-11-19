"""Pi's ripgrep-backed content search."""

from pydantic import Field

from .._pi_tools import PiParams, PiToolNode


class PiGrepParams(PiParams):
    pattern: str = Field(..., min_length=1, description="Search pattern, interpreted as a regex unless literal is enabled.")
    path: str = Field(default=".", min_length=1, description="Workspace file or directory to search.")
    glob: str | None = Field(default=None, description="Optional file glob, such as *.py.")
    ignoreCase: bool = False
    literal: bool = False
    context: int = Field(default=0, ge=0, description="Lines of context around each match.")
    limit: int = Field(default=100, ge=1, description="Maximum matching lines.")


class PiGrepNode(PiToolNode):
    type = "piGrep"
    display_name = "Pi Search Text"
    subtitle = "Search File Contents"
    description = "Search workspace file contents with Pi and ripgrep"
    tool_name = "pi_grep"
    tool_description = "Search workspace file contents with Pi's grep tool. Supports regex or literal matching, ignoreCase, glob filters and context lines. Respects ignore files, returns file paths and line numbers, and caps output. Requires ripgrep (rg) installed on the worker; no automatic downloads. Use workspace-relative paths."
    sdk_tool = "grep"
    chat_step = "Searching file contents"
    Params = PiGrepParams
