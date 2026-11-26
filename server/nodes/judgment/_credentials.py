"""The TypeSafe credential.

Jev through OpenRouter uses the OpenRouter key the owner already stores for
chat models (``nodes/model/_credentials.py``); a second class for that id
would collide in ``CREDENTIAL_REGISTRY``.
"""

from __future__ import annotations

import httpx

from services.judgment import config as judgment_config
from services.plugin.credential import ApiKeyCredential, ProbeResult

#: TypeSafe's list of the models a key can ask (``GET /v1/models``).
MODELS_PATH = "/models"


class TypeSafeCredential(ApiKeyCredential):
    """TypeSafe API key, sent as a Bearer token.

    The check lists the key's models, which costs nothing; a judgment would
    be billed. A refused key gets 401, and an answer without a model list is
    not taken for a valid key.
    """

    id = "typesafe"
    display_name = "TypeSafe"
    category = "AI"
    key_location = "bearer"
    docs_url = "https://docs.typesafe.ai"
    probe_url = judgment_config.base_url("typesafe") + MODELS_PATH

    @classmethod
    def _handle_probe_response(cls, response: httpx.Response) -> ProbeResult:
        try:
            models = response.json().get("models")
        except (ValueError, AttributeError):
            models = None
        if not isinstance(models, list):
            return ProbeResult(valid=False, message="TypeSafe's answer listed no models, so the key could not be checked.")
        names = [str(model["name"]) for model in models if isinstance(model, dict) and model.get("name")]
        return ProbeResult(valid=True, message=f"{cls.display_name} API key is valid", models=names)


__all__ = ["MODELS_PATH", "TypeSafeCredential"]
