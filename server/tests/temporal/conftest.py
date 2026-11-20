"""Shared setup for the Temporal tests.

The agent's model step asks ``services.usage.monthly_budget_reached`` whether
its employee has reached the owner's monthly budget. Under these tests that
check is a stand-in (the ``monthly_budget`` fixture from
``tests/usage_fixtures.py``), so a step test never reads the usage tables and
no employee is at its budget unless the test says so.
"""

import pytest


@pytest.fixture(autouse=True)
def no_monthly_budget(monthly_budget):
    """Every Temporal test runs with the budget check replaced."""
    return monthly_budget
