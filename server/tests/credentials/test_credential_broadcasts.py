"""Pytest invariant: every credential-mutation handler MUST broadcast.

The frontend's `useCatalogueQuery` cache + `apiKeyStatuses` map both go
stale unless the backend explicitly invalidates via:

- ``broadcaster.update_api_key_status(...)`` — per-provider validation
  state change (carries the validation result payload).
- ``broadcaster.broadcast_credential_event("credential.*", ...)`` —
  CloudEvents-typed mutation (refetch signal). Wraps ``WorkflowEvent``
  from ``services.events.envelope``.

Cross-tab visibility breaks the moment a handler omits both. This test
locks the contract by reading each handler's source via
``inspect.getsource`` and asserting it contains at least one of the two
broadcast call patterns. New credential mutations that forget to
broadcast fail CI before they ship.

Companion: ``test_auth_service.py`` locks the DB-write-then-cache-update
ordering inside AuthService.
"""

from __future__ import annotations

import inspect
import re

import pytest


pytestmark = pytest.mark.credentials


# Match either:
#   broadcaster.update_api_key_status(...)
#   broadcaster.broadcast_credential_event("credential.<anything>", ...)
# Anchor on `.update_api_key_status(` or `.broadcast_credential_event(`
# so renames force a test update (intentional).
_BROADCAST_PATTERN = re.compile(
    r"\.(?:update_api_key_status|broadcast_credential_event)\s*\("
)


def _handler_source(handler) -> str:
    """Return the unwrapped source of a handler decorated by `@ws_handler`."""
    fn = handler
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return inspect.getsource(fn)


class TestCredentialMutationHandlersBroadcast:
    """Every handler that mutates credential state MUST broadcast.

    Failing this test means a frontend cache will go stale across tabs
    after the mutation; users will see the wrong stored / connected
    state until they manually refresh.
    """

    def test_validate_api_key_broadcasts(self):
        """Validation broadcasts come from ``Credential.validate`` now.

        ``handle_validate_api_key`` is a pure dispatcher that calls
        ``CREDENTIAL_REGISTRY[provider].validate(...)``. The broadcast
        happens inside ``Credential.validate`` (the shared scaffold in
        ``services/plugin/credential.py``). This test checks the
        invariant at the point where it actually lives.
        """
        from services.plugin.credential import Credential

        src = inspect.getsource(Credential.validate)
        assert _BROADCAST_PATTERN.search(src), (
            "Credential.validate must broadcast via update_api_key_status — "
            "every credential mutation must surface to connected clients."
        )

    def test_save_api_key_broadcasts(self):
        from routers import websocket as ws_module

        src = _handler_source(ws_module.handle_save_api_key)
        assert _BROADCAST_PATTERN.search(src), (
            "handle_save_api_key must broadcast credential.api_key.saved "
            "via broadcast_credential_event so the catalogue's stored "
            "flag flips on every connected client"
        )

    def test_delete_api_key_broadcasts(self):
        from routers import websocket as ws_module

        src = _handler_source(ws_module.handle_delete_api_key)
        # Delete must trigger BOTH broadcasts: api_key_status (clears
        # apiKeyStatuses) + credential.api_key.deleted (catalogue refetch).
        matches = _BROADCAST_PATTERN.findall(src)
        assert len(matches) >= 2, (
            "handle_delete_api_key must broadcast both update_api_key_status "
            "(clears apiKeyStatuses[provider]) AND broadcast_credential_event "
            "('credential.api_key.deleted') (catalogue refetch). "
            f"Found {len(matches)} broadcast call(s)."
        )

    def test_twitter_logout_broadcasts(self):
        # Twitter handler moved to ``nodes/twitter/_handlers.py`` as
        # part of the plugin-extraction migration. The invariant now
        # asserts on the plugin-owned source.
        from nodes.twitter._handlers import handle_twitter_logout

        src = _handler_source(handle_twitter_logout)
        assert _BROADCAST_PATTERN.search(src), (
            "handle_twitter_logout must broadcast credential.oauth.disconnected"
        )

    def test_google_logout_broadcasts(self):
        from nodes.google._handlers import handle_google_logout

        src = _handler_source(handle_google_logout)
        assert _BROADCAST_PATTERN.search(src), (
            "handle_google_logout must broadcast credential.oauth.disconnected"
        )


