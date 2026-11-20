"""Usage tracking: what model calls and paid API calls cost.

This package owns the two usage tables, ``token_usage_metrics`` (one row per
model call) and ``api_usage_metrics`` (one row per paid API call). Other code
calls in; nothing here imports chat, employee, compaction or Temporal code.
It depends on the price service, the models and a database session. A summary
may join another table through its model, as ``chat_run_costs`` joins the
employee jobs a reply started.

- ``recording``: one row per call. A recorder never raises: the call it
  records already happened and was paid for.
- ``summaries``: what was recorded, added up.
- ``limit``: the spend limit of a chat run, and the monthly budget of an
  employee.
- ``handlers``: the WebSocket commands that read it. They register when
  ``main.py`` imports that module; importing this package registers nothing.

Callers import a recorder inside the function that calls it
(``from services.usage import record_api_usage``), so the name is read when
the call runs.
"""

from .limit import BUDGET_NOTICE, BUDGET_PAUSED, monthly_budget_reached, resolve_spend_limit, spend_limit_notice
from .recording import record_api_usage, record_llm_usage
from .summaries import (
    api_usage_summary,
    chat_run_cost,
    chat_run_costs,
    month_start,
    provider_usage_summary,
    workflow_cost_since,
    workflow_costs,
)

__all__ = [
    "BUDGET_NOTICE",
    "BUDGET_PAUSED",
    "api_usage_summary",
    "chat_run_cost",
    "chat_run_costs",
    "month_start",
    "monthly_budget_reached",
    "provider_usage_summary",
    "record_api_usage",
    "record_llm_usage",
    "resolve_spend_limit",
    "spend_limit_notice",
    "workflow_cost_since",
    "workflow_costs",
]
