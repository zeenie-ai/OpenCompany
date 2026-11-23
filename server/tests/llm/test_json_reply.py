"""A chat model's reply that should be one JSON object
(``services.llm.json_reply.parse_json_object``), and translate's LLM
provider, which reads its replies through it."""

import pytest

from services.llm.json_reply import parse_json_object


def test_a_json_object_is_read():
    assert parse_json_object('{"answer": 1}') == {"answer": 1}


def test_a_code_fence_around_the_object_is_tolerated():
    assert parse_json_object('```json\n{"answer": 1}\n```') == {"answer": 1}


def test_whitespace_around_the_object_is_tolerated():
    assert parse_json_object('\n  {"answer": 1}  \n') == {"answer": 1}


def test_a_reply_that_is_not_json_is_a_value_error():
    with pytest.raises(ValueError, match="model did not return JSON: Sure, here you go"):
        parse_json_object("Sure, here you go")


def test_json_that_is_not_an_object_is_a_value_error():
    with pytest.raises(ValueError, match="model returned list, expected an object"):
        parse_json_object("[1, 2]")


def test_an_empty_reply_is_a_value_error():
    with pytest.raises(ValueError, match="model did not return JSON"):
        parse_json_object(None)


def test_translate_reports_a_reply_that_is_not_json_as_its_own_error():
    from nodes.translate._protocol import TranslateError, TranslateErrorCategory
    from nodes.translate._providers.llm import _parse_json_object

    with pytest.raises(TranslateError) as caught:
        _parse_json_object("no json here", "openai")
    assert caught.value.provider == "openai"
    assert caught.value.category is TranslateErrorCategory.SERVER
    assert caught.value.message == "model did not return JSON: no json here"


def test_translate_still_reads_a_fenced_reply():
    from nodes.translate._providers.llm import _parse_json_object

    assert _parse_json_object('```\n{"translations": ["hola"]}\n```', "openai") == {"translations": ["hola"]}
