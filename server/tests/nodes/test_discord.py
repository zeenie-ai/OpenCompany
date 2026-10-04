"""Contract for the Discord plugin.

The security-relevant test is ``TestModelCannotChooseTheAccount``. A
dual-purpose ActionNode's tool call merges ``{**node_params, **tool_args}``,
so what stands between a prompt injection in an inbound Discord message and
sending as a different bot is ``server_controlled_fields``. This asserts it
actually holds through the real tool path, not just the schema.
"""

from __future__ import annotations

import asyncio
import typing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nodes.discord import _base, _ratelimit
from nodes.discord._accounts import (
    DEFAULT_ACCOUNT,
    account_id_from_scope,
    storage_scope,
)
from nodes.discord.discord_action import DiscordActionNode, DiscordActionParams
from nodes.discord.discord_receive import DiscordReceiveNode
from nodes.discord.discord_send import (
    MAX_CONTENT,
    DiscordSendNode,
    DiscordSendParams,
    split_content,
)
from services.plugin import NodeUserError

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(**raw):
    return SimpleNamespace(
        node_id="discord-1",
        node_type="discordSend",
        workflow_id="wf-1",
        session_id="default",
        user_id="owner",
        workspace_dir=None,
        outputs={},
        nodes=[],
        edges=[],
        raw=dict(raw),
    )


class TestAccountScoping:
    """account_id <-> session_id is the whole multi-account mechanism."""

    def test_default_account_uses_the_unprefixed_scope(self):
        """The credentials modal writes session_id="default" with no account
        concept at all. If that did not map to the default account, a
        single-bot install would see no credential."""
        assert storage_scope("") == DEFAULT_ACCOUNT
        assert storage_scope(DEFAULT_ACCOUNT) == DEFAULT_ACCOUNT

    def test_named_account_is_namespaced(self):
        assert storage_scope("123456") == "discord:123456"

    @pytest.mark.parametrize("account_id", ["", "default", "123456", "999"])
    def test_scope_round_trips(self, account_id):
        expected = account_id or DEFAULT_ACCOUNT
        assert account_id_from_scope(storage_scope(account_id)) == expected

    def test_scope_does_not_collide_with_another_provider(self):
        """The prefix is what keeps a Discord account from reading a scope
        some other plugin created."""
        assert storage_scope("123").startswith("discord:")


class TestPathGuard:
    """discordAction's custom operation lets a workflow name its own route.

    Without this guard that is an SSRF primitive that would send the bot
    token to an arbitrary host.
    """

    def test_relative_path_is_joined_onto_the_pinned_base(self):
        assert _base.build_url("users/@me") == f"{_base.API_BASE_URL}/{_base.API_VERSION}/users/@me"

    def test_leading_slash_is_tolerated(self):
        assert _base.build_url("/users/@me").endswith("/users/@me")

    @pytest.mark.parametrize(
        "path",
        [
            "https://evil.example/steal",
            "http://evil.example/steal",
            "//evil.example/steal",
            "../../../etc/passwd",
            "users/../../admin",
            "",
            "   ",
        ],
    )
    def test_escaping_paths_are_refused(self, path):
        with pytest.raises(NodeUserError):
            _base.build_url(path)

    @pytest.mark.parametrize("url", ["https://evil.example/api/webhooks/1/t", "https://discord.com.evil.io/x"])
    def test_webhook_host_is_whitelisted(self, url):
        with pytest.raises(NodeUserError):
            _base.assert_discord_host(url)

    def test_real_webhook_host_is_allowed(self):
        _base.assert_discord_host("https://discord.com/api/webhooks/1/token")


