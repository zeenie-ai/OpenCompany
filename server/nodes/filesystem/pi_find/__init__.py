"""Pi's glob-based file finder."""

from pydantic import Field

from .._pi_tools import PiParams, PiToolNode


class PiFindParams(PiParams):
    pattern: str = Field(..., min_length=1, description="File glob, such as *.ts or src/**/*.py.")
    path: str = Field(default=".", min_length=1, description="Directory inside the workflow workspace.")
    limit: int = Field(default=1000, ge=1, description="Maximum file paths to return.")


class PiFindNode(PiToolNode):
    type = "piFind"
    display_name = "Pi Find Files"
    subtitle = "Find Files by Pattern"
    description = "Find workspace files by glob with Pi"
    tool_name = "pi_find"
    tool_description = "Find files inside the workflow workspace using a glob such as *.ts or src/**/*.py. Returns paths relative to the search directory and respects ignore files. Requires installed ripgrep (rg); no automatic downloads. Use workspace-relative paths."
    sdk_tool = "find"
    chat_step = "Finding files"
    Params = PiFindParams
