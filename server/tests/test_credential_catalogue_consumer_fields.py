"""Normal-mode fields on the credential catalogue, and the shared connection
state (``services.credential_registry.provider_connection_state``).

Normal mode's Connectors grid lists only providers that declare a
``consumer_category`` and shows their short ``description``. ``connected``
answers "is this app usable right now": the same as ``stored`` unless a
provider declares a ``connected_check`` (WhatsApp's live pairing, the
IMAP/SMTP account's keys).
"""

from __future__ import annotations

import pytest

from services import credential_registry as registry_module
from services.credential_registry import CredentialRegistry, provider_connection_state
from services.status_broadcaster import get_status_broadcaster

MAX_DESCRIPTION = 90


@pytest.fixture()
def registry() -> CredentialRegistry:
    reg = CredentialRegistry()
    yield reg


@pytest.fixture()
def whatsapp_slot():
    """Set / restore the WhatsApp plugin's live status slot."""
    status = get_status_broadcaster()._status
    had = "whatsapp" in status
    saved = status.get("whatsapp")

    def set_connected(connected: bool) -> None:
        status["whatsapp"] = {"connected": connected}

    yield set_connected
    if had:
        status["whatsapp"] = saved
    else:
        status.pop("whatsapp", None)


class FakeAuth:
    def __init__(self, keys=(), oauth=None):
        self.keys = set(keys)
        self.oauth = dict(oauth or {})

    async def has_valid_key(self, key: str) -> bool:
        return key in self.keys

    async def get_oauth_tokens(self, provider: str):
        return self.oauth.get(provider)


class TestCatalogueFields:
    def test_consumer_categories_are_ordered(self, registry):
        assert [c["key"] for c in registry.get_consumer_categories()] == ["messages", "organize", "business", "ai"]

    def test_every_consumer_provider_has_a_known_category_and_a_short_description(self, registry):
        known = {c["key"] for c in registry.get_consumer_categories()}
        listed = [p for p in registry.get_all_providers() if p.get("consumer_category")]
        assert listed, "no provider declares a consumer_category"
        for provider in listed:
            assert provider["consumer_category"] in known, provider["id"]
            description = provider.get("description")
            assert isinstance(description, str) and 0 < len(description) <= MAX_DESCRIPTION, provider["id"]

    def test_llm_providers_inherit_the_ai_category_and_deepl_opts_out(self, registry):
        assert registry.get_provider("openai")["consumer_category"] == "ai"
        assert registry.get_provider("anthropic")["consumer_category"] == "ai"
        assert registry.get_provider("deepl").get("consumer_category") is None

    @pytest.mark.parametrize(
        ("pid", "category"),
        [
            ("whatsapp", "messages"),
            ("whatsapp_business", "messages"),
            ("telegram", "messages"),
            ("discord", "messages"),
            ("email_himalaya", "messages"),
            ("google", "organize"),
            ("microsoft", "organize"),
            ("stripe", "business"),
            ("ollama", "ai"),
        ],
    )
    def test_v1_apps_are_listed(self, registry, pid, category):
        assert registry.get_provider(pid)["consumer_category"] == category

    def test_editor_only_providers_stay_hidden(self, registry):
        for pid in ("claude_code", "codex_cli", "openai_compatible", "android_remote", "apify", "github"):
            assert not registry.get_provider(pid).get("consumer_category"), pid

    def test_connected_checks_are_well_formed(self, registry):
        for provider in registry.get_all_providers():
            check = provider.get("connected_check")
            if not check:
                continue
            assert check["type"] in {"status", "api_keys"}, provider["id"]
            if check["type"] == "status":
                assert check.get("key") and check.get("field"), provider["id"]
            else:
                assert check.get("keys"), provider["id"]

    def test_catalogue_payload_carries_consumer_categories(self, registry):
        assert registry.get_catalogue()["consumer_categories"] == registry.get_consumer_categories()

    def test_every_backend_icon_url_resolves(self, registry):
        """An ``icon_ref`` on a backend icon route must name something that
        route can serve, or the Connectors card shows a broken image (the
        IMAP/SMTP account once pointed at a credential class that does not
        exist)."""
        import re

        import nodes  # noqa: F401 - registers every node type and credential class
        from nodes._visuals import get_plugin_icon_path
        from services.plugin.credential import CREDENTIAL_REGISTRY

        for provider in registry.get_all_providers():
            ref = provider.get("icon_ref") or ""
            if match := re.fullmatch(r"/api/schemas/credentials/(\w+)/icon", ref):
                credential = CREDENTIAL_REGISTRY.get(match.group(1))
                assert credential is not None and credential.get_icon_path() is not None, provider["id"]
            elif match := re.fullmatch(r"/api/schemas/nodes/(\w+)/icon", ref):
                assert get_plugin_icon_path(match.group(1)) is not None, provider["id"]