class TestErrorClassification:
    """The split that matters is retryable vs terminal.

    NodeUserError is non-retryable in the shared policy, so classifying a
    throttle as one fails fast instead of backing off; classifying a
    permanent rejection as retryable burns three attempts to reach the same
    answer.
    """

    @pytest.mark.parametrize(
        "code, status, category, retryable",
        [
            (0, 401, "auth", False),
            (40001, 401, "auth", False),
            (50001, 403, "permission", False),
            (50013, 403, "permission", False),
            (10003, 404, "not_found", False),
            (0, 429, "throttle", True),
            (0, 500, "transient", True),
            (0, 502, "transient", True),
            (0, 400, "unknown", False),
        ],
    )
    def test_classification(self, code, status, category, retryable):
        assert _base.classify_error(code, status) == (category, retryable)

    def test_auth_failure_raises_annotated_permission_error(self):
        """An annotated PermissionError gets the framework's credential
        envelope and a reconnect affordance; a NodeUserError does not."""
        with pytest.raises(PermissionError) as excinfo:
            _base.raise_for_discord_error({"code": 40001, "message": "Unauthorized"}, 401)
        assert excinfo.value.provider == "discord"
        assert excinfo.value.reason == "invalid"
        assert excinfo.value.auth == "api_key"

    def test_permission_failure_is_a_user_error(self):
        with pytest.raises(NodeUserError):
            _base.raise_for_discord_error({"code": 50013, "message": "Missing Permissions"}, 403)

    def test_server_failure_is_retryable_not_a_user_error(self):
        with pytest.raises(RuntimeError) as excinfo:
            _base.raise_for_discord_error({"code": 0, "message": "boom"}, 502)
        assert not isinstance(excinfo.value, NodeUserError)


class TestRateLimit:
    def test_body_retry_after_beats_the_rounded_header(self):
        """The header is whole seconds; the body carries sub-second
        precision. Preferring the header would over-wait on every 429."""
        assert _ratelimit.parse_retry_after({"retry_after": 0.35}, {"Retry-After": "1"}) == 0.35

    def test_header_is_the_fallback(self):
        assert _ratelimit.parse_retry_after(None, {"Retry-After": "2"}) == 2.0

    def test_malformed_retry_after_degrades_instead_of_raising(self):
        """These values are attacker-adjacent; a parse error must not become
        an unhandled exception mid-send."""
        assert _ratelimit.parse_retry_after({"retry_after": "soon"}, {"Retry-After": "nope"}) == 1.0

    def test_global_limit_detected_from_body_or_scope_header(self):
        assert _ratelimit.is_global_limit({"global": True}, {})
        assert _ratelimit.is_global_limit(None, {"X-RateLimit-Scope": "global"})
        assert not _ratelimit.is_global_limit({"global": False}, {"X-RateLimit-Scope": "user"})

    def test_non_json_429_is_recognised_as_the_edge_ban(self):
        """Discord's own 429 is always JSON. An HTML one is Cloudflare
        rejecting the host IP, which has a different remedy and would
        otherwise read as an hour of unexplained rate limiting."""
        assert _ratelimit.is_cloudflare_ban({"Content-Type": "text/html"})
        assert not _ratelimit.is_cloudflare_ban({"Content-Type": "application/json"})

    def test_invalid_request_guard_trips_before_the_ban(self):
        guard = _ratelimit._InvalidRequestGuard()
        for _ in range(_ratelimit.INVALID_REQUEST_SAFETY_MARGIN):
            guard.record(401, now=1000.0)
        with pytest.raises(_ratelimit.InvalidRequestBudgetExhausted):
            guard.check(now=1000.0)

    def test_guard_only_counts_rejections(self):
        guard = _ratelimit._InvalidRequestGuard()
        for _ in range(100):
            guard.record(200, now=1000.0)
        assert guard.count(now=1000.0) == 0

    def test_guard_window_expires(self):
        guard = _ratelimit._InvalidRequestGuard()
        guard.record(429, now=1000.0)
        assert guard.count(now=1000.0) == 1
        assert guard.count(now=1000.0 + _ratelimit.INVALID_REQUEST_WINDOW_SECONDS + 1) == 0

    def test_guard_is_process_wide(self):
        """The ban is enforced per source IP, not per token, so three bots on
        one host share one budget. A per-account guard would not stop the
        ban it exists to prevent."""
        assert _ratelimit.invalid_request_guard() is _ratelimit.invalid_request_guard()


class TestContentSplitting:
    def test_short_text_is_one_chunk(self):
        assert split_content("hello") == ["hello"]

    def test_empty_text_produces_no_chunks(self):
        assert split_content("") == []

    def test_every_chunk_is_within_the_limit(self):
        chunks = split_content("word " * 2000)
        assert chunks
        assert all(len(c) <= MAX_CONTENT for c in chunks)

    def test_split_prefers_a_paragraph_boundary(self):
        first = "a" * 1200
        second = "b" * 1200
        chunks = split_content(f"{first}\n\n{second}")
        assert chunks[0] == first

    def test_unsplittable_text_is_hard_cut_rather_than_dropped(self):
        text = "x" * 5000
        chunks = split_content(text)
        assert all(len(c) <= MAX_CONTENT for c in chunks)
        assert "".join(chunks) == text


