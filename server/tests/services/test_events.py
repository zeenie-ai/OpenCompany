"""Contract tests for the generalized event-source framework."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Iterable
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.node_contract


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ============================================================================
# WorkflowEvent envelope
# ============================================================================


class TestWorkflowEvent:
    def test_required_fields(self):
        from services.events import WorkflowEvent

        ev = WorkflowEvent(source="stripe://acct_1", type="stripe.charge.succeeded")
        assert ev.specversion == "1.0"
        assert ev.id  # uuid default
        assert ev.datacontenttype == "application/json"

    def test_round_trip_json(self):
        from services.events import WorkflowEvent

        ev = WorkflowEvent(
            source="stripe://acct_1",
            type="stripe.charge.succeeded",
            data={"amount": 1000},
        )
        restored = WorkflowEvent.model_validate(json.loads(ev.model_dump_json()))
        assert restored.type == ev.type
        assert restored.data == {"amount": 1000}
        assert restored.id == ev.id

    def test_matches_type_glob(self):
        from services.events import WorkflowEvent

        ev = WorkflowEvent(source="stripe://x", type="stripe.charge.succeeded")
        assert ev.matches_type("all") is True
        assert ev.matches_type("") is True
        assert ev.matches_type("stripe.charge.succeeded") is True
        assert ev.matches_type("stripe.charge.*") is True
        assert ev.matches_type("stripe.*") is True
        assert ev.matches_type("payment_intent.*") is False

    def test_from_legacy(self):
        from services.events import WorkflowEvent

        ev = WorkflowEvent.from_legacy("whatsapp_message_received", {"text": "hi"})
        assert ev.type == "whatsapp_message_received"
        assert ev.data == {"text": "hi"}
        assert ev.source.startswith("legacy://")

    def test_agent_capability_is_a_safe_cloudevent(self):
        from services.events import WorkflowEvent

        ev = WorkflowEvent.agent_capability(
            "agent-7",
            capability_kind="skill",
            capability_name="write-todos",
            state="loaded",
            workflow_id="7",
            execution_id="12",
            root_execution_id="10",
            target_node_id="master-skill-7",
            tool_call_id="call-1",
            content_hash="sha256-safe",
            event_id="occurrence-1",
        )
        dumped = ev.model_dump(mode="json", exclude_none=True)

        assert dumped["specversion"] == "1.0"
        assert dumped["id"] == "occurrence-1"
        assert dumped["source"] == "opencompany://services/agent"
        assert dumped["type"] == "com.opencompany.agent.skill.loaded"
        assert dumped["subject"] == "agent-7"
        assert dumped["dataschema"].endswith("agent.skill.loaded.json")
        assert dumped["data"]["author_node_id"] == "agent-7"
        # Operational scope belongs to data, not invalid snake_case
        # CloudEvents extension attributes.
        assert "execution_id" not in {key for key in dumped if dumped[key] is not None}
        for secret_field in ("prompt", "tool_args", "result", "instructions", "resource", "error"):
            assert secret_field not in dumped["data"]

    @pytest.mark.parametrize(
        ("kind", "state"),
        [("skill", "started"), ("tool", "loaded"), ("other", "started")],
    )
    def test_agent_capability_rejects_invalid_kind_state_pairs(self, kind, state):
        from services.events import WorkflowEvent

        with pytest.raises(ValueError):
            WorkflowEvent.agent_capability(
                "agent-1",
                capability_kind=kind,
                capability_name="capability",
                state=state,
            )

    def test_capability_broadcaster_keeps_cloudevent_envelope_intact(self):
        from services.status_broadcaster import StatusBroadcaster

        broadcaster = StatusBroadcaster()
        broadcaster.broadcast = AsyncMock()
        event = _run(
            broadcaster.broadcast_agent_capability(
                "agent-1",
                capability_kind="tool",
                capability_name="write_todos",
                state="started",
                workflow_id="1",
                event_id="tool-start-1",
            )
        )

        assert event.subject == "agent-1"
        wire = broadcaster.broadcast.await_args.args[0]
        assert wire["type"] == "agent_capability"
        assert wire["data"]["specversion"] == "1.0"
        assert wire["data"]["id"] == "tool-start-1"
        assert wire["data"]["type"] == "com.opencompany.agent.tool.started"
        assert wire["data"]["data"]["workflow_id"] == "1"


# ============================================================================
# Verifiers
# ============================================================================


class TestStripeVerifier:
    SECRET = "whsec_test"

    def _sign(self, body: bytes, ts: int | None = None) -> dict:
        ts = ts if ts is not None else int(time.time())
        signed = f"{ts}.".encode() + body
        sig = hmac.new(self.SECRET.encode(), signed, hashlib.sha256).hexdigest()
        return {"Stripe-Signature": f"t={ts},v1={sig}"}

    def test_valid_signature_passes(self):
        from services.events import StripeVerifier

        body = b'{"id":"evt_1"}'
        StripeVerifier.verify(self._sign(body), body, self.SECRET)

    def test_tampered_body_rejected(self):
        from services.events import StripeVerifier

        body = b'{"id":"evt_1"}'
        headers = self._sign(body)
        with pytest.raises(ValueError):
            StripeVerifier.verify(headers, b'{"id":"evt_2"}', self.SECRET)

    def test_missing_header_rejected(self):
        from services.events import StripeVerifier

        with pytest.raises(ValueError, match="missing"):
            StripeVerifier.verify({}, b"{}", self.SECRET)

    def test_malformed_header_rejected(self):
        from services.events import StripeVerifier

        with pytest.raises(ValueError):
            StripeVerifier.verify({"Stripe-Signature": "garbage"}, b"{}", self.SECRET)


class TestGitHubVerifier:
    def test_round_trip(self):
        from services.events import GitHubVerifier

        secret = "shh"
        body = b'{"action":"opened"}'
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        GitHubVerifier.verify({"X-Hub-Signature-256": sig}, body, secret)

    def test_tampered_rejected(self):
        from services.events import GitHubVerifier

        with pytest.raises(ValueError):
            GitHubVerifier.verify({"X-Hub-Signature-256": "sha256=deadbeef"}, b"{}", "shh")


class TestStandardWebhooksVerifier:
    def test_round_trip(self):
        from services.events import StandardWebhooksVerifier

        secret_raw = b"super-secret-key-bytes"
        secret = "whsec_" + base64.b64encode(secret_raw).decode()
        body = b'{"foo":"bar"}'
        msg_id = "msg_abc"
        ts = "1700000000"
        signed = f"{msg_id}.{ts}.".encode() + body
        sig = base64.b64encode(hmac.new(secret_raw, signed, hashlib.sha256).digest()).decode()
        headers = {
            "webhook-id": msg_id,
            "webhook-timestamp": ts,
            "webhook-signature": f"v1,{sig}",
        }
        StandardWebhooksVerifier.verify(headers, body, secret)

    def test_tampered_rejected(self):
        from services.events import StandardWebhooksVerifier

        with pytest.raises(ValueError):
            StandardWebhooksVerifier.verify(
                {"webhook-id": "1", "webhook-timestamp": "1", "webhook-signature": "v1,bad"},
                b"{}",
                "whsec_AAAA",
            )


class TestHmacVerifier:
    def test_round_trip(self):
        from services.events import HmacVerifier

        secret = "shh"
        body = b'{"x":1}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        HmacVerifier.verify({"X-Signature-256": sig}, body, secret)


# ============================================================================
# PollingEventSource loop
# ============================================================================


class TestPollingEventSource:
    def test_emits_what_poll_once_returns_then_sleeps(self):
        from services.events import PollingEventSource, WorkflowEvent

        class FakePolling(PollingEventSource):
            type = "fake.polling"
            poll_interval_default = 0  # tight loop for the test

            def __init__(self):
                super().__init__()
                self.calls = 0

            async def poll_once(self, state) -> Iterable[WorkflowEvent]:
                self.calls += 1
                if self.calls == 1:
                    return [WorkflowEvent(source="x", type="fake.tick", data={"n": 1})]
                self._stopped = True
                return []

        async def drain():
            src = FakePolling()
            seen = []
            async for ev in src.emit():
                seen.append(ev)
                if len(seen) >= 1:
                    src._stopped = True
            return seen

        events = _run(drain())
        assert len(events) == 1
        assert events[0].type == "fake.tick"


# ============================================================================
# WebhookSource — handle() integration
# ============================================================================


class TestWebhookSourceHandle:
    def _build_source(self, secret_value: str | None):
        from services.events import StripeVerifier, WebhookSource, WorkflowEvent

        class _Cred:
            @classmethod
            async def resolve(cls):
                if secret_value is None:
                    raise PermissionError
                return {"api_key": "sk_test", "test_secret": secret_value}

        class FakeSource(WebhookSource):
            type = "fake.hook"
            path = "fake"
            verifier = StripeVerifier
            secret_field = "test_secret"
            credential = _Cred

            async def shape(self, request, body, payload):
                return WorkflowEvent(source="fake://x", type="fake.event", data=payload)

        return FakeSource()

    def _signed_request(self, body: bytes, secret: str):
        ts = int(time.time())
        signed = f"{ts}.".encode() + body
        sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

        class FakeReq:
            headers = {"Stripe-Signature": f"t={ts},v1={sig}"}

            async def body(self):
                return body

        return FakeReq()

    def test_valid_signature_dispatches(self):
        src = self._build_source(secret_value="whsec_test")
        body = b'{"event":"payload"}'
        req = self._signed_request(body, "whsec_test")
        with patch("services.event_waiter.dispatch") as dispatch:
            ev = _run(src.handle(req))
        assert ev.type == "fake.event"
        dispatch.assert_called_once()
        called_type, called_event = dispatch.call_args[0]
        assert called_type == "fake.hook"
        assert called_event is ev

    def test_tampered_signature_raises_400(self):
        from fastapi import HTTPException

        src = self._build_source(secret_value="whsec_test")
        body = b'{"event":"payload"}'
        req = self._signed_request(body, "whsec_other")  # signed with different secret
        with pytest.raises(HTTPException) as exc:
            _run(src.handle(req))
        assert exc.value.status_code == 400

    def test_missing_secret_accepts_unverified(self):
        """No secret captured yet -> log warning, accept event."""
        src = self._build_source(secret_value=None)
        body = b'{"event":"payload"}'

        class FakeReq:
            headers = {}

            async def body(self):
                return body

        with patch("services.event_waiter.dispatch") as dispatch:
            ev = _run(src.handle(FakeReq()))
        assert ev.type == "fake.event"
        dispatch.assert_called_once()


# ============================================================================
# DaemonEventSource lifecycle (mocked ProcessService)
# ============================================================================


class TestDaemonEventSource:
    def test_start_calls_process_service_start(self):
        from services.events import DaemonEventSource

        class FakeDaemon(DaemonEventSource):
            type = "fake.daemon"
            process_name = "fake-daemon"
            binary_name = "echo"  # always on PATH

            def build_command(self, secrets):
                return "echo hello"

            def parse_line(self, stream, line):
                return None

        async def go():
            with (
                patch("services.events.daemon.shutil.which", return_value="/usr/bin/echo"),
                patch("services.events.daemon.get_process_service") as get_ps,
            ):
                ps = get_ps.return_value

                async def fake_start(**kwargs):
                    return {"success": True, "result": {"pid": 4242}}

                ps.start.side_effect = fake_start

                async def fake_stop(**kwargs):
                    return {"success": True}

                ps.stop.side_effect = fake_stop

                src = FakeDaemon()
                result = await src.start()
                assert result["success"] is True
                assert src.pid == 4242
                ps.start.assert_called_once()
                # Cancel the tail task so the test exits cleanly.
                await src.stop()
                assert src.pid is None

        _run(go())

    def test_start_fails_when_binary_missing(self):
        from services.events import DaemonEventSource

        class FakeDaemon(DaemonEventSource):
            type = "fake.daemon"
            process_name = "fake-daemon"
            binary_name = "definitely-not-on-path-zzzzz"
            install_hint = "see docs"

            def build_command(self, secrets):
                return "x"

        async def go():
            with patch("services.events.daemon.shutil.which", return_value=None):
                src = FakeDaemon()
                result = await src.start()
                assert result["success"] is False
                assert "PATH" in result["error"]
                assert "see docs" in result["error"]

        _run(go())
