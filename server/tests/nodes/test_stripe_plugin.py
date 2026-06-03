"""Contract tests for the Stripe plugin (Wave 12 framework version).

The plugin is now a thin specialisation of ``services.events`` —
``StripeListenSource`` (DaemonEventSource) supervises ``stripe listen``
and ``StripeWebhookSource`` (WebhookSource) receives forwarded events.
These tests verify the plugin wiring and the receive-node reshape;
end-to-end smoke against the real CLI requires the binary on PATH.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.node_contract


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _signed(body: bytes, secret: str) -> dict:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}"}


def _fake_request(body: bytes, headers: dict | None = None):
    """Return an object with the two attrs WebhookSource.handle uses."""

    class R:
        pass

    r = R()
    r.headers = headers or {}

    async def _body():
        return body

    r.body = _body
    return r


# ============================================================================
# StripeWebhookSource — shape() and end-to-end handle()
# ============================================================================


class TestStripeWebhookShape:
    SECRET = "whsec_test_xyz"

    def _stub_secret_resolution(self):
        return patch(
            "nodes.stripe._source.StripeCredential.resolve",
            AsyncMock(return_value={"api_key": "sk_test", "stripe_webhook_secret": self.SECRET}),
        )

    def test_shape_extracts_stripe_fields(self):
        from nodes.stripe._source import get_webhook_source

        src = get_webhook_source()
        payload = {
            "id": "evt_1",
            "type": "charge.succeeded",
            "created": 1700000000,
            "livemode": False,
            "account": "acct_test",
            "data": {"object": {"amount": 1000}},
        }

        class FakeReq:
            headers = {}

            async def body(self):
                return b""

        ev = _run(src.shape(FakeReq(), b"", payload))
        assert ev.id == "evt_1"
        assert ev.type == "stripe.charge.succeeded"
        assert ev.source == "stripe://acct_test"
        assert ev.data == {"object": {"amount": 1000}}

    def test_handle_dispatches_on_valid_signature(self):
        from nodes.stripe._source import get_webhook_source

        src = get_webhook_source()
        body = json.dumps({"id": "evt_2", "type": "charge.succeeded", "data": {}}).encode()
        req = _fake_request(body, headers=_signed(body, self.SECRET))
        with self._stub_secret_resolution(), patch("services.event_waiter.dispatch") as dispatch:
            ev = _run(src.handle(req))
        assert ev.type == "stripe.charge.succeeded"
        dispatch.assert_called_once()
        ev_type, dispatched = dispatch.call_args[0]
        assert ev_type == "stripe.webhook"
        assert dispatched.id == "evt_2"

    def test_handle_rejects_tampered_signature(self):
        from fastapi import HTTPException
        from nodes.stripe._source import get_webhook_source

        src = get_webhook_source()
        body = json.dumps({"id": "evt_3", "type": "charge.succeeded"}).encode()
        req = _fake_request(body, headers=_signed(body, "whsec_other"))
        with self._stub_secret_resolution():
            with pytest.raises(HTTPException) as exc:
                _run(src.handle(req))
        assert exc.value.status_code == 400


# ============================================================================
# StripeReceiveNode — filter + reshape
# ============================================================================


class TestStripeReceiveFilter:
    def _filter(self, params: dict | None = None):
        from nodes.stripe.stripe_receive import StripeReceiveNode, StripeReceiveParams

        node = StripeReceiveNode()
        return node.build_filter(StripeReceiveParams(**(params or {})))

    def _ev(self, stripe_type: str, livemode: bool = False):
        from services.events import WorkflowEvent

        return WorkflowEvent(
            source="stripe://acct_1",
            type=f"stripe.{stripe_type}",
            data={"livemode": livemode},
        )

    def test_all_matches_anything(self):
        f = self._filter({"event_type_filter": "all"})
        assert f(self._ev("charge.succeeded")) is True
        assert f(self._ev("payment_intent.created")) is True

    def test_exact_match(self):
        f = self._filter({"event_type_filter": "charge.succeeded"})
        assert f(self._ev("charge.succeeded")) is True
        assert f(self._ev("charge.refunded")) is False

    def test_wildcard_prefix(self):
        f = self._filter({"event_type_filter": "charge.*"})
        assert f(self._ev("charge.succeeded")) is True
        assert f(self._ev("charge.refunded")) is True
        assert f(self._ev("payment_intent.created")) is False

    def test_livemode_filter(self):
        live = self._filter({"livemode_filter": "live"})
        test = self._filter({"livemode_filter": "test"})
        assert live(self._ev("charge.succeeded", livemode=True)) is True
        assert live(self._ev("charge.succeeded", livemode=False)) is False
        assert test(self._ev("charge.succeeded", livemode=False)) is True
        assert test(self._ev("charge.succeeded", livemode=True)) is False


class TestStripeReceiveReshape:
    def test_shape_output_extracts_stripe_fields(self):
        from nodes.stripe.stripe_receive import StripeReceiveNode
        from services.events import WorkflowEvent

        ev = WorkflowEvent(
            id="evt_1",
            source="stripe://acct_42",
            type="stripe.charge.succeeded",
            data={
                "request": {"id": "req_99"},
                "data": {"object": {"amount": 2500}},
                "livemode": True,
                "api_version": "2024-04-10",
            },
        )
        out = StripeReceiveNode().shape_output(ev)
        assert out["event_id"] == "evt_1"
        assert out["event_type"] == "charge.succeeded"
        assert out["request_id"] == "req_99"
        assert out["account"] == "acct_42"
        assert out["livemode"] is True
        assert out["data"] == {"object": {"amount": 2500}}


# ============================================================================
# StripeActionNode — pass-through over the CLI
# ============================================================================


class TestStripeActionPassthrough:
    @pytest.fixture
    def cli_capture(self):
        captured: list[list[str]] = []

        async def fake_run(*, binary, argv, **kwargs):
            # Stripe CLI uses ~/.config/stripe/config.toml — no credential injection.
            captured.append(list(argv))
            return {"success": True, "result": {"id": "x"}, "stdout": "{}"}

        return captured, fake_run

    def test_command_is_shlex_split(self, cli_capture):
        captured, fake = cli_capture
        with patch("nodes.stripe.stripe_action.run_cli_command", AsyncMock(side_effect=fake)):
            from nodes.stripe.stripe_action import StripeActionNode, StripeActionParams

            node = StripeActionNode()
            result = _run(node.run(None, StripeActionParams(command="customers create --email a@b.com")))
            assert result["success"] is True
            assert captured == [["customers", "create", "--email", "a@b.com"]]

    def test_quoted_args_preserved(self, cli_capture):
        captured, fake = cli_capture
        with patch("nodes.stripe.stripe_action.run_cli_command", AsyncMock(side_effect=fake)):
            from nodes.stripe.stripe_action import StripeActionNode, StripeActionParams

            node = StripeActionNode()
            _run(node.run(None, StripeActionParams(command="customers create --name 'Acme Inc'")))
            assert captured == [["customers", "create", "--name", "Acme Inc"]]

    def test_empty_command_raises(self):
        from nodes.stripe.stripe_action import StripeActionNode, StripeActionParams

        node = StripeActionNode()
        with pytest.raises(RuntimeError, match="command is required"):
            _run(node.run(None, StripeActionParams(command="   ")))

    def test_cli_failure_raises(self):
        from nodes.stripe.stripe_action import StripeActionNode, StripeActionParams

        async def fake_fail(*, binary, argv, **kwargs):
            return {"success": False, "error": "stripe: unknown command 'frobnicate'"}

        with patch("nodes.stripe.stripe_action.run_cli_command", AsyncMock(side_effect=fake_fail)):
            node = StripeActionNode()
            with pytest.raises(RuntimeError, match="frobnicate"):
                _run(node.run(None, StripeActionParams(command="frobnicate")))


# ============================================================================
# Plugin self-registration
# ============================================================================


class TestStripePluginRegistration:
    def test_ws_handlers_registered(self):
        import nodes.stripe  # noqa: F401
        from services.ws_handler_registry import get_ws_handlers

        registered = get_ws_handlers()
        for name in (
            "stripe_login",
            "stripe_logout",
            "stripe_connect",
            "stripe_disconnect",
            "stripe_reconnect",
            "stripe_status",
            "stripe_trigger",
        ):
            assert name in registered, f"WS handler '{name}' not registered"

    def test_webhook_source_registered(self):
        import nodes.stripe  # noqa: F401
        from services.events import WEBHOOK_SOURCES

        assert "stripe" in WEBHOOK_SOURCES
        assert WEBHOOK_SOURCES["stripe"].type == "stripe.webhook"

    def test_node_classes_registered(self):
        import nodes.stripe  # noqa: F401
        from services.node_registry import get_node_class

        assert get_node_class("stripeReceive").__name__ == "StripeReceiveNode"
        assert get_node_class("stripeAction").__name__ == "StripeActionNode"

    def test_credential_registered(self):
        import nodes.stripe  # noqa: F401
        from services.plugin.credential import CREDENTIAL_REGISTRY

        # Stripe CLI manages auth at ~/.config/stripe/config.toml; the
        # credential class is a thin marker keyed by "stripe" (no api_key).
        assert "stripe" in CREDENTIAL_REGISTRY

    def test_output_schemas_registered(self):
        import nodes.stripe  # noqa: F401
        from services.node_output_schemas import NODE_OUTPUT_SCHEMAS

        assert "stripeReceive" in NODE_OUTPUT_SCHEMAS
        assert "stripeAction" in NODE_OUTPUT_SCHEMAS

    def test_action_node_is_ai_tool(self):
        from nodes.stripe.stripe_action import StripeActionNode

        assert StripeActionNode.usable_as_tool is True

    def test_receive_node_subscribes_to_stripe_webhook(self):
        from nodes.stripe.stripe_receive import StripeReceiveNode

        assert StripeReceiveNode.event_type == "stripe.webhook"

    def test_listen_source_has_correct_namespace(self):
        from nodes.stripe._source import get_listen_source

        src = get_listen_source()
        assert src.process_name == "stripe-listen"
        assert src.workflow_namespace == "_stripe"
        # Empty binary_name disables the framework's PATH check; the
        # plugin resolves the binary itself via ensure_stripe_cli (which
        # falls back to an OS-cache download via
        # ``core.paths.package_dir('stripe')`` on systems without a
        # system install of the Stripe CLI).
        assert src.binary_name == ""