class TestModelCannotChooseTheAccount:
    """Which bot sends is operator configuration, not a model decision."""

    def test_tool_args_cannot_override_the_configured_account(self):
        node = DiscordSendNode()
        captured = {}

        async def _fake_post(path, body=None, *, account_id=DEFAULT_ACCOUNT, **kwargs):
            captured["account_id"] = account_id
            captured["path"] = path
            return {"id": "123", "channel_id": "c1"}

        with patch("nodes.discord.discord_send._base.post", new=_fake_post):
            _run(
                node.execute_as_tool(
                    {"channel_id": "c1", "message": "hi", "account_id": "ATTACKER"},
                    {"account_id": "operator-account", "channel_id": "c1"},
                    _ctx(),
                )
            )

        assert captured["account_id"] == "operator-account"

    def test_account_is_declared_server_controlled_on_both_nodes(self):
        for node_cls in (DiscordSendNode, DiscordActionNode):
            assert "account_id" in node_cls.server_controlled_fields

    def test_action_node_account_cannot_be_overridden(self):
        node = DiscordActionNode()
        captured = {}

        async def _fake_get(path, *, account_id=DEFAULT_ACCOUNT, params=None):
            captured["account_id"] = account_id
            return []

        with patch("nodes.discord.discord_action._base.get", new=_fake_get):
            _run(
                node.execute_as_tool(
                    {"operation": "list_guilds", "account_id": "ATTACKER"},
                    {"operation": "list_guilds", "account_id": "operator-account"},
                    _ctx(),
                )
            )

        assert captured["account_id"] == "operator-account"


class TestSendShape:
    def _capture(self, params, **patches):
        node = DiscordSendNode()
        captured = {"posts": []}

        async def _fake_post(path, body=None, *, account_id=DEFAULT_ACCOUNT, **kwargs):
            captured["posts"].append({"path": path, "body": body, "kwargs": kwargs})
            return {"id": f"m{len(captured['posts'])}", "channel_id": "c1"}

        with patch("nodes.discord.discord_send._base.post", new=_fake_post):
            captured["result"] = _run(node.send(_ctx(), params))
        return captured

    def test_channel_send_posts_to_the_channel_messages_route(self):
        captured = self._capture(DiscordSendParams(channel_id="c1", message="hi"))
        assert captured["posts"][0]["path"] == "channels/c1/messages"
        assert captured["posts"][0]["body"]["content"] == "hi"

    def test_dm_opens_a_channel_first(self):
        """Discord has no send-to-user route; a DM channel must be opened."""
        captured = self._capture(DiscordSendParams(target_type="user", user_id="u1", message="hi"))
        assert captured["posts"][0]["path"] == "users/@me/channels"

    def test_long_message_is_split_across_posts(self):
        captured = self._capture(DiscordSendParams(channel_id="c1", message="word " * 2000))
        assert len(captured["posts"]) > 1
        assert captured["result"].parts == len(captured["posts"])
        assert len(captured["result"].message_ids) == len(captured["posts"])

    def test_reply_reference_rides_only_the_first_message(self):
        captured = self._capture(
            DiscordSendParams(channel_id="c1", message="word " * 2000, reply_to_message_id="m0")
        )
        assert "message_reference" in captured["posts"][0]["body"]
        assert all("message_reference" not in p["body"] for p in captured["posts"][1:])

    def test_embeds_ride_the_final_message(self):
        """So they render after the text they belong to."""
        captured = self._capture(
            DiscordSendParams(channel_id="c1", message="word " * 2000, embeds=[{"title": "t"}])
        )
        assert "embeds" not in captured["posts"][0]["body"]
        assert captured["posts"][-1]["body"]["embeds"] == [{"title": "t"}]

    def test_empty_send_is_refused(self):
        node = DiscordSendNode()
        with pytest.raises(NodeUserError):
            _run(node.send(_ctx(), DiscordSendParams(channel_id="c1")))

    def test_too_many_embeds_is_refused_locally(self):
        """Discord rejects the whole message, so failing here is clearer."""
        node = DiscordSendNode()
        with pytest.raises(NodeUserError):
            _run(
                node.send(
                    _ctx(),
                    DiscordSendParams(channel_id="c1", embeds=[{"title": str(i)} for i in range(11)]),
                )
            )

    def test_missing_channel_is_a_clear_error(self):
        node = DiscordSendNode()
        with pytest.raises(NodeUserError):
            _run(node.send(_ctx(), DiscordSendParams(message="hi")))


