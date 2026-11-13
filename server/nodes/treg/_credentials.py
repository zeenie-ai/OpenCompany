"""treg credential: a marker only, like GitHub and Google Cloud.

The treg CLI owns its sign-in. ``treg login`` stores the team key in the
CLI's own config file, which OpenCompany pins under its data folder
(``<DATA_DIR>/treg/config.json``, see ``_service.treg_env``). OpenCompany
never reads or stores that key: the Credentials modal's connected badge
comes from the synthetic ``cli-managed`` marker ``_handlers.py`` writes
after a sign-in (``services/cli_agent/_cli_auth.py``).
"""

from __future__ import annotations

from typing import Any, Dict

from services.plugin.credential import Credential


class TregCredential(Credential):
    id = "treg"
    display_name = "treg"
    category = "Scrapers"
    auth = "custom"
    docs_url = "https://treg.to/docs"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Nothing to resolve: the key stays in the treg CLI's own config."""
        return {}

    @classmethod
    async def is_configured(cls, auth_service: Any, parameters: Dict[str, Any]) -> bool:
        """Signed in when the sign-in marker is stored. The base class looks
        for an API key, which a CLI sign-in never stores."""
        return await auth_service.get_oauth_tokens(cls.id) is not None
