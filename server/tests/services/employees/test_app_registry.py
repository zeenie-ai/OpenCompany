"""config/employee_apps.json against the real plugins.

The graph builder copies these templates into saved node parameters, so a
typo here is a broken employee at run time. Every node type must be a
registered plugin, every parameter a field of its Params model with a value
the model accepts, and every ``${trigger.<field>}`` a field that trigger
actually emits.
"""

from __future__ import annotations

import re

import pytest

from services.credential_registry import get_credential_registry
from services.employees import apps as apps_module
from services.employees.apps import (
    SIDE_EFFECTS,
    allowed_when_asking_first,
    app_for_node_type,
    get_apps,
    resolve_app,
)
from services.node_registry import get_node_class

PLACEHOLDER = re.compile(r"\$\{([a-z_]+)(?:\.([a-zA-Z0-9_]+))?\}")

# What each trigger's event data really contains (the plugin code, not the
# declared Output models, which differ: googleGmailReceive declares `from_`
# but emits `from`). Keep in step with the plugins when they change.
TRIGGER_EMITS = {
    "whatsappReceive": {
        "message_id", "sender", "sender_phone", "chat_id", "message_type", "text", "content",
        "timestamp", "is_group", "is_from_me", "group_info",
    },
    "whatsappBusinessReceive": {
        "message_id", "from", "wa_id", "profile_name", "timestamp", "type", "text",
        "phone_number_id", "display_phone_number", "media", "reply_to_message_id",
    },
    "telegramReceive": {
        "message_id", "chat_id", "chat_type", "chat_title", "from_id", "from_username",
        "from_first_name", "from_last_name", "is_bot", "text", "caption", "content_type", "date",
    },
    "discordReceive": {
        "account_id", "message_id", "channel_id", "channel_name", "guild_id", "guild_name", "is_dm",
        "author_id", "author_name", "author_display_name", "author_is_bot", "content", "timestamp",
    },
    "googleGmailReceive": {
        "message_id", "thread_id", "from", "to", "cc", "subject", "date", "snippet", "labels", "body",
    },
    "msMailReceive": {
        "message_id", "id", "conversation_id", "from", "from_name", "to", "subject", "body_preview",
        "body", "received", "is_read", "has_attachments", "web_link",
    },
    "emailReceive": {"raw", "message_id", "folder"},
}

OWNER_FIELDS = {"google_email", "microsoft_email", "email_address", "email_provider"}

# Sample values the builder would substitute, typed for the Params models.
SAMPLES = {
    "reply_text": "Hello from the employee",
    "reply_subject": "Re: your message",
    "owner.email_provider": "gmail",
}