class TestActionShape:
    def test_every_operation_has_a_method(self):
        declared = set(typing.get_args(DiscordActionParams.model_fields["operation"].annotation))
        implemented = {spec.name for spec in DiscordActionNode._operations.values()}
        assert declared == implemented

    def test_download_attachments_without_input_is_a_clear_error(self):
        node = DiscordActionNode()
        with pytest.raises(NodeUserError):
            _run(node.download_attachments(_ctx(), DiscordActionParams(operation="download_attachments")))

    def test_required_ids_are_validated(self):
        node = DiscordActionNode()
        with pytest.raises(NodeUserError):
            _run(node.list_channels(_ctx(), DiscordActionParams(operation="list_channels")))


class TestToolSchema:
    """Corpus-wide invariant, asserted locally so the reason is visible."""

    @pytest.mark.parametrize("node_cls", [DiscordSendNode, DiscordActionNode])
    def test_tool_schema_carries_no_unresolvable_ref(self, node_cls):
        """A $ref with no $defs alongside it is a pointer the LLM cannot
        follow. Nested models are fine -- they are inlined before emission."""
        schema = node_cls.Params.model_json_schema()
        rendered = str(schema)
        assert "$ref" not in rendered or "$defs" in rendered

    @pytest.mark.parametrize("node_cls", [DiscordSendNode, DiscordActionNode])
    def test_locked_fields_are_stripped_from_model_arguments(self, node_cls):
        """AccountScopedNode strips every locked field from tool_args before
        the merge, again, behind the framework's own strip (defence in
        depth)."""
        assert node_cls.server_controlled_fields
        assert node_cls.execute_as_tool is not _base.ActionNode.execute_as_tool

    @pytest.mark.parametrize("node_cls", [DiscordSendNode, DiscordActionNode])
    def test_no_param_is_named_type(self, node_cls):
        """A Params field called `type` is silently dropped from the served
        schema, so the node would ship a parameter the panel never renders."""
        assert "type" not in node_cls.Params.model_fields


class TestTriggerIsDeployable:
    """Membership in the constants frozensets is what makes deploy see it.

    Omitting a trigger there is a silent failure, not an error:
    find_trigger_nodes filters on the set, so deploy simply ignores the node
    -- no listener, no warning. constants.py says so in a comment for exactly
    this reason.
    """

    def test_registered_for_deployment(self):
        from constants import (
            EVENT_TRIGGER_TYPES,
            POLLING_TRIGGER_TYPES,
            WORKFLOW_TRIGGER_TYPES,
        )

        assert DiscordReceiveNode.type in EVENT_TRIGGER_TYPES
        assert DiscordReceiveNode.type in WORKFLOW_TRIGGER_TYPES
        # Push-based: a polling entry would spawn a poll loop that duplicates
        # every gateway event.
        assert DiscordReceiveNode.type not in POLLING_TRIGGER_TYPES

    def test_canary_type_matches_the_emitted_envelope(self):
        """The registered string becomes the EventType Search Attribute the
        Visibility query matches on. If it drifted from what the envelope
        carries, the listener would start and never fire."""
        from nodes.discord._events import MESSAGE_RECEIVED_TYPE, discord_message_received
        from services.deployment.canary_registry import cloudevent_type_for

        assert cloudevent_type_for(DiscordReceiveNode.type) == MESSAGE_RECEIVED_TYPE
        assert discord_message_received({"channel_id": "c1"}).type == MESSAGE_RECEIVED_TYPE

    def test_trigger_has_no_input_handles(self):
        kinds = [h["kind"] for h in DiscordReceiveNode.handles]
        assert "output" in kinds
        assert "input" not in kinds

    def test_filter_builder_is_registered(self):
        from services.event_waiter import FILTER_BUILDERS

        assert DiscordReceiveNode.type in FILTER_BUILDERS