class TestProviderConnectionState:
    async def test_api_key_provider(self, registry):
        state = await provider_connection_state(registry.get_provider("openai"), FakeAuth(keys={"openai"}))
        assert state["stored"] is True and state["connected"] is True
        state = await provider_connection_state(registry.get_provider("openai"), FakeAuth())
        assert state["stored"] is False and state["connected"] is False

    async def test_email_account_is_stored_and_connected_only_with_both_keys(self, registry):
        provider = registry.get_provider("email_himalaya")
        state = await provider_connection_state(provider, FakeAuth(keys={"email_address"}))
        assert state == {"stored": False, "connected": False, "account_label": None}
        state = await provider_connection_state(provider, FakeAuth(keys={"email_address", "email_password"}))
        assert state["stored"] is True and state["connected"] is True

    async def test_whatsapp_connected_follows_the_live_pairing(self, registry, whatsapp_slot):
        provider = registry.get_provider("whatsapp")
        auth = FakeAuth(oauth={"whatsapp": {"name": "Shop phone"}})
        whatsapp_slot(False)
        state = await provider_connection_state(provider, auth)
        assert state["stored"] is True
        assert state["connected"] is False
        whatsapp_slot(True)
        assert (await provider_connection_state(provider, auth))["connected"] is True

    async def test_telegram_stored_check_reads_the_bot_token(self, registry):
        provider = registry.get_provider("telegram")
        state = await provider_connection_state(provider, FakeAuth(keys={"telegram"}))
        assert state["stored"] is True and state["connected"] is True

    async def test_oauth_account_label(self, registry):
        state = await provider_connection_state(
            registry.get_provider("google"), FakeAuth(oauth={"google": {"email": "owner@example.com"}})
        )
        assert state["account_label"] == "owner@example.com"
        assert state["connected"] is True

    async def test_credential_extras_can_replace_stored_and_connected_follows(self, monkeypatch):
        class Extras:
            @classmethod
            async def catalogue_extras(cls):
                return {"stored": True, "endpoints": [{"slug": "home"}]}

        from services.plugin import credential as credential_module

        monkeypatch.setitem(credential_module.CREDENTIAL_REGISTRY, "fake_endpoints", Extras)
        state = await provider_connection_state({"id": "fake_endpoints", "kind": "apiKey"}, FakeAuth())
        assert state["stored"] is True
        assert state["connected"] is True
        assert state["endpoints"] == [{"slug": "home"}]


class TestLiveVersion:
    def test_pairing_changes_the_served_version(self, registry, whatsapp_slot):
        whatsapp_slot(False)
        before = registry.get_live_version()
        assert before == registry.get_live_version()
        whatsapp_slot(True)
        after = registry.get_live_version()
        assert after != before
        assert after.startswith(registry.get_version())

    def test_fingerprint_is_empty_without_status_checks(self):
        assert registry_module._status_fingerprint([{"id": "openai", "kind": "apiKey"}]) == ""
