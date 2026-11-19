"""Pi's paginated file and image reader."""

from pydantic import Field

from .._pi_tools import PiParams, PiToolNode


class PiReadParams(PiParams):
    path: str = Field(..., min_length=1, description="File path inside the workflow workspace.")
    offset: int = Field(default=1, ge=1, description="First line to read, starting at 1.")
    limit: int | None = Field(default=None, ge=1, description="Maximum lines; Pi also caps text output at 50 KB.")


class PiReadNode(PiToolNode):
    type = "piRead"
    display_name = "Pi Read File"
    subtitle = "Read Text or Image"
    description = "Read workspace text and images with Pi's built-in reader"
    tool_name = "pi_read"
    tool_description = "Read a file inside the workflow workspace with Pi. Text uses 1-based offset and optional line limit; output is truncated with pagination guidance. PNG, JPEG, GIF and WebP images are returned as media. Use workspace-relative paths."
    sdk_tool = "read"
    chat_step = "Reading a file"
    Params = PiReadParams