class TestEventShaping:
    """_dispatch.shape_message feeds _filters; the two move together."""

    def _message(self, **overrides):
        base = SimpleNamespace(
            id=111111111111111111,
            content="hello",
            created_at=None,
            attachments=[],
            mentions=[],
            reference=None,
            author=SimpleNamespace(id=222222222222222222, name="bob", display_name="Bob", bot=False),
            guild=SimpleNamespace(id=333333333333333333, name="Guild"),
            channel=SimpleNamespace(id=444444444444444444, name="general"),
            _state=None,
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_snowflakes_are_stringified(self):
        """discord.py exposes ids as int. Past 2^53 a numeric comparison
        collapses distinct ids, and comparing an id is the most natural edge
        condition a user writes."""
        from nodes.discord._dispatch import shape_message

        event = shape_message(self._message(), account_id="default")
        for field in ("message_id", "channel_id", "guild_id", "author_id"):
            assert isinstance(event[field], str), field

    def test_dm_has_no_guild(self):
        from nodes.discord._dispatch import shape_message

        event = shape_message(self._message(guild=None), account_id="default")
        assert event["is_dm"] is True
        assert event["guild_id"] is None

    def test_attachments_are_metadata_not_bytes(self):
        from nodes.discord._dispatch import shape_message

        attachment = SimpleNamespace(
            id=555555555555555555,
            filename="a.png",
            size=10,
            url="https://cdn.discordapp.com/x",
            content_type="image/png",
            width=1,
            height=2,
        )
        event = shape_message(self._message(attachments=[attachment]), account_id="default")

        assert event["has_attachments"] is True
        rendered = str(event["attachments"])
        assert "bytes" not in rendered and "base64" not in rendered
        assert event["attachments"][0]["id"] == "555555555555555555"


class TestFilters:
    def _event(self, **overrides):
        base = {
            "account_id": "default",
            "content": "hello world",
            "author_is_bot": False,
            "is_dm": False,
            "guild_id": "g1",
            "channel_id": "c1",
            "author_id": "u1",
            "mentions_me": False,
            "has_attachments": False,
        }
        base.update(overrides)
        return base

    def _matches(self, params, **event_overrides):
        from nodes.discord._filters import build_discord_filter

        return build_discord_filter(params)(self._event(**event_overrides))

    def test_empty_filter_matches(self):
        assert self._matches({})

    def test_bots_ignored_by_default(self):
        """Two bots replying to each other is the loop this prevents."""
        assert not self._matches({}, author_is_bot=True)
        assert self._matches({"ignore_bots": False}, author_is_bot=True)

    def test_account_scoping(self):
        """Without this, connecting a second bot makes every trigger fire
        twice."""
        assert not self._matches({"account_id": "other"})
        assert self._matches({"account_id": "default"})

    @pytest.mark.parametrize(
        "params, overrides, expected",
        [
            ({"scope": "dm"}, {"is_dm": False}, False),
            ({"scope": "dm"}, {"is_dm": True}, True),
            ({"scope": "guild"}, {"is_dm": True}, False),
            ({"guild_id": "g2"}, {}, False),
            ({"channel_id": "c1"}, {}, True),
            ({"author_id": "u2"}, {}, False),
            ({"require_mention": True}, {}, False),
            ({"require_mention": True}, {"mentions_me": True}, True),
            ({"require_attachment": True}, {}, False),
            ({"keywords": "world"}, {}, True),
            ({"keywords": "absent"}, {}, False),
            ({"keywords": "WORLD"}, {}, True),
        ],
    )
    def test_filter_matrix(self, params, overrides, expected):
        assert self._matches(params, **overrides) is expected


class TestGatewayLifecycle:
    def test_each_account_gets_its_own_label(self):
        """The supervisor registry keys on the label; a shared one would mean
        the second account silently replaced the first."""
        from nodes.discord._gateway import DiscordGateway

        assert DiscordGateway("a").label != DiscordGateway("b").label

    def test_terminal_errors_are_not_retried(self):
        """A bad token or an unapproved intent fails identically every time,
        so retrying only spends the daily IDENTIFY budget."""
        from nodes.discord._gateway import DiscordGateway

        gateway = DiscordGateway("acct")
        assert gateway.can_retry()

        class PrivilegedIntentsRequired(Exception):
            pass

        translated = gateway._translate_start_error(PrivilegedIntentsRequired("nope"))
        assert isinstance(translated, NodeUserError)
        assert not gateway.can_retry()
        assert "Message Content" in str(translated)

    def test_unknown_errors_pass_through_for_retry(self):
        from nodes.discord._gateway import DiscordGateway

        gateway = DiscordGateway("acct")
        original = ConnectionResetError("socket died")
        assert gateway._translate_start_error(original) is original
        assert gateway.can_retry()

    def test_intents_request_message_content(self):
        """Without it the gateway connects and delivers empty content, with
        no error anywhere."""
        import discord

        from nodes.discord._gateway import _resolve_intents

        intents = _resolve_intents(discord)
        assert intents.message_content
        assert intents.guild_messages
        assert intents.dm_messages

    def test_login_failure_surfaces_immediately(self):
        """A rejected token raises inside client.start(), not out of
        wait_until_ready(). Awaiting readiness alone would sit out the whole
        60s timeout and then report "not ready" for a bad credential."""
        import sys

        from nodes.discord._gateway import READY_TIMEOUT_SECONDS, DiscordGateway

        class LoginFailure(Exception):
            pass

        class _FakeClient:
            def __init__(self, *a, **kw):
                self._closed = False
                self.user = None
                self.guilds = []

            def event(self, fn):
                return fn

            def is_closed(self):
                return self._closed

            async def start(self, token):
                raise LoginFailure("Improper token")

            async def wait_until_ready(self):
                await asyncio.sleep(READY_TIMEOUT_SECONDS * 10)

            async def close(self):
                self._closed = True

        fake_discord = SimpleNamespace(
            Client=_FakeClient,
            Intents=SimpleNamespace(none=lambda: SimpleNamespace()),
        )

        async def _resolve(account_id):
            return {"token": "bad"}

        gateway = DiscordGateway("acct")
        with (
            patch.dict(sys.modules, {"discord": fake_discord}),
            patch("nodes.discord._gateway.resolve_secrets", new=_resolve),
            patch("nodes.discord._gateway._resolve_intents", return_value=None),
        ):
            with pytest.raises(NodeUserError) as excinfo:
                _run(gateway._do_start())

        assert "token" in str(excinfo.value).lower()
        assert not gateway.can_retry()

    def test_receive_refuses_to_wait_without_a_connection(self):
        """Otherwise the Run button registers a waiter that can never
        resolve, which reads as a hung node."""
        from nodes.discord.discord_receive import DiscordReceiveNode

        result = _run(DiscordReceiveNode().execute("d1", {}, _ctx()))
        assert result["success"] is False
        assert "not connected" in result["error"].lower()


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    return private, public_hex


class TestEd25519Verifier:
    """The first asymmetric verifier here; the four shipped ones are HMAC."""

    def test_valid_signature_passes(self):
        from nodes.discord._verifier import DiscordEd25519Verifier

        private, public_hex = _keypair()
        body = b'{"type":1}'
        timestamp = "1700000000"
        signature = private.sign(timestamp.encode() + body).hex()

        DiscordEd25519Verifier.verify(
            {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp},
            body,
            public_hex,
        )

    def test_tampered_body_is_rejected(self):
        from nodes.discord._verifier import DiscordEd25519Verifier

        private, public_hex = _keypair()
        timestamp = "1700000000"
        signature = private.sign(timestamp.encode() + b'{"type":1}').hex()

        with pytest.raises(ValueError):
            DiscordEd25519Verifier.verify(
                {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp},
                b'{"type":2}',
                public_hex,
            )

    def test_timestamp_is_part_of_the_signed_payload(self):
        """Signing the body alone would let a captured request be replayed
        under a different timestamp."""
        from nodes.discord._verifier import DiscordEd25519Verifier

        private, public_hex = _keypair()
        body = b'{"type":1}'
        signature = private.sign(b"1700000000" + body).hex()

        with pytest.raises(ValueError):
            DiscordEd25519Verifier.verify(
                {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": "1700009999"},
                body,
                public_hex,
            )

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"X-Signature-Ed25519": "aa"},
            {"X-Signature-Timestamp": "1700000000"},
        ],
    )
    def test_missing_headers_are_rejected(self, headers):
        from nodes.discord._verifier import DiscordEd25519Verifier

        _, public_hex = _keypair()
        with pytest.raises(ValueError):
            DiscordEd25519Verifier.verify(headers, b"{}", public_hex)

    def test_non_hex_inputs_are_rejected_not_crashed(self):
        from nodes.discord._verifier import DiscordEd25519Verifier

        _, public_hex = _keypair()
        with pytest.raises(ValueError):
            DiscordEd25519Verifier.verify(
                {"X-Signature-Ed25519": "zzzz", "X-Signature-Timestamp": "1"},
                b"{}",
                public_hex,
            )


