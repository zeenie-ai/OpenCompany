"""Unit tests for ``services.workflow_naming``.

Locks the contract for the slug allocator that derives human-readable
workflow identifiers (``AI_Assistant_1``) from arbitrary display names.
The slug surfaces as the on-disk workspace folder, the Temporal Web UI
prefix, and the sidebar label — so regressions here have visible
operator + filesystem impact.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Tuple

import pytest

from services.workflow_naming import (
    next_available_slug,
    slugify_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_db(rows: List[Tuple[str, str]]):
    """Minimal duck-typed Database exposing ``list_workflow_slugs``."""

    async def list_workflow_slugs():
        return list(rows)

    return SimpleNamespace(list_workflow_slugs=list_workflow_slugs)


# ---------------------------------------------------------------------------
# slugify_name — every shape the user might type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("AI Assistant", "AI_Assistant"),
        ("Test/Workflow:Beta!", "Test_Workflow_Beta"),
        ("Hello World", "Hello_World"),
        ("already_snake", "already_snake"),
        ("UPPER", "UPPER"),
        ("lower", "lower"),
        ("MixedCase Workflow 3", "MixedCase_Workflow_3"),
        ("  leading/trailing  ", "leading_trailing"),
        ("dot.separated.name", "dot_separated_name"),
        ("colon:separated", "colon_separated"),
        ("a-b-c", "a_b_c"),
    ],
)
def test_slugify_name_ascii(name: str, expected: str) -> None:
    assert slugify_name(name) == expected


@pytest.mark.parametrize(
    "name",
    ["", "   ", "!!!", "@#$%", "...", "___", "  -  "],
)
def test_slugify_name_falls_back_when_empty(name: str) -> None:
    assert slugify_name(name) == "Workflow"


def test_slugify_name_truncates_long_input() -> None:
    long_name = "a" * 200
    assert slugify_name(long_name) == "a" * 50


def test_slugify_name_preserves_case() -> None:
    # python-slugify lowercases by default; we explicitly disable that.
    assert slugify_name("AI Assistant") == "AI_Assistant"
    assert slugify_name("AI Assistant") != "ai_assistant"


def test_slugify_name_strips_emoji() -> None:
    assert slugify_name("Hello World 🚀") == "Hello_World"


def test_slugify_name_transliterates_unicode() -> None:
    # python-slugify pulls in text-unidecode by default — non-ASCII names
    # transliterate to ASCII instead of falling back to "Workflow_N".
    # Exact transliteration depends on the lib version; assert ASCII +
    # non-empty + not the bare fallback.
    result = slugify_name("日本語")
    assert result != "Workflow"
    assert result.isascii()
    assert result.replace("_", "").isalnum()


def test_slugify_name_none_input() -> None:
    # Defensive — callers should pass strings, but the helper must not
    # raise on None.
    assert slugify_name(None) == "Workflow"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# next_available_slug — fill-gap counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_available_slug_first_creation_gets_underscore_1() -> None:
    db = _fake_db([])
    assert await next_available_slug("AI Assistant", db) == "AI_Assistant_1"


@pytest.mark.asyncio
async def test_next_available_slug_increments_on_collision() -> None:
    db = _fake_db([("u1", "AI_Assistant_1")])
    assert await next_available_slug("AI Assistant", db) == "AI_Assistant_2"


@pytest.mark.asyncio
async def test_next_available_slug_fills_gaps() -> None:
    # AI_Assistant_2 was deleted; next allocation reuses the slot.
    db = _fake_db([("u1", "AI_Assistant_1"), ("u3", "AI_Assistant_3")])
    assert await next_available_slug("AI Assistant", db) == "AI_Assistant_2"


@pytest.mark.asyncio
async def test_next_available_slug_skips_unrelated_prefixes() -> None:
    # Other workflows with different slug bases must not bump our counter.
    db = _fake_db([
        ("u1", "Cool_Bot_1"),
        ("u2", "Cool_Bot_2"),
        ("u3", "Different_3"),
    ])
    assert await next_available_slug("AI Assistant", db) == "AI_Assistant_1"


@pytest.mark.asyncio
async def test_next_available_slug_ignores_non_numeric_suffix() -> None:
    # Slugs that share the prefix but have a non-digit suffix shouldn't
    # mess with the counter (defensive — shouldn't happen in practice).
    db = _fake_db([("u1", "AI_Assistant_X"), ("u2", "AI_Assistant_1")])
    assert await next_available_slug("AI Assistant", db) == "AI_Assistant_2"


@pytest.mark.asyncio
async def test_next_available_slug_exclude_id_skips_self() -> None:
    # Rename path: a workflow renaming itself to a name whose slug base
    # is unchanged must NOT bump itself to _2 over its own existing slug.
    db = _fake_db([("self_uuid", "AI_Assistant_1"), ("other_uuid", "AI_Assistant_2")])
    result = await next_available_slug(
        "AI Assistant", db, exclude_id="self_uuid",
    )
    # _1 is free now (because we're excluding self) — fill-gap returns _1.
    assert result == "AI_Assistant_1"


@pytest.mark.asyncio
async def test_next_available_slug_empty_name_uses_fallback_base() -> None:
    db = _fake_db([])
    assert await next_available_slug("", db) == "Workflow_1"
    assert await next_available_slug("!!!", db) == "Workflow_1"


@pytest.mark.asyncio
async def test_next_available_slug_special_chars_in_name() -> None:
    db = _fake_db([])
    assert await next_available_slug("Test/Workflow:Beta!", db) == "Test_Workflow_Beta_1"
