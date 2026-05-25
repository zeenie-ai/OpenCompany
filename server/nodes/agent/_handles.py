"""Shared handle topology for agent plugins.

Local to ``nodes/agent/`` — not a global lookup. Each agent class
imports and uses what it needs.
"""

from __future__ import annotations


STD_SIZE = {"width": 300, "height": 200}
STD_AGENT_HINTS = {**STD_SIZE, "hasSkills": True}


def std_agent_handles() -> tuple:
    """Skill + Tool + Memory + Task + Input + Output — shared by 18
    of the 20 agent variants. Returned as a tuple so the BaseNode
    `handles` class attribute is immutable."""
    return (
        {"name": "input-skill", "kind": "input", "position": "bottom", "offset": "25%", "label": "Skill", "role": "skill"},
        {"name": "input-tools", "kind": "input", "position": "bottom", "offset": "75%", "label": "Tool", "role": "tools"},
        {"name": "input-main", "kind": "input", "position": "left", "offset": "25%", "label": "Input", "role": "main"},
        {"name": "input-memory", "kind": "input", "position": "left", "offset": "50%", "label": "Memory", "role": "memory"},
        {"name": "input-task", "kind": "input", "position": "left", "offset": "75%", "label": "Task", "role": "task"},
        {"name": "output-main", "kind": "output", "position": "right", "offset": "50%", "label": "Output", "role": "main"},
        {"name": "output-top", "kind": "output", "position": "top", "label": "Output", "role": "main"},
    )


def team_lead_agent_handles() -> tuple:
    """Skill / Tool / Teammates bottom + Memory/Task/Input left + Outputs.
    Used by orchestrator_agent and ai_employee."""
    return (
        {"name": "input-skill", "kind": "input", "position": "bottom", "offset": "20%", "label": "Skill", "role": "skill"},
        {"name": "input-tools", "kind": "input", "position": "bottom", "offset": "50%", "label": "Tool", "role": "tools"},
        {"name": "input-teammates", "kind": "input", "position": "bottom", "offset": "80%", "label": "Team", "role": "teammates"},
        {"name": "input-main", "kind": "input", "position": "left", "offset": "25%", "label": "Input", "role": "main"},
        {"name": "input-memory", "kind": "input", "position": "left", "offset": "50%", "label": "Memory", "role": "memory"},
        {"name": "input-task", "kind": "input", "position": "left", "offset": "75%", "label": "Task", "role": "task"},
        {"name": "output-main", "kind": "output", "position": "right", "offset": "50%", "label": "Output", "role": "main"},
        {"name": "output-top", "kind": "output", "position": "top", "label": "Output", "role": "main"},
    )
