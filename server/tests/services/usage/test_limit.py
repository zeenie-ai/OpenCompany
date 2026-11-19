"""The spend limit of a chat run (``services/usage/limit.py``).

Run against real SQLite: the limit is the owner's setting when above 0, else
the environment's, and a run reaches it on the cost of its own model calls
and paid tool calls.
"""

import re
from pathlib import Path
from types import SimpleNamespace

from models.database import UserSettings
from services.usage import resolve_spend_limit, spend_limit_notice
from tests.services.usage._helpers import add_api_call, add_model_call, unavailable

REPO_ROOT = Path(__file__).resolve().parents[4]
NO_ENV_LIMIT = SimpleNamespace(chat_run_spend_limit_usd=0.0)
TWO_DOLLARS = (
    "I stopped here because this reply reached the spend limit of $2.00 set in Settings. "
    "Ask me to continue and I'll carry on from where I stopped."
)


async def owner_sets(database, limit: float) -> None:
    await database.save_user_settings({"chat_run_spend_limit_usd": limit})


async def notice_for(database, run_id: str = "r_1", settings=NO_ENV_LIMIT):
    return await spend_limit_notice(run_id, database=database, settings=settings)


# ---- which limit applies -----------------------------------------------------


def test_there_is_no_limit_until_one_is_set():
    assert resolve_spend_limit(None, NO_ENV_LIMIT) == 0


def test_the_environment_sets_a_limit_for_an_owner_who_set_none():
    assert resolve_spend_limit({"chat_run_spend_limit_usd": 0}, SimpleNamespace(chat_run_spend_limit_usd=5.0)) == 5.0


def test_the_owners_limit_beats_the_environments():
    assert resolve_spend_limit({"chat_run_spend_limit_usd": 2.0}, SimpleNamespace(chat_run_spend_limit_usd=5.0)) == 2.0


# ---- reaching it -------------------------------------------------------------


async def test_a_run_is_never_stopped_while_no_limit_is_set(database):
    await add_model_call(database, chat_run_id="r_1", total_cost=500.0)

    assert await notice_for(database) is None


async def test_a_run_under_its_limit_is_not_stopped(database):
    await owner_sets(database, 2.0)
    await add_model_call(database, chat_run_id="r_1", total_cost=1.5)

    assert await notice_for(database) is None


async def test_a_run_that_reached_its_limit_gets_the_notice(database):
    await owner_sets(database, 2.0)
    await add_model_call(database, chat_run_id="r_1", total_cost=1.5)
    await add_model_call(database, chat_run_id="r_1", total_cost=0.5)

    assert await notice_for(database) == TWO_DOLLARS


async def test_paid_tool_calls_count_toward_the_limit(database):
    await owner_sets(database, 2.0)
    await add_model_call(database, chat_run_id="r_1", total_cost=1.5)
    await add_api_call(database, chat_run_id="r_1", cost=0.75)

    assert await notice_for(database) == TWO_DOLLARS


async def test_another_runs_cost_does_not_count(database):
    await owner_sets(database, 2.0)
    await add_model_call(database, chat_run_id="r_1", total_cost=0.5)
    await add_model_call(database, chat_run_id="r_2", total_cost=9.0)
    await add_api_call(database, chat_run_id="r_2", cost=9.0)

    assert await notice_for(database, "r_1") is None


async def test_the_environments_limit_stops_a_run_too(database):
    await add_model_call(database, chat_run_id="r_1", total_cost=2.5)

    notice = await notice_for(database, settings=SimpleNamespace(chat_run_spend_limit_usd=2.0))

    assert notice == TWO_DOLLARS


async def test_a_limit_that_cannot_be_read_lets_the_step_run():
    broken = SimpleNamespace(get_user_settings=unavailable, get_session=unavailable)

    assert await notice_for(broken) is None


# ---- the default -------------------------------------------------------------


def test_every_copy_of_the_default_is_no_limit():
    """The default is written in three places: ``.env.template`` is canonical,
    ``Settings`` mirrors it for bare construction, and ``UserSettings`` for a
    new row. They cannot share a constant, so this test keeps them equal."""
    template = (REPO_ROOT / ".env.template").read_text(encoding="utf-8")
    config = (REPO_ROOT / "server" / "core" / "config.py").read_text(encoding="utf-8")

    env_match = re.search(r"^CHAT_RUN_SPEND_LIMIT_USD=([\d.]+)\s*$", template, re.M)
    config_match = re.search(r'chat_run_spend_limit_usd: float = Field\(default=([\d.]+), env="CHAT_RUN_SPEND_LIMIT_USD"', config)
    assert env_match and config_match

    copies = {
        ".env.template": float(env_match.group(1)),
        "Settings": float(config_match.group(1)),
        "UserSettings": float(UserSettings.model_fields["chat_run_spend_limit_usd"].default),
    }
    assert set(copies.values()) == {0.0}, copies
