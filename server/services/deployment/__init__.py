"""Deployment module - Event-driven workflow deployment.

Wave 13.2: side-effect import publishes the 5 deployment WS handlers
(deploy_workflow / cancel_deployment / get_deployment_status /
get_workflow_lock / update_deployment_settings) into the central
``ws_handler_registry``.
"""

from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .state import DeploymentState, TriggerInfo
from .triggers import TriggerManager
from .manager import DeploymentManager
from .handlers import WS_HANDLERS as _DEPLOYMENT_WS_HANDLERS

_register_ws_handlers(_DEPLOYMENT_WS_HANDLERS)

__all__ = [
    "DeploymentState",
    "TriggerInfo",
    "TriggerManager",
    "DeploymentManager",
]
