"""Credential-management WS handlers — Wave 13.5 extraction.

Side-effect import registers the 4 credential CRUD handlers
(validate_api_key / get_stored_api_key / save_api_key / delete_api_key)
into ``ws_handler_registry``. The per-provider validation logic lives
in ``services/plugin/credential.py``'s ``CREDENTIAL_REGISTRY``; these
handlers just shape the request + response envelope around it.
"""

from __future__ import annotations

from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .handlers import WS_HANDLERS as _CREDENTIALS_WS_HANDLERS

_register_ws_handlers(_CREDENTIALS_WS_HANDLERS)

from .enrollment import WS_HANDLERS as _SOURCE_WS_HANDLERS
_register_ws_handlers(_SOURCE_WS_HANDLERS)

__all__ = ["handlers"]
