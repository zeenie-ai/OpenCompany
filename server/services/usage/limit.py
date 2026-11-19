"""The spend limit of a chat run.

A chat run is one reply. Its cost is its model calls plus its paid tool
calls. When that reaches the limit, the run stops and the agent says so; the
owner's next message starts a new run that carries on.

The limit is the owner's ``chat_run_spend_limit_usd`` setting when it is
above 0, else ``CHAT_RUN_SPEND_LIMIT_USD``. 0 means no limit, which is the
default.

The agent's model step asks :func:`spend_limit_notice` before each model
call, so a run can pass the limit by the cost of one turn. A managed employee
job is never limited: the step does not ask for one.
"""

from typing import Any, Mapping, Optional

from core.logging import get_logger
from services.usage.summaries import chat_run_cost

logger = get_logger(__name__)

NOTICE = (
    "I stopped here because this reply reached the spend limit of ${limit:.2f} set in Settings. "
    "Ask me to continue and I'll carry on from where I stopped."
)


def resolve_spend_limit(user_settings: Optional[Mapping[str, Any]], settings: Any) -> float:
    """The limit in USD: the owner's value when above 0, else the
    environment's. 0 means no limit."""
    owner = float((user_settings or {}).get("chat_run_spend_limit_usd") or 0)
    if owner > 0:
        return owner
    return float(settings.chat_run_spend_limit_usd)


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
