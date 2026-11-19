"""Shared tool bundle for newly created Coding Agents."""
from __future__ import annotations

import sys
from typing import Any, Mapping, Sequence

from services.graph_build import GraphAdditions, NewNode, tool_edge


_PI_TOOLS = (
    ("piRead", "Pi Read File"),
    ("piWrite", "Pi Write File"),
    ("piEdit", "Pi Edit File"),
    ("piBash", "Pi Bash"),
    ("piGrep", "Pi Search Text"),
    ("piFind", "Pi Find Files"),
    ("piLs", "Pi List Directory"),
    ("piPowerShell", "Pi PowerShell"),
)


def default_pi_tool_types() -> tuple[str, ...]:
    """Pi's portable tools, plus its Windows-only PowerShell tool."""
    return tuple(kind for kind, _ in _PI_TOOLS if kind != "piPowerShell" or sys.platform == "win32")


def coding_agent_additions(
    *, agent_parameters: Mapping[str, Any] | None = None,
    position: tuple[float, float] = (0, 0), agent_ref: str = "agent",
    create_agent: bool = True, create_context: bool = True,
    exclude_tool_types: Sequence[str] = (),
) -> GraphAdditions:
    """Create an agent and its connected SDK tools, or complete a new agent's bundle.

    This is a creation recipe, never a saved-graph normalizer. Explicitly
    supplied tools take precedence so their parameters and connections survive.
    """
    x, y = position
    nodes, edges = [], []
    if create_agent:
        from services.agent_runtime_policy import new_agent_parameters
        nodes.append(NewNode(agent_ref, "coding_agent", "Coding Agent",
            new_agent_parameters(dict(agent_parameters or {})), position=(x, y)))
    if create_context:
        nodes.append(NewNode("context", "context", "Coding context",
            position=(x, y - 180), context_of=agent_ref))
    defaults = set(default_pi_tool_types()) - set(exclude_tool_types)
    for index, (kind, label) in enumerate((kind, label) for kind, label in _PI_TOOLS if kind in defaults):
        tool_ref = f"pi_tool_{kind}"
        nodes.append(NewNode(tool_ref, kind, label,
            position=(x - 420 + (index % 4) * 280, y + 240 + (index // 4) * 180)))
        edges.append(tool_edge(tool_ref, agent_ref))
    return GraphAdditions(nodes=tuple(nodes), edges=tuple(edges))
