"""Pi's targeted, multi-replacement file editor."""

from pydantic import Field

from .._pi_tools import PiParams, PiToolNode


class PiReplacement(PiParams):
    oldText: str = Field(..., min_length=1, description="Existing text to replace; must identify one occurrence.")
    newText: str = Field(..., description="Replacement text; empty text deletes the match.")


class PiEditParams(PiParams):
    path: str = Field(..., min_length=1, description="File path inside the workflow workspace.")
    edits: list[PiReplacement] = Field(..., min_length=1, description="Disjoint replacements matched against the original file.")


class PiEditNode(PiToolNode):
    type = "piEdit"
    display_name = "Pi Edit File"
    subtitle = "Replace Matching Text"
    description = "Apply targeted edits and return a diff with Pi's built-in editor"
    tool_name = "pi_edit"
    tool_description = "Edit a workspace file using Pi's targeted text replacements. Supply edits [{oldText,newText}] matched against the original file. Matches must be unambiguous and non-overlapping. Returns a diff; preserves existing line endings. Use workspace-relative paths."
    sdk_tool = "edit"
    chat_step = "Editing a file"
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    Params = PiEditParams