def _placeholders(value):
    if isinstance(value, str):
        yield from PLACEHOLDER.finditer(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _placeholders(item)


def _substitute(value):
    if isinstance(value, str):
        def sub(match):
            key = match.group(1) + (f".{match.group(2)}" if match.group(2) else "")
            return SAMPLES.get(key, "sample")

        return PLACEHOLDER.sub(sub, value)
    return value


def _templates():
    for app in get_apps().values():
        for role in ("trigger", "reply", "notify_owner"):
            template = getattr(app, role)
            if template is not None:
                yield app, role, template
        for tool in app.tools:
            yield app, "tool", tool


TEMPLATES = list(_templates())
IDS = [f"{app.id}.{role}.{template.type}" for app, role, template in TEMPLATES]


@pytest.mark.parametrize(("app", "role", "template"), TEMPLATES, ids=IDS)
def test_template_node_types_are_registered(app, role, template):
    assert get_node_class(template.type) is not None, template.type


@pytest.mark.parametrize(("app", "role", "template"), TEMPLATES, ids=IDS)
def test_template_params_are_real_fields_with_valid_values(app, role, template):
    params_model = get_node_class(template.type).Params
    unknown = set(template.params) - set(params_model.model_fields)
    assert not unknown, f"{template.type} has no params {sorted(unknown)}"
    sample = {key: _substitute(value) for key, value in template.params.items()}
    params_model.model_validate(sample)


@pytest.mark.parametrize(("app", "role", "template"), TEMPLATES, ids=IDS)
def test_placeholders_name_real_fields(app, role, template):
    trigger_type = app.trigger.type if app.trigger else None
    sources = [template.params]
    if role == "trigger":
        sources.append({"prompt": template.prompt})
    for source in sources:
        for match in _placeholders(source):
            scope, field = match.group(1), match.group(2)
            if scope == "trigger":
                assert trigger_type in TRIGGER_EMITS, f"{app.id} uses ${{trigger.*}} without a known trigger"
                assert field in TRIGGER_EMITS[trigger_type], f"{trigger_type} emits no `{field}`"
            elif scope == "owner":
                assert field in OWNER_FIELDS, f"unknown owner field `{field}`"
            else:
                assert scope in {"reply_text", "reply_subject"} and field is None, match.group(0)


def test_triggers_are_deployable_and_replies_have_somewhere_to_go():
    from constants import WORKFLOW_TRIGGER_TYPES

    for app in get_apps().values():
        if app.trigger is None:
            continue
        assert app.trigger.type in WORKFLOW_TRIGGER_TYPES, app.id
        assert app.trigger.type in TRIGGER_EMITS, app.id
        assert app.reply is not None or app.notify_owner is not None, f"{app.id} can neither reply nor report"
        assert app.phrase("when") and app.phrase("waiting"), app.id


def test_every_app_is_a_consumer_connector():
    registry = get_credential_registry()
    for app in get_apps().values():
        provider = registry.get_provider(app.provider_id)
        assert provider is not None, app.provider_id
        assert provider.get("consumer_category"), f"{app.provider_id} is not listed in Normal-mode Connectors"


def test_side_effects_and_the_ask_first_rule():
    for app in get_apps().values():
        assert app.side_effects in SIDE_EFFECTS
        for tool in app.tools:
            assert tool.side_effects in SIDE_EFFECTS
    assert allowed_when_asking_first("read") and allowed_when_asking_first("write")
    assert not allowed_when_asking_first("send") and not allowed_when_asking_first("money")
    assert get_apps()["stripe"].tools[0].side_effects == "money"
    assert get_apps()["google_sheets"].tools[0].side_effects == "write"


@pytest.mark.parametrize(
    ("app", "tool"),
    [(app, tool) for app in get_apps().values() for tool in app.tools if tool.ask_first_params],
    ids=lambda value: getattr(value, "id", None) or getattr(value, "type", ""),
)
def test_ask_first_params_are_real_fields_and_make_the_tool_safe(app, tool):
    """The ask-first form of a tool is only offered for a send/money tool,
    and must itself be a valid node."""
    assert not allowed_when_asking_first(tool.side_effects), f"{app.id}: a read/write tool is never left out, so needs no ask-first form"
    params_model = get_node_class(tool.type).Params
    assert set(tool.ask_first_params) <= set(params_model.model_fields)
    params_model.model_validate({**tool.params, **tool.ask_first_params})


def test_the_web_browser_is_an_app_that_turns_read_only_under_ask_first():
    web = get_apps()["web"]
    assert web.provider_id == "browser" and web.trigger is None and web.reply is None
    (tool,) = web.tools
    assert (tool.type, tool.role, tool.side_effects) == ("browser", "browser", "money")
    assert dict(tool.params) == {"interaction": "full"}
    assert dict(tool.ask_first_params) == {"interaction": "read_only"}
    assert app_for_node_type("browser").id == "web"
    for name in ("Web browser", "browser", "Chrome", "our website", "the internet"):
        assert resolve_app(name).id == "web", name


def test_node_types_map_back_to_one_app():
    assert app_for_node_type("whatsappReceive").id == "whatsapp"
    assert app_for_node_type("whatsappSend").id == "whatsapp"
    assert app_for_node_type("googleGmail").id == "gmail"
    assert app_for_node_type("googleCalendar").id == "google_calendar"
    assert app_for_node_type("msMail").id == "outlook"
    assert app_for_node_type("aiAgent") is None


@pytest.mark.parametrize(
    ("name", "connected", "expected"),
    [
        ("WhatsApp", None, "whatsapp"),
        ("whatsapp business", None, "whatsapp_business"),
        ("Whats", None, "whatsapp"),
        ("Gmail", None, "gmail"),
        ("Google Calendar", None, "google_calendar"),
        ("calendar", None, "google_calendar"),
        ("calendar", ["outlook_calendar"], "outlook_calendar"),
        ("email", None, "email"),
        ("email", ["gmail"], "gmail"),
        ("Email", ["outlook"], "outlook"),
        ("gmail inbox", None, "gmail"),
        ("Sheets", None, "google_sheets"),
        ("Stripe payments", None, "stripe"),
        ("Telegram", None, "telegram"),
        ("Slack", None, None),
        ("", None, None),
        ("QuickBooks", None, None),
    ],
)
def test_resolve_app(name, connected, expected):
    app = resolve_app(name, connected)
    assert (app.id if app else None) == expected


_TOOL = '{"type": "browser", "side_effects": "money", %s}'


@pytest.mark.parametrize(
    "app_json",
    [
        '{"name": "X", "provider_id": "p", "side_effects": "dangerous"}',
        '{"name": "X", "provider_id": "p", "side_effects": "money", "tools": [%s]}' % (_TOOL % '"ask_first_params": ["read_only"]'),
        '{"name": "X", "provider_id": "p", "side_effects": "money", "tools": [%s]}' % (_TOOL % '"role": ""'),
    ],
    ids=["bad side effect", "ask_first_params not an object", "empty role"],
)
def test_a_malformed_registry_fails_loudly(tmp_path, monkeypatch, app_json):
    bad = tmp_path / "employee_apps.json"
    bad.write_text('{"apps": {"x": %s}}' % app_json, encoding="utf-8")
    monkeypatch.setattr(apps_module, "CONFIG_PATH", bad)
    apps_module.reload_apps()
    try:
        with pytest.raises(apps_module.AppRegistryError):
            get_apps()
    finally:
        monkeypatch.undo()
        apps_module.reload_apps()
