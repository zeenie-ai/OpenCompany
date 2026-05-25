"""Contract tests for whatsapp nodes: whatsappSend, whatsappDb, whatsappReceive.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/whatsapp/`. The handlers proxy all traffic
to the Go `whatsapp-rpc` service via a WebSocket RPC client in
`routers/whatsapp.py`. We patch the two entry points the handlers use:

  - `nodes.whatsapp._service.handle_whatsapp_send` - sending messages
  - `nodes.whatsapp._service.whatsapp_rpc_call`    - generic RPC calls
  - `nodes.whatsapp._service.handle_whatsapp_chat_history` - chat history RPC

For `whatsappReceive` we use the same event-waiter stub pattern as
`test_telegram_social.py` - patching the module reference imported by both
`services.node_executor` and `services.handlers.triggers`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import patched_broadcaster, patched_container


pytestmark = pytest.mark.node_contract


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _patch_whatsapp_send(return_value):
    """Patch nodes.whatsapp._service.handle_whatsapp_send at the import site."""
    return patch(
        "nodes.whatsapp._service.handle_whatsapp_send",
        new=AsyncMock(return_value=return_value),
    )


def _patch_rpc_call(return_value=None, *, side_effect=None):
    """Patch nodes.whatsapp._service.whatsapp_rpc_call.

    ``return_value`` may be a dict or list. ``side_effect`` may be a callable
    for per-method routing.
    """
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = return_value if return_value is not None else {}
    return patch("nodes.whatsapp._service.whatsapp_rpc_call", new=AsyncMock(**kwargs))


def _patch_chat_history_handler(return_value):
    return patch(
        "nodes.whatsapp._service.handle_whatsapp_chat_history",
        new=AsyncMock(return_value=return_value),
    )


# ============================================================================
# whatsappSend
# ============================================================================


class TestWhatsappSend:
    async def test_text_happy_path_to_phone(self, harness):
        with _patch_whatsapp_send({"success": True}):
            result = await harness.execute(
                "whatsappSend",
                {
                    "recipient_type": "phone",
                    "phone": "15551234567",
                    "message_type": "text",
                    "message": "hello world",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["status"] == "sent"
        assert payload["recipient"] == "15551234567"
        assert payload["recipient_type"] == "phone"
        assert payload["message_type"] == "text"
        assert payload["preview"] == "hello world"

    async def test_format_markdown_transforms_message(self, harness):
        """When format_markdown=true, parameters.message is rewritten before RPC call."""
        captured = {}

        async def fake_send(params):
            captured["message"] = params.get("message")
            return {"success": True}

        with (
            patch(
                "nodes.whatsapp._service.handle_whatsapp_send",
                new=AsyncMock(side_effect=fake_send),
            ),
            patch(
                "services.markdown_formatter.to_whatsapp",
                return_value="*bold*",
            ),
        ):
            result = await harness.execute(
                "whatsappSend",
                {
                    "recipient_type": "self",
                    "message_type": "text",
                    "message": "**bold**",
                    "format_markdown": True,
                },
            )

        harness.assert_envelope(result, success=True)
        assert captured["message"] == "*bold*"
        assert result["result"]["recipient"] == "self"

    async def test_channel_rejects_contact_message_type(self, harness):
        """Channels only allow text/image/video/audio/document."""
        with _patch_whatsapp_send({"success": True}) as mock_send:
            result = await harness.execute(
                "whatsappSend",
                {
                    "recipient_type": "channel",
                    "channel_jid": "120363123@newsletter",
                    "message_type": "contact",
                    "contact_name": "Alice",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "channel" in result["error"].lower()
        # Must short-circuit before hitting the RPC
        mock_send.assert_not_awaited()

    async def test_missing_phone_returns_error(self, harness):
        with _patch_whatsapp_send({"success": True}) as mock_send:
            result = await harness.execute(
                "whatsappSend",
                {
                    "recipient_type": "phone",
                    # no phone
                    "message_type": "text",
                    "message": "hi",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "phone" in result["error"].lower()
        mock_send.assert_not_awaited()

    async def test_rpc_failure_surfaces_as_error_envelope(self, harness):
        with _patch_whatsapp_send({"success": False, "error": "not connected"}):
            result = await harness.execute(
                "whatsappSend",
                {
                    "recipient_type": "self",
                    "message_type": "text",
                    "message": "ping",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "not connected" in result["error"].lower()


# ============================================================================
# whatsappDb - representative subset of 5 operations
# ============================================================================


class TestWhatsappDb:
    async def test_chat_history_individual_happy_path(self, harness):
        chat_response = {
            "success": True,
            "messages": [
                {"message_id": "m1", "text": "hi", "message_type": "text"},
                {"message_id": "m2", "text": "yo", "message_type": "text"},
            ],
            "total": 2,
            "has_more": False,
        }
        with _patch_chat_history_handler(chat_response), _patch_rpc_call({}):
            result = await harness.execute(
                "whatsappDb",
                {
                    "operation": "chat_history",
                    "chat_type": "individual",
                    "phone": "15551234567",
                    "limit": 10,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["operation"] == "chat_history"
        assert payload["count"] == 2
        # Index is base_offset + i + 1
        assert payload["messages"][0]["index"] == 1
        assert payload["messages"][1]["index"] == 2

    async def test_chat_history_missing_phone_errors(self, harness):
        with _patch_chat_history_handler({"success": True, "messages": []}), _patch_rpc_call({}):
            result = await harness.execute(
                "whatsappDb",
                {"operation": "chat_history", "chat_type": "individual"},
            )

        harness.assert_envelope(result, success=False)
        assert "phone" in result["error"].lower()

    async def test_search_groups_truncates_and_shapes(self, harness):
        """search_groups returns only {jid, name} and reports truncation."""
        groups = [{"jid": f"g{i}@g.us", "name": f"Group {i}", "extra_field": "noise"} for i in range(5)]
        # Handler runs data.get('success', True) BEFORE isinstance(data, list)
        # (documented bug), so the RPC stub must return a dict envelope here.
        with _patch_rpc_call({"success": True, "result": groups}):
            result = await harness.execute(
                "whatsappDb",
                {"operation": "search_groups", "query": "", "limit": 2},
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["total"] == 5
        assert payload["returned"] == 2
        assert payload["has_more"] is True
        assert all(set(g.keys()) == {"jid", "name"} for g in payload["groups"])
        assert payload["hint"] is not None  # truncation hint populated

    async def test_channel_messages_with_invite_link_resolves_jid(self, harness):
        """Invite links should trigger a newsletter_info lookup to resolve JID."""
        resolved_jid = "120363REAL@newsletter"

        async def fake_rpc(method, params=None):
            if method == "newsletter_info":
                # _resolve_to_jid reads result from dict
                return {"result": {"jid": resolved_jid}}
            if method == "newsletter_messages":
                # Capture for later assertion via closure
                fake_rpc.captured_messages_params = params
                return [{"message_id": "n1", "message_type": "text"}]
            return {}

        fake_rpc.captured_messages_params = None

        with _patch_rpc_call(side_effect=fake_rpc):
            result = await harness.execute(
                "whatsappDb",
                {
                    "operation": "channel_messages",
                    "channel_jid": "https://whatsapp.com/channel/abc",
                    "channel_count": 5,
                },
            )

        harness.assert_envelope(result, success=True)
        # The channel_messages RPC must have been called with the resolved JID
        assert fake_rpc.captured_messages_params["jid"] == resolved_jid
        assert result["result"]["count"] == 1

    async def test_channel_create_happy_path(self, harness):
        """Creating a channel by name returns the RPC result merged into payload."""
        created = {"jid": "120363NEW@newsletter", "name": "My Channel"}
        with _patch_rpc_call(created):
            result = await harness.execute(
                "whatsappDb",
                {
                    "operation": "channel_create",
                    "channel_name": "My Channel",
                    "channel_description": "desc",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["operation"] == "channel_create"
        assert payload["jid"] == "120363NEW@newsletter"
        assert payload["name"] == "My Channel"

    async def test_contact_profile_pic_happy_path(self, harness):
        pic_result = {"url": "https://example.com/pic.jpg"}
        with _patch_rpc_call(pic_result):
            result = await harness.execute(
                "whatsappDb",
                {
                    "operation": "contact_profile_pic",
                    "profile_pic_jid": "15551234567@s.whatsapp.net",
                    "preview": True,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["operation"] == "contact_profile_pic"
        assert payload["url"] == "https://example.com/pic.jpg"

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_rpc_call({}):
            result = await harness.execute(
                "whatsappDb",
                {"operation": "bogus_op"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_rpc_exception_becomes_error_envelope(self, harness):
        with _patch_rpc_call(side_effect=Exception("rpc down")):
            result = await harness.execute(
                "whatsappDb",
                {"operation": "list_contacts", "limit": 10},
            )

        harness.assert_envelope(result, success=False)
        assert "rpc down" in result["error"].lower()


# ============================================================================
# whatsappReceive (generic trigger via handle_trigger_node + event_waiter)
# ============================================================================


def _make_waiter_stub(*, canned_event=None, is_trigger=True, wait_side_effect=None):
    """Fake event_waiter module stub for the generic trigger path.

    Scaling-branch plugin trigger does::

        waiter = event_waiter.register(...)   # sync
        event_data = await waiter.future      # awaitable

    So `register` is a sync MagicMock and `waiter_obj.future` is a
    pre-resolved asyncio.Future (or raises the side-effect exception).
    """
    import asyncio

    loop = asyncio.get_event_loop()
    future = loop.create_future()
    if wait_side_effect is not None:
        future.set_exception(wait_side_effect)
    else:
        future.set_result(canned_event or {})

    waiter_obj = MagicMock(name="Waiter")
    waiter_obj.id = "waiter-test-id"
    waiter_obj.future = future

    stub = MagicMock(name="event_waiter_module")
    stub.is_trigger_node = MagicMock(return_value=is_trigger)
    stub.get_trigger_config = MagicMock(
        return_value=MagicMock(
            node_type="whatsappReceive",
            event_type="whatsapp_message_received",
            display_name="WhatsApp Message",
        )
    )
    stub.register = AsyncMock(return_value=waiter_obj)
    # Kept for back-compat with tests still asserting wait_for_event calls.
    if wait_side_effect is not None:
        stub.wait_for_event = AsyncMock(side_effect=wait_side_effect)
    else:
        stub.wait_for_event = AsyncMock(return_value=canned_event or {})
    stub.get_backend_mode = MagicMock(return_value="asyncio.Future")
    stub.cancel = MagicMock(return_value=True)
    stub.dispatch = MagicMock(return_value=1)
    return stub


class TestWhatsappReceive:
    CANNED = {
        "message_id": "wam_1",
        "sender": "15551234567@s.whatsapp.net",
        "sender_phone": "15551234567",
        "chat_id": "15551234567@s.whatsapp.net",
        "message_type": "text",
        "text": "hello there",
        "is_group": False,
        "is_from_me": False,
        "push_name": "Alice",
        "timestamp": "2026-04-15T12:00:00",
        "is_forwarded": False,
    }

    async def test_happy_path_returns_canned_event(self, harness):
        waiter = _make_waiter_stub(canned_event=self.CANNED)

        with patched_broadcaster(), patched_container(), patch("services.event_waiter", waiter):
            result = await harness.execute(
                "whatsappReceive",
                {
                    "filter": "all",
                    "messageTypeFilter": "all",
                    "ignoreOwnMessages": True,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["text"] == "hello there"
        assert payload["sender_phone"] == "15551234567"
        # Plugin trigger calls register synchronously; waiter.future is awaited.
        waiter.register.assert_called_once()

    async def test_cancellation_propagates_as_error(self, harness):
        import asyncio

        waiter = _make_waiter_stub(wait_side_effect=asyncio.CancelledError())

        with patched_broadcaster(), patched_container(), patch("services.event_waiter", waiter):
            result = await harness.execute(
                "whatsappReceive",
                {"filter": "all"},
            )

        harness.assert_envelope(result, success=False)
        assert "cancel" in result["error"].lower()

    async def test_waiter_exception_surfaces_as_error(self, harness):
        waiter = _make_waiter_stub(wait_side_effect=RuntimeError("redis stream down"))

        with patched_broadcaster(), patched_container(), patch("services.event_waiter", waiter):
            result = await harness.execute(
                "whatsappReceive",
                {"filter": "keywords", "keywords": "urgent,fire"},
            )

        harness.assert_envelope(result, success=False)
        assert "redis stream down" in result["error"].lower()