class TestInteractionTokenNeverLeaves:
    """The token is a 15-minute bearer credential and trigger output is
    persisted, broadcast and replayed into LLM context."""

    def test_shaped_interaction_carries_a_ref_not_the_token(self):
        from nodes.discord._interactions import resolve_token, shape_interaction

        payload = {
            "id": "111",
            "type": 2,
            "application_id": "999",
            "token": "SUPER_SECRET_TOKEN",
            "data": {"name": "ping", "options": [{"name": "who", "value": "world"}]},
            "member": {"user": {"id": "222", "username": "bob"}},
        }
        event = shape_interaction(payload, account_id="default")

        assert "SUPER_SECRET_TOKEN" not in str(event)
        assert event["interaction_ref"]
        assert event["command_name"] == "ping"
        assert event["options"] == {"who": "world"}
        # The ref is what trades back for the token, server-side only.
        assert resolve_token(event["interaction_ref"]) == ("999", "SUPER_SECRET_TOKEN")

    def test_unknown_ref_resolves_to_nothing(self):
        from nodes.discord._interactions import resolve_token

        assert resolve_token("not-a-real-ref") is None

    def test_component_click_defers_as_an_update(self):
        """Deferring a component as a new message posts an empty one."""
        from nodes.discord._interactions import (
            APPLICATION_COMMAND,
            DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
            DEFERRED_UPDATE_MESSAGE,
            MESSAGE_COMPONENT,
            deferred_response_type,
        )

        assert deferred_response_type(MESSAGE_COMPONENT) == DEFERRED_UPDATE_MESSAGE
        assert deferred_response_type(APPLICATION_COMMAND) == DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE


class TestInteractionsEndpoint:
    """Protocol requirements Discord enforces on the endpoint itself."""

    def _app(self):
        from fastapi import FastAPI

        from nodes.discord._router import router

        app = FastAPI()
        app.include_router(router)
        return app

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(self._app())

    def _post(self, body: bytes, headers: dict, public_key: str):
        auth = SimpleNamespace(get_api_key=AsyncMock(return_value=public_key))
        with patch("services.plugin.deps.get_auth_service", return_value=auth):
            return self._client().post(
                "/api/discord/interactions", content=body, headers=headers
            )

    def test_ping_is_answered_with_pong(self):
        private, public_hex = _keypair()
        body = b'{"type":1}'
        ts = "1700000000"
        sig = private.sign(ts.encode() + body).hex()

        response = self._post(
            body,
            {"X-Signature-Ed25519": sig, "X-Signature-Timestamp": ts, "Content-Type": "application/json"},
            public_hex,
        )

        assert response.status_code == 200
        assert response.json() == {"type": 1}

    def test_bad_signature_is_401_not_400(self):
        """Discord's endpoint-validation probe sends a deliberately invalid
        signature and refuses to save the URL unless it gets a 401."""
        _, public_hex = _keypair()
        other_private, _ = _keypair()
        body = b'{"type":1}'
        ts = "1700000000"
        sig = other_private.sign(ts.encode() + body).hex()

        response = self._post(
            body,
            {"X-Signature-Ed25519": sig, "X-Signature-Timestamp": ts, "Content-Type": "application/json"},
            public_hex,
        )

        assert response.status_code == 401

    def test_missing_public_key_fails_closed(self):
        """Accepting unverified requests would let anyone trigger workflows."""
        response = self._post(
            b'{"type":1}',
            {"X-Signature-Ed25519": "aa", "X-Signature-Timestamp": "1", "Content-Type": "application/json"},
            "",
        )

        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "5"

    def test_command_is_deferred_within_the_deadline(self):
        """A workflow cannot run in three seconds, so the endpoint must ACK
        and fan out afterwards."""
        private, public_hex = _keypair()
        body = b'{"type":2,"id":"1","application_id":"9","token":"t","data":{"name":"ping"}}'
        ts = "1700000000"
        sig = private.sign(ts.encode() + body).hex()

        with patch("nodes.discord._events.dispatch_discord_interaction_created", new=AsyncMock()):
            response = self._post(
                body,
                {"X-Signature-Ed25519": sig, "X-Signature-Timestamp": ts, "Content-Type": "application/json"},
                public_hex,
            )

        assert response.status_code == 200
        assert response.json() == {"type": 5}


