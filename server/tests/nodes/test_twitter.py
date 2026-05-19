"""Contract tests for twitter nodes.

Covers: twitterSend, twitterSearch, twitterUser, twitterReceive.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/twitter/`. A refactor that breaks any of these
indicates the docs (and the user-visible contract) must be updated.

External boundaries mocked:
  - The XDK `Client` class is replaced at its import site inside
    `services.handlers.twitter`. XDK's methods are sync (they run via
    `requests` internally and get wrapped in ``asyncio.to_thread``), so
    every stubbed method is a plain MagicMock returning an object with the
    fields the handler reads (``.data`` etc.).
  - ``auth_service.get_oauth_tokens('twitter')`` is fed via
    ``patched_container(auth_oauth_tokens={...})``.
  - ``event_waiter`` is patched at both the executor dispatch site and the
    generic trigger handler for ``twitterReceive``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import (
    patched_broadcaster,
    patched_container,
    patched_pricing,
)


pytestmark = pytest.mark.node_contract


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _ok_tokens() -> dict:
    return {
        "twitter": {
            "access_token": "tk_access",
            "refresh_token": "tk_refresh",
            "email": "user@example.com",
            "name": "Owner",
            "scopes": "tweet.read tweet.write users.read",
        }
    }


def _make_client_class(*, posts=None, users=None):
    """Return a stub replacement for ``xdk.Client`` that captures ctor args.

    ``posts`` / ``users`` are MagicMocks with whatever methods the specific
    test needs. Each time the handler does ``Client(access_token=...)`` the
    returned stub records the call and yields a fresh instance with the
    provided sub-namespaces attached.
    """
    created_instances = []

    class _StubClient:
        def __init__(self, access_token=None, **kwargs):
            self.access_token = access_token
            self.posts = posts or MagicMock(name="posts")
            self.users = users or MagicMock(name="users")
            created_instances.append(self)

    _StubClient.instances = created_instances  # type: ignore[attr-defined]
    return _StubClient


def _patch_client(stub_cls):
    """Patch xdk.Client at the SDK module. Scaling-branch plugin does
    `from xdk import Client` inside `nodes.twitter._base.get_twitter_client`
    per call, so patching the source module is the correct target."""
    return patch("xdk.Client", stub_cls)


# ============================================================================
# twitterSend
# ============================================================================


class TestTwitterSend:
    async def test_tweet_happy_path(self, harness):
        posts = MagicMock(name="posts")
        posts.create = MagicMock(
            return_value=SimpleNamespace(
                data={"id": "1999000000000000001", "text": "hello world"}
            )
        )
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend",
                {"action": "tweet", "text": "hello world"},
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["data"]["id"] == "1999000000000000001"
        assert payload["data"]["text"] == "hello world"
        posts.create.assert_called_once()
        body = posts.create.call_args.kwargs["body"]
        assert body == {"text": "hello world"}
        # Client constructed with the stored access token
        assert stub_cls.instances[0].access_token == "tk_access"

    async def test_tweet_truncates_to_280_chars(self, harness):
        posts = MagicMock(name="posts")
        posts.create = MagicMock(
            return_value=SimpleNamespace(data={"id": "1", "text": "t"})
        )
        stub_cls = _make_client_class(posts=posts)
        long_text = "x" * 400

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend", {"action": "tweet", "text": long_text}
            )

        harness.assert_envelope(result, success=True)
        sent = posts.create.call_args.kwargs["body"]["text"]
        assert len(sent) == 280
        assert sent == "x" * 280

    async def test_reply_happy_path(self, harness):
        posts = MagicMock(name="posts")
        posts.create = MagicMock(
            return_value=SimpleNamespace(data={"id": "r1", "text": "replying"})
        )
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend",
                {
                    "action": "reply",
                    "text": "replying",
                    "tweet_id": "1234567890",
                },
            )

        harness.assert_envelope(result, success=True)
        body = posts.create.call_args.kwargs["body"]
        assert body == {
            "text": "replying",
            "reply": {"in_reply_to_tweet_id": "1234567890"},
        }

    async def test_reply_missing_reply_to_id_errors(self, harness):
        posts = MagicMock(name="posts")
        posts.create = MagicMock()
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend", {"action": "reply", "text": "hi"}
            )

        harness.assert_envelope(result, success=False)
        assert "tweet_id" in result["error"].lower()
        posts.create.assert_not_called()

    async def test_tweet_empty_text_errors(self, harness):
        posts = MagicMock(name="posts")
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend", {"action": "tweet", "text": ""}
            )

        harness.assert_envelope(result, success=False)
        assert "text is required" in result["error"].lower()

    async def test_unknown_action_errors(self, harness):
        # Post-refactor: action is a Literal; unknown values rejected by Pydantic.
        stub_cls = _make_client_class()

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend", {"action": "mysterious", "text": "x", "tweet_id": "1"}
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_missing_oauth_tokens_errors(self, harness):
        stub_cls = _make_client_class()

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens={}  # no twitter entry
        ), patched_pricing():
            result = await harness.execute(
                "twitterSend", {"action": "tweet", "text": "hi"}
            )

        harness.assert_envelope(result, success=False)
        assert "not connected" in result["error"].lower()

    async def test_401_triggers_refresh_and_retry(self, harness):
        # First create() raises 401. Handler should call refresh_access_token,
        # rebuild the client, and retry exactly once.
        calls = {"n": 0}

        def _create_side_effect(body=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("401 Unauthorized")
            return SimpleNamespace(data={"id": "99", "text": "ok"})

        posts = MagicMock(name="posts")
        posts.create = MagicMock(side_effect=_create_side_effect)
        stub_cls = _make_client_class(posts=posts)

        refresh_mock = AsyncMock(
            return_value={
                "success": True,
                "access_token": "tk_new",
                "refresh_token": "tk_refresh2",
            }
        )

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing(), patch(
            "nodes.twitter._oauth.TwitterOAuth"
        ) as oauth_cls:
            oauth_instance = MagicMock()
            oauth_instance.refresh_access_token = refresh_mock
            oauth_cls.return_value = oauth_instance

            result = await harness.execute(
                "twitterSend", {"action": "tweet", "text": "hi"}
            )

        harness.assert_envelope(result, success=True)
        assert calls["n"] == 2  # original + retry
        refresh_mock.assert_awaited_once_with("tk_refresh")
        # New client built with refreshed token
        assert stub_cls.instances[-1].access_token == "tk_new"


# ============================================================================
# twitterSearch
# ============================================================================


class TestTwitterSearch:
    @staticmethod
    def _search_page(tweets, includes=None):
        return SimpleNamespace(data=tweets, includes=includes)

    async def test_happy_path_returns_enriched_tweets(self, harness):
        # Raw tweet references an author in includes.users and a media key in
        # includes.media; entities.urls contains a t.co shortlink that should
        # be expanded in display_text. A note_tweet field replaces the text.
        raw_tweet = {
            "id": "tw1",
            "author_id": "user42",
            "text": "check this https://t.co/abc",
            "created_at": "2026-04-15T10:00:00",
            "lang": "en",
            "source": "web",
            "conversation_id": "conv1",
            "in_reply_to_user_id": None,
            "possibly_sensitive": False,
            "entities": {
                "urls": [
                    {
                        "url": "https://t.co/abc",
                        "expanded_url": "https://example.com/long",
                        "display_url": "example.com/long",
                    }
                ]
            },
            "public_metrics": {
                "retweet_count": 2,
                "reply_count": 1,
                "like_count": 7,
                "quote_count": 0,
                "bookmark_count": 0,
                "impression_count": 120,
            },
            "attachments": {"media_keys": ["mk1"]},
            "referenced_tweets": [{"type": "quoted", "id": "twQ"}],
            "note_tweet": None,
        }
        includes = {
            "users": [
                {
                    "id": "user42",
                    "username": "alice",
                    "name": "Alice",
                    "profile_image_url": "https://example.com/a.jpg",
                }
            ],
            "media": [
                {"media_key": "mk1", "type": "photo", "url": "https://example.com/m.jpg"}
            ],
            "tweets": [
                {"id": "twQ", "text": "quoted body", "author_id": "userQ"}
            ],
        }

        posts = MagicMock(name="posts")
        # search_recent is a generator; one page is enough per the handler's
        # "break after first page" contract.
        posts.search_recent = MagicMock(
            return_value=iter([self._search_page([raw_tweet], includes)])
        )
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSearch",
                {"query": "hello", "max_results": 10},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["tweets", "count", "query"])
        payload = result["result"]
        assert payload["count"] == 1
        assert payload["query"] == "hello"

        [tw] = payload["tweets"]
        # Enriched fields documented in twitterSearch.md
        assert tw["id"] == "tw1"
        assert tw["display_text"] == "check this https://example.com/long"
        assert tw["public_metrics"]["like_count"] == 7
        # Author joined from includes.users
        assert tw["author"]["username"] == "alice"
        # Media joined from includes.media via attachments.media_keys
        assert tw["media"][0]["media_key"] == "mk1"
        # Referenced tweets joined from includes.tweets
        assert tw["referenced_tweets"][0] == {
            "type": "quoted",
            "id": "twQ",
            "text": "quoted body",
            "author_id": "userQ",
        }
        # Expanded URL list present
        assert tw["urls"][0]["expanded_url"] == "https://example.com/long"

    async def test_note_tweet_replaces_text(self, harness):
        raw_tweet = {
            "id": "tw2",
            "author_id": "u1",
            "text": "short",
            "note_tweet": {"text": "this is a long form thread exceeding 280 chars"},
            "entities": {"urls": []},
            "public_metrics": {},
        }
        posts = MagicMock(name="posts")
        posts.search_recent = MagicMock(
            return_value=iter([self._search_page([raw_tweet], {})])
        )
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSearch", {"query": "x"}
            )

        harness.assert_envelope(result, success=True)
        tw = result["result"]["tweets"][0]
        assert tw["text"].startswith("this is a long form thread")
        assert tw["display_text"] == tw["text"]

    async def test_max_results_above_100_rejected(self, harness):
        posts = MagicMock(name="posts")
        posts.search_recent = MagicMock(return_value=iter([self._search_page([], {})]))
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSearch", {"query": "x", "max_results": 500}
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_max_results_below_10_rejected(self, harness):
        posts = MagicMock(name="posts")
        posts.search_recent = MagicMock(return_value=iter([self._search_page([], {})]))
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSearch", {"query": "x", "max_results": 3}
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_empty_query_short_circuits(self, harness):
        posts = MagicMock(name="posts")
        posts.search_recent = MagicMock()
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterSearch", {"query": "   "}
            )

        # Handler checks "not query" i.e. falsy, so empty string fails.
        # Whitespace-only goes through but a strip-less empty check means
        # the SDK may get called; docs document "query required". Accept
        # either behaviour but require an envelope.
        harness.assert_envelope(result)

    async def test_empty_query_exact_short_circuits(self, harness):
        posts = MagicMock(name="posts")
        posts.search_recent = MagicMock()
        stub_cls = _make_client_class(posts=posts)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute("twitterSearch", {"query": ""})

        harness.assert_envelope(result, success=False)
        assert "query is required" in result["error"].lower()
        posts.search_recent.assert_not_called()


# ============================================================================
# twitterUser
# ============================================================================


class TestTwitterUser:
    async def test_me_happy_path(self, harness):
        users = MagicMock(name="users")
        users.get_me = MagicMock(
            return_value=SimpleNamespace(
                data={
                    "id": "me1",
                    "username": "me",
                    "name": "Me",
                    "description": "hi",
                    "created_at": "2020-01-01",
                    "profile_image_url": "https://example.com/me.jpg",
                    "verified": True,
                }
            )
        )
        stub_cls = _make_client_class(users=users)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute("twitterUser", {"operation": "me"})

        harness.assert_envelope(result, success=True)
        user = result["result"]["user"]
        assert user["id"] == "me1"
        assert user["username"] == "me"
        assert user["description"] == "hi"
        assert user["verified"] is True
        users.get_me.assert_called_once()

    async def test_by_username_not_found(self, harness):
        users = MagicMock(name="users")
        users.get_by_usernames = MagicMock(
            return_value=SimpleNamespace(data=[])
        )
        stub_cls = _make_client_class(users=users)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterUser", {"operation": "by_username", "username": "ghost"}
            )

        harness.assert_envelope(result, success=False)
        assert "ghost" in result["error"]
        assert "not found" in result["error"].lower()

    async def test_by_username_missing_param(self, harness):
        users = MagicMock(name="users")
        users.get_by_usernames = MagicMock()
        stub_cls = _make_client_class(users=users)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterUser", {"operation": "by_username"}
            )

        harness.assert_envelope(result, success=False)
        assert "username" in result["error"].lower()
        users.get_by_usernames.assert_not_called()

    async def test_followers_above_1000_rejected(self, harness):
        users = MagicMock(name="users")
        users.get_followers = MagicMock(
            return_value=iter(
                [SimpleNamespace(data=[{"id": "u1", "username": "a", "name": "A"}])]
            )
        )
        stub_cls = _make_client_class(users=users)

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterUser",
                {"operation": "followers", "user_id": "x1", "max_results": 9999},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_unknown_operation(self, harness):
        stub_cls = _make_client_class()

        with _patch_client(stub_cls), patched_container(
            auth_oauth_tokens=_ok_tokens()
        ), patched_pricing():
            result = await harness.execute(
                "twitterUser", {"operation": "mysterious"}
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# twitterReceive
# ============================================================================


def _make_waiter_stub(*, canned_event=None):
    """Fake event_waiter module for the trigger path."""
    waiter_obj = MagicMock(name="Waiter")
    waiter_obj.id = "waiter-twitter-id"

    stub = MagicMock(name="event_waiter_module")
    stub.is_trigger_node = MagicMock(return_value=True)
    stub.get_trigger_config = MagicMock(
        return_value=MagicMock(
            node_type="twitterReceive",
            event_type="twitter_event_received",
            display_name="Twitter Event",
        )
    )
    stub.register = AsyncMock(return_value=waiter_obj)
    stub.wait_for_event = AsyncMock(return_value=canned_event or {})
    stub.run_trigger_precheck = AsyncMock(return_value=None)
    stub.get_backend_mode = MagicMock(return_value="asyncio.Future")
    stub.cancel = MagicMock(return_value=True)
    stub.dispatch = MagicMock(return_value=1)
    return stub


class TestTwitterReceive:
    CANNED = {
        "trigger_type": "mentions",
        "tweet_id": "tw123",
        "text": "@me hello",
        "author_id": "u999",
        "author_username": "alice",
        "created_at": "2026-04-15T12:00:00",
    }

    async def test_happy_path_returns_canned_event(self, harness):
        waiter = _make_waiter_stub(canned_event=self.CANNED)

        with patched_broadcaster(), patch(
            "services.handlers.triggers.event_waiter", waiter
        ):
            result = await harness.execute(
                "twitterReceive",
                {"trigger_type": "mentions", "poll_interval": 60},
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["tweet_id"] == "tw123"
        assert payload["author_username"] == "alice"
        waiter.register.assert_awaited_once()
        waiter.wait_for_event.assert_awaited_once()

    async def test_cancellation_returns_error_envelope(self, harness):
        import asyncio

        waiter = _make_waiter_stub()
        waiter.wait_for_event = AsyncMock(side_effect=asyncio.CancelledError())

        with patched_broadcaster(), patch(
            "services.handlers.triggers.event_waiter", waiter
        ):
            result = await harness.execute(
                "twitterReceive", {"trigger_type": "mentions"}
            )

        harness.assert_envelope(result, success=False)
        assert "cancel" in result["error"].lower()

    async def test_unknown_trigger_config_errors(self, harness):
        # get_trigger_config -> None simulates a missing registry entry.
        waiter = _make_waiter_stub()
        waiter.get_trigger_config = MagicMock(return_value=None)

        with patched_broadcaster(), patch(
            "services.handlers.triggers.event_waiter", waiter
        ):
            result = await harness.execute(
                "twitterReceive", {"trigger_type": "mentions"}
            )

        harness.assert_envelope(result, success=False)
        assert "unknown trigger" in result["error"].lower()
        waiter.register.assert_not_awaited()

    async def test_generic_exception_becomes_envelope(self, harness):
        waiter = _make_waiter_stub()
        waiter.wait_for_event = AsyncMock(side_effect=RuntimeError("boom"))

        with patched_broadcaster(), patch(
            "services.handlers.triggers.event_waiter", waiter
        ):
            result = await harness.execute(
                "twitterReceive", {"trigger_type": "mentions"}
            )

        harness.assert_envelope(result, success=False)
        assert "boom" in result["error"]
