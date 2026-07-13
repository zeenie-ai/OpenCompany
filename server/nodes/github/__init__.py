"""Plugin for the 'vcs' palette group — GitHub via the gh CLI.

Self-contained CLI-managed-auth plugin (Stripe pattern): the gh CLI
owns its auth end-to-end — ``gh auth login --web`` driven from the
credentials modal (or the user's own terminal), token in the system
credential store, a synthetic ``cli-managed`` marker OAuth row for the
catalogue badge. OpenCompany never stores or injects a token. On login
success ``gh auth setup-git`` configures git's credential helper (the
official bridge), so a future git node needs zero auth code.
"""

from __future__ import annotations

from services.node_output_schemas import register_output_schema
from services.ws_handler_registry import register_ws_handlers

from ._credentials import GitHubCredential
from ._handlers import WS_HANDLERS
from .github_action import GitHubActionNode, GitHubActionOutput

register_ws_handlers(WS_HANDLERS)
register_output_schema("githubAction", GitHubActionOutput)

__all__ = [
    "GitHubCredential",
    "GitHubActionNode",
    "WS_HANDLERS",
]
