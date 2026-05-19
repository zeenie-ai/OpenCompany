"""Unit tests for ``services.plugin.identifiers``.

Locks the contract for ``NODE_TYPE_PATTERN`` / ``is_valid_node_type``
— the input sanitizer that gates URL-facing plugin lookups
(``GET /api/schemas/nodes/{node_type}/icon`` etc.). A regression here
would re-open the ``py/path-injection`` CodeQL alerts that fixed
``server/nodes/_visuals.py:get_plugin_icon_path`` cleared.
"""

from __future__ import annotations

import re

import pytest

from services.plugin.identifiers import NODE_TYPE_PATTERN, is_valid_node_type


# ---------------------------------------------------------------------------
# Positive cases — every shape registered ``BaseNode.type`` values take
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        # Real-world examples from the registry.
        "aiAgent",
        "openaiChatModel",
        "anthropicChatModel",
        "geminiChatModel",
        "claude_code_agent",
        "browser",
        "gmaps_create",
        "gmaps_nearby_places",
        "whatsappSend",
        "twitterReceive",
        "fileRead",
        # Edge shapes still inside the contract.
        "A",
        "_leading_underscore",
        "x1",
        "snake_case_lots_of_underscores",
        "camelCaseMixed123",
    ],
)
def test_accepts_valid_identifiers(value: str) -> None:
    assert is_valid_node_type(value) is True


# ---------------------------------------------------------------------------
# Negative cases — every shape an attacker might try via URL injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",                              # empty
        "1leading_digit",                # digit prefix
        "with space",                    # whitespace
        "with-hyphen",                   # hyphen (not a Python identifier char)
        "with.dot",                      # dot (path separator on URLs)
        "with/slash",                    # path separator
        "with\\backslash",               # Windows separator
        "..",                            # parent-dir token
        "../etc/passwd",                 # classic traversal
        "..\\windows\\system32",          # Windows traversal
        "foo\x00bar",                    # null byte truncation
        "foo\nbar",                      # newline / CRLF injection
        "foo\rbar",
        "%2e%2e%2fetc",                  # URL-encoded traversal
        " leading_space",
        "trailing_space ",
        "tab\there",
        "foo;bar",                       # command separator
        "foo|bar",                       # pipe
        "$VAR",                          # shell expansion
        "${HOME}",
        "foo'bar",                       # quote
        "foo\"bar",
        "foo`bar",                       # backtick (command substitution)
    ],
)
def test_rejects_traversal_and_injection_vectors(value: str) -> None:
    assert is_valid_node_type(value) is False


# ---------------------------------------------------------------------------
# Defensive type-check — URL params can be ``None`` if a typed dict
# decoder hands forward a missing key.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, 123, b"aiAgent", ["aiAgent"], object()])
def test_rejects_non_string(value) -> None:
    assert is_valid_node_type(value) is False


# ---------------------------------------------------------------------------
# Wire-format invariant — the pattern string is consumed verbatim by
# FastAPI's ``Path(pattern=...)`` constraint in routers/schemas.py.
# Re-compiling here proves the string is a valid regex and verifies the
# two consumers (FastAPI side + Python side) match on a few canonical
# values.
# ---------------------------------------------------------------------------


def test_pattern_string_is_valid_regex() -> None:
    compiled = re.compile(NODE_TYPE_PATTERN)
    assert compiled.fullmatch("aiAgent") is not None
    assert compiled.fullmatch("../etc/passwd") is None


def test_pattern_string_anchors_full_match_only() -> None:
    """``fullmatch`` semantics — the ``^`` / ``$`` anchors must not
    allow ``re.search`` to find the pattern as a substring of a hostile
    URL value. FastAPI's ``pattern=`` uses ``re.match`` (anchored at
    start only), but the ``$`` anchor blocks suffix injection."""
    compiled = re.compile(NODE_TYPE_PATTERN)
    assert compiled.fullmatch("aiAgent/../etc/passwd") is None
    assert compiled.fullmatch("aiAgent\x00../etc") is None
