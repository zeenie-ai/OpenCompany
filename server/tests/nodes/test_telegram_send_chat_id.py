"""Telegram Send takes the trigger's chat id as it comes: the receive
trigger emits an int, and a whole-string template keeps that type."""

import pytest
from pydantic import ValidationError

from nodes.telegram.telegram_send import TelegramSendParams


def test_an_int_chat_id_becomes_its_string():
    assert TelegramSendParams(recipient_type="user", chat_id=123456789).chat_id == "123456789"
    assert TelegramSendParams(recipient_type="group", chat_id=-1001234567890).chat_id == "-1001234567890"


def test_strings_pass_through_and_booleans_are_refused():
    assert TelegramSendParams(chat_id="@someone").chat_id == "@someone"
    with pytest.raises(ValidationError):
        TelegramSendParams(chat_id=True)
