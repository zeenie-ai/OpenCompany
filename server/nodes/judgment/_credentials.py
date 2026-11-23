"""The TypeSafe credential.

Jev through OpenRouter uses the OpenRouter key the owner already stores for
chat models (``nodes/model/_credentials.py``); a second class for that id
would collide in ``CREDENTIAL_REGISTRY``.
"""

from __future__ import annotations

from services.judgment import Question
from services.judgment import config as judgment_config
from services.plugin.credential import ApiKeyCredential

from ._providers.systemone import PATH, to_systemone


class TypeSafeCredential(ApiKeyCredential):
    """TypeSafe API key, sent as a Bearer token.

    The probe asks one noul, the cheapest call that proves the key works
    (a few input tokens, and output is free). It is not booked as usage.
    """

    id = "typesafe"
    display_name = "TypeSafe"
    category = "AI"
    key_location = "bearer"
    docs_url = "https://docs.typesafe.ai"
    probe_method = "POST"
    probe_url = judgment_config.base_url("typesafe") + PATH
    probe_json = to_systemone(
        judgment_config.model("typesafe"),
        "Connection test.",
        {"ok": Question(type="noul", description="This text is a connection test.")},
    )


__all__ = ["TypeSafeCredential"]
