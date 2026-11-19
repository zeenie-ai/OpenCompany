"""Shared setup for the node tests.

A node that makes a paid API call books it through
``services.usage.record_api_usage``. Under these tests that recorder is a
stand-in (the ``api_usage`` fixture from ``tests/usage_fixtures.py``), so a
node test checks the call it made and never writes a usage row.
"""

import pytest


@pytest.fixture(autouse=True)
def booked_api_calls(api_usage):
    """Every node test runs with the API usage recorder replaced."""
    return api_usage
