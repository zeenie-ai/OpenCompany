"""Fixtures for the usage tests: a real database on a throwaway file."""

import pytest


@pytest.fixture
def database(real_database):
    """The shared real database (``tests/database_fixtures.py``)."""
    return real_database
