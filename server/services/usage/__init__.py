"""Usage tracking: what model calls and paid API calls cost.

This package owns the two usage tables, ``token_usage_metrics`` (one row per
model call) and ``api_usage_metrics`` (one row per paid API call). Other code
calls in; nothing here imports chat, employee, compaction or Temporal code.
It depends on the price service, the models and a database session.

- ``recording``: one row per call. A recorder never raises: the call it
  records already happened and was paid for.
- ``summaries``: what was recorded, added up.
- ``handlers``: the WebSocket commands that read it. They register when
  ``main.py`` imports that module; importing this package registers nothing.

Callers import a recorder inside the function that calls it
(``from services.usage import record_api_usage``), so the name is read when
the call runs.
"""

from .recording import record_api_usage, record_llm_usage
from .summaries import api_usage_summary, chat_run_costs, month_start, provider_usage_summary, workflow_costs

__all__ = [
    "api_usage_summary",
    "chat_run_costs",
    "month_start",
    "provider_usage_summary",
    "record_api_usage",
    "record_llm_usage",
    "workflow_costs",
]
