"""The monthly budget per employee (``services/usage/limit.py``).

Run against real SQLite. The budget is the owner's
``employee_monthly_budget_usd`` setting when above 0, else the environment's;
0 means no budget.
"""


async def test_the_owners_budget_is_saved_and_read_back(database):
    await database.save_user_settings({"employee_monthly_budget_usd": 20.0})

    assert (await database.get_user_settings())["employee_monthly_budget_usd"] == 20.0


async def test_an_owner_who_set_none_has_no_budget(database):
    await database.save_user_settings({})

    assert (await database.get_user_settings())["employee_monthly_budget_usd"] == 0
