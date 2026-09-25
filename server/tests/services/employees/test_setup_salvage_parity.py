"""The server's "is this reply worth showing" check agrees with the
client's renderer on the shared corpus of model replies
(client/src/features/home/genui/__fixtures__/replies.json; the vitest
suite genui/__tests__/replies.test.ts runs the same file). If they
disagreed, the server would retry replies the client could show, or pass
on replies the client cannot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.employees.setup_reply import app_names, is_salvageable, parse_reply, repair_json

CORPUS = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "home" / "genui" / "__fixtures__" / "replies.json"


def _cases():
    if not CORPUS.exists():
        pytest.skip(f"client corpus not present: {CORPUS}")
    return json.loads(CORPUS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_salvage_matches_the_client(case):
    assert is_salvageable(case["reply"]) is case["salvageable"]


@pytest.mark.parametrize("case", [c for c in _cases() if "text" in c.get("expect", {})], ids=lambda case: case["name"])
def test_introduction_text_matches_the_client(case):
    assert parse_reply(case["reply"]).text == case["expect"]["text"]


def test_app_names_come_from_every_place_a_reply_names_an_app():
    clean = next(case for case in _cases() if case["name"] == "clean minified reply")
    assert app_names(clean["reply"]) == ["WhatsApp", "Google Calendar"]


def test_repair_closes_what_is_open():
    assert repair_json('{"a":{"b":[1,2') == {"a": {"b": [1]}}
    assert repair_json('{"a":"unterminated') is None


def test_nan_is_refused_like_json_parse():
    # The repair cuts back to before the value JSON.parse cannot read.
    assert parse_reply('{"text":"x","spec":{"elements":{"a":{"type":"Metric","props":{"value":NaN}}}}}').spec == {
        "elements": {"a": {"type": "Metric"}}
    }