class TestRouterMounting:
    """Both surfaces must actually mount.

    Building a fresh APIRouter and include_router()-ing the OAuth factory's
    router into it looks right and is not: on an APIRouter, unlike an app,
    include_router leaves a pathless placeholder rather than flattening the
    child's routes. The callback silently did not exist, and the pathless
    entry read as an ungated route to the public-surface invariant.
    """

    def _paths(self):
        from nodes.discord._router import router

        return {getattr(route, "path", None) for route in router.routes}

    def test_all_routes_mount(self):
        assert {
            "/api/discord/callback",
            "/api/discord/interactions",
            "/api/discord/interactions/{account_id}",
        } <= self._paths()

    def test_no_pathless_route_entries(self):
        assert all(path for path in self._paths())

    def test_every_route_is_under_the_gated_api_prefix(self):
        """AuthMiddleware gates on the prefix; anything outside it would be
        served unauthenticated."""
        assert all(path.startswith("/api/discord/") for path in self._paths())


class TestInteractionTriggerIsDeployable:
    def test_registered_for_deployment(self):
        from constants import EVENT_TRIGGER_TYPES, POLLING_TRIGGER_TYPES, WORKFLOW_TRIGGER_TYPES

        from nodes.discord.discord_interaction import DiscordInteractionNode

        assert DiscordInteractionNode.type in EVENT_TRIGGER_TYPES
        assert DiscordInteractionNode.type in WORKFLOW_TRIGGER_TYPES
        assert DiscordInteractionNode.type not in POLLING_TRIGGER_TYPES

    def test_canary_type_matches_the_emitted_envelope(self):
        from services.deployment.canary_registry import cloudevent_type_for

        from nodes.discord._events import INTERACTION_CREATED_TYPE, discord_interaction_created
        from nodes.discord.discord_interaction import DiscordInteractionNode

        assert cloudevent_type_for(DiscordInteractionNode.type) == INTERACTION_CREATED_TYPE
        assert discord_interaction_created({"interaction_id": "1"}).type == INTERACTION_CREATED_TYPE

    def test_message_and_interaction_use_distinct_types(self):
        """One node maps to one CloudEvents type, so a shared type would make
        each trigger fire on the other's events."""
        from nodes.discord._events import INTERACTION_CREATED_TYPE, MESSAGE_RECEIVED_TYPE

        assert INTERACTION_CREATED_TYPE != MESSAGE_RECEIVED_TYPE

    @pytest.mark.parametrize(
        "params, event, expected",
        [
            ({}, {"interaction_type": 2, "command_name": "ping"}, True),
            ({"interaction_kind": "command"}, {"interaction_type": 3}, False),
            ({"interaction_kind": "component"}, {"interaction_type": 3}, True),
            ({"command_name": "ping"}, {"command_name": "ping"}, True),
            ({"command_name": "/ping"}, {"command_name": "ping"}, True),
            ({"command_name": "other"}, {"command_name": "ping"}, False),
            ({"custom_id": "btn"}, {"custom_id": "btn"}, True),
            ({"guild_id": "g1"}, {"guild_id": "g2"}, False),
        ],
    )
    def test_filter_matrix(self, params, event, expected):
        from nodes.discord._filters import build_interaction_filter

        base = {"account_id": "default"}
        base.update(event)
        assert build_interaction_filter(params)(base) is expected
