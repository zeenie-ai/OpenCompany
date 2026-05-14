"""Plugins for the 'email' palette group.

Self-registers the trigger filter builder for ``emailReceive`` so the
central ``services/event_waiter.py`` carries no plugin-specific code.
"""

from services.event_waiter import register_filter_builder

from ._events import dispatch_email_received  # noqa: F401 — re-export
from ._filters import build_filter as build_email_filter

register_filter_builder("emailReceive", build_email_filter)
