"""Plugins for the 'email' palette group.

Self-registers:
  - The trigger filter builder for ``emailReceive`` so
    ``services/event_waiter.py`` carries no plugin-specific code.
  - ``emailReceive`` into ``services.deployment.canary_registry`` so the
    Temporal-durable ``TriggerListenerWorkflow`` path activates when
    ``event_framework_enabled`` is on (default-true post-2026-05-15).
"""

from services.deployment.canary_registry import register_canary_trigger_type
from services.event_waiter import register_filter_builder

from ._events import dispatch_email_received  # noqa: F401 — re-export
from ._filters import build_filter as build_email_filter

register_filter_builder("emailReceive", build_email_filter)
register_canary_trigger_type("emailReceive", "com.opencompany.email.message.received")
