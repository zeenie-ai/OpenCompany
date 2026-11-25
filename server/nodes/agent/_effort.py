"""The ``effort`` option every native LLM agent declares (in its Options):
how hard the agent's model thinks, on every run.

``low`` (the chat picker's Quick) or ``high`` (Thorough) reaches the provider
only for a model listed in its ``effort_models`` (services/llm/unifier.py);
any other model answers as without it. Unset is Balanced: the model's
``default_effort`` in llm_defaults.json, else its own default. For the agent
that answers a chat run, a level the owner picked in the chat wins
(services/chat/choice.py).
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import Field

AgentEffort = Annotated[
    Optional[Literal["low", "high"]],
    Field(
        description="How hard the model thinks: low is quick, high is thorough. Unset, the model's own default.",
        json_schema_extra={"group": "options"},
    ),
]


__all__ = ["AgentEffort"]
