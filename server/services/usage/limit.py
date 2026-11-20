"""The two spend limits: one per chat run, and a monthly budget per employee.

A chat run is one reply. Its cost is its model calls plus its paid tool
calls. When that reaches the limit, the run stops and the agent says so; the
owner's next message starts a new run that carries on. The limit is the
owner's ``chat_run_spend_limit_usd`` setting when it is above 0, else
``CHAT_RUN_SPEND_LIMIT_USD``.

An employee is one workflow. Its budget counts everything booked for it this
month, team work and scheduled runs included. The budget is the owner's
``employee_monthly_budget_usd`` setting when it is above 0, else
``EMPLOYEE_MONTHLY_BUDGET_USD``. What happens at the budget (the employee is
paused) is the caller's: this module only answers whether it was reached.

0 means no limit, which is the default for both.

The agent's model step asks before each model call, so a run can pass a
limit by the cost of one turn. The per-reply limit covers the agent that
answers in chat: the step does not ask it for a managed employee job.
"""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from core.logging import get_logger
from services.usage.summaries import chat_run_cost, month_start, workflow_cost_since

logger = get_logger(__name__)

NOTICE = (
    "I stopped here because this reply reached the spend limit of ${limit:.2f} set in Settings. "
    "Ask me to continue and I'll carry on from where I stopped."
)

#: What the owner reads on an employee paused at its budget, and when a
#: Resume is refused because it is still over.
BUDGET_PAUSED = "Reached this month's budget of ${budget:.2f}. Raise it in Settings > Billing, then resume."

#: What the agent says in a reply the budget ended.
BUDGET_NOTICE = (
    "I stopped here because I reached this month's budget of ${budget:.2f}. "
    "Raise it in Settings > Billing, then resume me, and I'll carry on."
)


def _owner_or_environment(user_settings: Optional[Mapping[str, Any]], settings: Any, name: str) -> float:
    """A limit in USD: the owner's value when above 0, else the environment's."""
    owner = float((user_settings or {}).get(name) or 0)
    if owner > 0:
        return owner
    return float(getattr(settings, name))


def resolve_spend_limit(user_settings: Optional[Mapping[str, Any]], settings: Any) -> float:
    """The per-reply limit in USD. 0 means no limit."""
    return _owner_or_environment(user_settings, settings, "chat_run_spend_limit_usd")


async def spend_limit_notice(chat_run_id: str, *, database: Any = None, settings: Any = None) -> Optional[str]:
    """What the agent says when the run has reached its limit, else ``None``.

    A limit that cannot be read answers ``None`` and logs a warning: the step
    runs, because a failed check must not end the owner's reply. With no
    ``database`` or ``settings`` the container's are used.
    """
    try:
        from core.container import container  # looked up when called

        if database is None:
            database = container.database()
        if settings is None:
            settings = container.settings()
        limit = resolve_spend_limit(await database.get_user_settings(), settings)
        if limit <= 0:
            return None
        if await chat_run_cost(database, chat_run_id) < limit:
            return None
        return NOTICE.format(limit=limit)
    except Exception:
        logger.warning("Spend limit not checked", chat_run_id=chat_run_id, exc_info=True)
        return None


async def monthly_budget_reached(
    workflow_id: str, *, database: Any = None, settings: Any = None, now: Optional[datetime] = None
) -> Optional[float]:
    """The budget in USD, when the workflow's cost this month has reached it,
    else ``None``.

    The month starts at midnight on the 1st in the owner's time zone
    (``month_start``), the month the cost panel shows. A budget that cannot
    be read answers ``None`` and logs a warning, so the step runs. With no
    ``database``, ``settings`` or ``now`` the container's and the clock are
    used.
    """
    try:
        from core.container import container  # looked up when called

        if database is None:
            database = container.database()
        if settings is None:
            settings = container.settings()
        if now is None:
            now = datetime.now(timezone.utc)
        user_settings = await database.get_user_settings() or {}
        budget = _owner_or_environment(user_settings, settings, "employee_monthly_budget_usd")
        if budget <= 0:
            return None
        since = month_start(now, user_settings.get("profile_timezone"))
        if await workflow_cost_since(database, workflow_id, since) < budget:
            return None
        return budget
    except Exception:
        logger.warning("Monthly budget not checked", workflow_id=workflow_id, exc_info=True)
        return None