class TestCredentialEventCloudEventsShape:
    """The credential broadcast helper wraps `WorkflowEvent` so the
    on-the-wire body is a CloudEvents v1.0 envelope. Locks the spec
    fields so future refactors don't quietly drop required keys.
    """

    @pytest.fixture
    def envelope(self):
        from services.events.envelope import WorkflowEvent

        event = WorkflowEvent(
            source="machinaos://services/credentials",
            type="com.machinaos.credential.api_key.saved",
            subject="openai",
            data={"provider": "openai"},
        )
        return event.model_dump(mode="json")

    def test_specversion_pinned_to_1(self, envelope):
        assert envelope["specversion"] == "1.0"

    def test_required_fields_present(self, envelope):
        # CloudEvents 1.0 mandatory: id, source, specversion, type
        for field in ("id", "source", "specversion", "type"):
            assert envelope.get(field), f"missing required field: {field}"

    def test_credential_subject_is_provider(self, envelope):
        assert envelope["subject"] == "openai"

    def test_event_type_namespaced(self, envelope):
        # Convention: com.machinaos.credential.<area>.<action>
        # (reverse-DNS prefix per CloudEvents Primer)
        assert envelope["type"].startswith("com.machinaos.credential."), envelope["type"]


class TestAuthServiceDbCanonicalInvariant:
    """AuthService.store_*/remove_* must do the DB call before touching
    the in-memory cache. Reverse order leaves a stale-cache window.

    Static-source check; doesn't run the methods.
    """

    @pytest.fixture
    def auth_source(self):
        from services import auth as auth_module

        return inspect.getsource(auth_module.AuthService)

    @pytest.mark.parametrize(
        "method_name,db_call",
        [
            ("store_api_key", "credentials_db.save_api_key"),
            ("remove_api_key", "credentials_db.delete_api_key"),
            ("store_oauth_tokens", "credentials_db.save_oauth_tokens"),
            ("remove_oauth_tokens", "credentials_db.delete_oauth_tokens"),
        ],
    )
    def test_method_calls_db(self, auth_source, method_name, db_call):
        """Every store_*/remove_* method must call the canonical DB
        method. Invariant breaks if a refactor accidentally bypasses
        the DB and only updates the cache."""
        # Find the method body in the AuthService source. Anchor end on
        # the next method/class start OR end-of-string (the last method
        # in the class has no trailing sibling).
        method_re = re.compile(
            rf"async def {method_name}\b.*?(?=\n    async def |\n    def |\nclass |\Z)",
            re.DOTALL,
        )
        match = method_re.search(auth_source)
        assert match, f"AuthService.{method_name} not found in source"
        body = match.group(0)
        assert db_call in body, (
            f"AuthService.{method_name} must call self.{db_call}() "
            f"before touching the in-memory cache (DB is canonical "
            f"source of truth)"
        )

    def test_oauth_cache_does_not_carry_refresh_token(self, auth_source):
        """Per RFC 9700, refresh tokens must not live in process memory.
        store_oauth_tokens caches only the access token + display fields.
        """
        match = re.search(
            r"async def store_oauth_tokens\b.*?(?=\n    async def |\n    def |\nclass )",
            auth_source,
            re.DOTALL,
        )
        assert match, "AuthService.store_oauth_tokens not found"
        body = match.group(0)

        # Find the cache-write block. It assigns to self._oauth_cache[...].
        cache_block = re.search(
            r"self\._oauth_cache\[\w+\]\s*=\s*\{[^}]+\}", body, re.DOTALL
        )
        assert cache_block, (
            "AuthService.store_oauth_tokens must populate _oauth_cache"
        )
        assert '"refresh_token"' not in cache_block.group(0), (
            "RFC 9700 violation: _oauth_cache entry must not carry "
            "refresh_token. Use get_oauth_refresh_token() helper that "
            "always reads from the encrypted DB."
        )
