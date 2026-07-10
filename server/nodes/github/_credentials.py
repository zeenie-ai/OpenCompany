"""GitHub credential — thin marker (Stripe idiom).

The gh CLI manages its own auth state (system credential store,
populated by ``gh auth login`` and cleared by ``gh auth logout``).
MachinaOs stores no token — the credentials modal's connected badge is
driven by the synthetic ``cli-managed`` marker OAuth row written by
``_handlers.py`` after a successful login.
"""

from __future__ import annotations

from typing import Any, Dict

from services.plugin.credential import Credential


class GitHubCredential(Credential):
    id = "github"
    display_name = "GitHub"
    category = "Developer Tools"
    auth = "custom"
    docs_url = "https://cli.github.com"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Nothing to resolve — auth lives in the gh CLI's own
        credential store."""
        return {}
