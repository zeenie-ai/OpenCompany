"""Usage tracking: what model calls and paid API calls cost.

This package owns the two usage tables, ``token_usage_metrics`` (one row per
model call) and ``api_usage_metrics`` (one row per paid API call). Other code
calls in; nothing here imports chat, employee, compaction or Temporal code.
It depends on the price service, the models and a database session.

- ``summaries``: what was recorded, added up.
- ``handlers``: the WebSocket commands that read it. They register when
  ``main.py`` imports that module; importing this package registers nothing.
"""

from .summaries import api_usage_summary, provider_usage_summary

__all__ = ["api_usage_summary", "provider_usage_summary"]
