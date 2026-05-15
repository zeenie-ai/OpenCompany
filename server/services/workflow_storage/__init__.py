"""Workflow storage WS handlers — Wave 13.7 extraction.

Side-effect import registers the 5 workflow storage CRUD handlers
(save_workflow / import_workflow / get_workflow / get_all_workflows /
delete_workflow) into ``ws_handler_registry``. Storage backend lives
in ``core.database``; this package owns the request/response envelope
shape.
"""

from __future__ import annotations

from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .handlers import WS_HANDLERS as _WORKFLOW_STORAGE_WS_HANDLERS

_register_ws_handlers(_WORKFLOW_STORAGE_WS_HANDLERS)

__all__ = ["handlers"]
