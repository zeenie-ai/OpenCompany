"""Contract tests for the Microsoft Graph nodes: msMail, msCalendar.

Each node is driven through the full NodeExecutor dispatch via the shared
`harness` fixture. Microsoft Graph HTTP is mocked with respx so no network
is touched, and the outgoing request (method + path + body) is asserted —
not just the parsed result — because that is the real contract with Graph.

The proactive token-refresh helper (`ensure_fresh_microsoft_token`) is
patched to a no-op here; it is a pure token-freshness concern exercised in
isolation by `test_ensure_fresh_token_*` below. Credentials reach the
`Connection` facade via `patched_container(auth_oauth_tokens=...)`.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from tests.nodes._mocks import patched_container, patched_pricing

pytestmark = pytest.mark.node_contract

GRAPH = "https://graph.microsoft.com/v1.0"

# An owner token so MicrosoftCredential.resolve() -> get_oauth_tokens returns
# a bearer the Connection facade can inject.
_OWNER_TOKENS = {"microsoft": {"access_token": "tok_ms", "email": "me@contoso.com", "name": "Me"}}


@contextmanager
def _no_refresh():
    """Skip the proactive-refresh side trip so tests exercise the Graph call."""
    with patch("nodes.microsoft._base.ensure_fresh_microsoft_token", new=AsyncMock(return_value="tok_ms")):
        yield


# ============================================================================
# msMail
# ============================================================================


class TestMsMailSend:
    @respx.mock
    async def test_send_posts_sendmail(self, harness):
        route = respx.post(f"{GRAPH}/me/sendMail").mock(return_value=httpx.Response(202))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {
                    "operation": "send",
                    "to": "alice@contoso.com, bob@contoso.com",
                    "subject": "Hi",
                    "body": "Hello there",
                    "body_type": "text",
                },
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["sent"] is True
        assert route.called
        sent = respx.calls.last.request
        assert sent.headers["Authorization"] == "Bearer tok_ms"
        import json as _json

        payload = _json.loads(sent.content)
        msg = payload["message"]
        assert msg["subject"] == "Hi"
        assert msg["body"]["contentType"] == "Text"
        assert [r["emailAddress"]["address"] for r in msg["toRecipients"]] == [
            "alice@contoso.com",
            "bob@contoso.com",
        ]

    async def test_send_missing_recipient_is_user_error(self, harness):
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "send", "subject": "x", "body": "y"})
        harness.assert_envelope(result, success=False)
        assert "recipient" in result["error"].lower()


class TestMsMailSearch:
    @respx.mock
    async def test_search_uses_search_param(self, harness):
        respx.get(f"{GRAPH}/me/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "AAA",
                            "subject": "Quarterly plan",
                            "from": {"emailAddress": {"address": "jane@contoso.com", "name": "Jane"}},
                            "receivedDateTime": "2026-08-01T10:00:00Z",
                            "bodyPreview": "hello",
                            "isRead": False,
                            "webLink": "https://outlook/AAA",
                        }
                    ]
                },
            )
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "search", "query": "plan", "search_max_results": 5})

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 1
        assert payload["messages"][0]["message_id"] == "AAA"
        assert payload["messages"][0]["from"] == "jane@contoso.com"
        sent = respx.calls.last.request
        assert sent.url.params["$search"] == '"plan"'
        assert sent.url.params["$top"] == "5"

    async def test_search_empty_query_is_user_error(self, harness):
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "search", "query": ""})
        harness.assert_envelope(result, success=False)
        assert "query" in result["error"].lower()


class TestMsMailReply:
    @respx.mock
    async def test_reply_posts_reply(self, harness):
        route = respx.post(f"{GRAPH}/me/messages/AAA/reply").mock(return_value=httpx.Response(202))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "reply", "reply_message_id": "AAA", "comment": "Thanks!"},
            )
        harness.assert_envelope(result, success=True)
        assert result["result"]["replied"] is True
        assert route.called

    @respx.mock
    async def test_reply_all_uses_replyall_endpoint(self, harness):
        route = respx.post(f"{GRAPH}/me/messages/AAA/replyAll").mock(return_value=httpx.Response(202))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "reply", "reply_message_id": "AAA", "comment": "Thanks all!", "reply_all": True},
            )
        harness.assert_envelope(result, success=True)
        assert route.called


class TestMsMailGraphError:
    @respx.mock
    async def test_graph_error_becomes_user_error(self, harness):
        respx.post(f"{GRAPH}/me/sendMail").mock(
            return_value=httpx.Response(
                403,
                json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}},
            )
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "send", "to": "a@contoso.com", "subject": "s", "body": "b"},
            )
        harness.assert_envelope(result, success=False)
        assert "ErrorAccessDenied" in result["error"] or "Access is denied" in result["error"]


# ============================================================================
# msCalendar
# ============================================================================


class TestMsCalendarCreate:
    @respx.mock
    async def test_create_posts_event(self, harness):
        respx.post(f"{GRAPH}/me/events").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "EV1",
                    "subject": "Standup",
                    "start": {"dateTime": "2026-08-10T14:00:00.0000000", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-08-10T14:30:00.0000000", "timeZone": "UTC"},
                    "location": {"displayName": "Room 1"},
                    "webLink": "https://outlook/EV1",
                },
            )
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msCalendar",
                {
                    "operation": "create",
                    "title": "Standup",
                    "start_time": "2026-08-10T14:00:00",
                    "end_time": "2026-08-10T14:30:00",
                    "location": "Room 1",
                    "attendees": "alice@contoso.com",
                },
            )
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["event_id"] == "EV1"
        assert payload["title"] == "Standup"
        sent = respx.calls.last.request
        import json as _json

        body = _json.loads(sent.content)
        assert body["subject"] == "Standup"
        assert body["start"]["dateTime"] == "2026-08-10T14:00:00"
        assert body["attendees"][0]["emailAddress"]["address"] == "alice@contoso.com"

    async def test_create_missing_title_is_user_error(self, harness):
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msCalendar",
                {"operation": "create", "start_time": "x", "end_time": "y"},
            )
        harness.assert_envelope(result, success=False)
        assert "title" in result["error"].lower()


class TestMsCalendarList:
    @respx.mock
    async def test_list_uses_calendarview(self, harness):
        route = respx.get(f"{GRAPH}/me/calendarView").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "EV1",
                            "subject": "Standup",
                            "start": {"dateTime": "2026-08-10T14:00:00.0000000"},
                            "end": {"dateTime": "2026-08-10T14:30:00.0000000"},
                            "location": {"displayName": "Room 1"},
                            "organizer": {"emailAddress": {"address": "boss@contoso.com"}},
                            "webLink": "https://outlook/EV1",
                        }
                    ]
                },
            )
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msCalendar",
                {"operation": "list", "start_date": "2026-08-01T00:00:00", "end_date": "2026-08-31T00:00:00"},
            )
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 1
        assert payload["events"][0]["event_id"] == "EV1"
        assert route.called
        sent = respx.calls.last.request
        assert sent.url.params["startDateTime"] == "2026-08-01T00:00:00"
        assert sent.url.params["endDateTime"] == "2026-08-31T00:00:00"


class TestMsCalendarDelete:
    @respx.mock
    async def test_delete_calls_delete(self, harness):
        route = respx.delete(f"{GRAPH}/me/events/EV1").mock(return_value=httpx.Response(204))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msCalendar", {"operation": "delete", "event_id": "EV1"})
        harness.assert_envelope(result, success=True)
        assert result["result"]["deleted"] is True
        assert route.called

    async def test_delete_missing_id_is_user_error(self, harness):
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msCalendar", {"operation": "delete"})
        harness.assert_envelope(result, success=False)
        assert "event id" in result["error"].lower()


class TestMsCalendarUpdate:
    @respx.mock
    async def test_update_patches_event(self, harness):
        route = respx.patch(f"{GRAPH}/me/events/EV1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "EV1", "subject": "Renamed", "start": {"dateTime": "x"}, "end": {"dateTime": "y"}},
            )
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msCalendar",
                {"operation": "update", "event_id": "EV1", "update_title": "Renamed"},
            )
        harness.assert_envelope(result, success=True)
        assert result["result"]["title"] == "Renamed"
        assert route.called
        import json as _json

        body = _json.loads(respx.calls.last.request.content)
        assert body == {"subject": "Renamed"}

    async def test_update_no_fields_is_user_error(self, harness):
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msCalendar", {"operation": "update", "event_id": "EV1"})
        harness.assert_envelope(result, success=False)
        assert "no update fields" in result["error"].lower()


# ============================================================================
# Params schema invariants (tool-facing flatness)
# ============================================================================


class TestParamsSchemas:
    def test_params_are_flat_no_refs(self):
        from nodes.microsoft.calendar import CalendarParams
        from nodes.microsoft.mail import MailParams

        for model in (MailParams, CalendarParams):
            schema = model.model_json_schema()
            assert "$defs" not in schema, f"{model.__name__} leaked $defs"
            assert "definitions" not in schema, f"{model.__name__} leaked definitions"


# ============================================================================
# Token freshness helper (unit)
# ============================================================================


class TestEnsureFreshToken:
    async def test_missing_tokens_raises_permission_error(self):
        from nodes.microsoft import _auth_helper

        auth = AsyncMock()
        auth.get_oauth_tokens = AsyncMock(return_value=None)
        with patch("services.plugin.deps.get_auth_service", return_value=auth):
            with pytest.raises(PermissionError) as exc:
                await _auth_helper.ensure_fresh_microsoft_token("owner")
        assert getattr(exc.value, "provider", None) == "microsoft"

    async def test_refresh_persists_new_token(self):
        from nodes.microsoft import _auth_helper

        # Force a refresh: clear any cached expiry so _needs_refresh() is True.
        _auth_helper._TOKEN_EXPIRY.pop("owner", None)

        auth = AsyncMock()
        auth.get_oauth_tokens = AsyncMock(return_value={"access_token": "old", "email": "me@contoso.com", "name": "Me", "scopes": "User.Read"})
        auth.get_oauth_refresh_token = AsyncMock(return_value="rt")
        auth.get_api_key = AsyncMock(side_effect=lambda k, *a, **kw: {"microsoft_client_id": "cid", "microsoft_client_secret": "sec"}.get(k))
        auth.store_oauth_tokens = AsyncMock(return_value=True)

        refresh_result = {"success": True, "access_token": "new", "refresh_token": "rt2", "expires_in": 3600}

        with patch("services.plugin.deps.get_auth_service", return_value=auth), patch(
            "nodes.microsoft._oauth.MicrosoftOAuth.refresh_access_token",
            new=AsyncMock(return_value=refresh_result),
        ):
            token = await _auth_helper.ensure_fresh_microsoft_token("owner")

        assert token == "new"
        auth.store_oauth_tokens.assert_awaited_once()
        _, kwargs = auth.store_oauth_tokens.call_args
        assert kwargs["access_token"] == "new"
        assert kwargs["refresh_token"] == "rt2"


# ============================================================================
# msMailReceive (polling trigger)
# ============================================================================


@contextmanager
def _fresh_token():
    """Skip the token-freshness resolve so ctx-free Graph helpers just run."""
    with patch("nodes.microsoft._base.ensure_fresh_microsoft_token", new=AsyncMock(return_value="tok_ms")):
        yield


class TestMsMailReceive:
    def test_registration(self):
        from services.node_registry import get_node_class

        cls = get_node_class("msMailReceive")
        assert cls is not None
        assert cls.mode == "polling"
        assert cls.event_type == "ms_mail_received"
        assert cls.task_queue == "triggers-poll"
        # Trigger nodes are not AI tools.
        assert getattr(cls, "usable_as_tool", False) is False

    def test_query_defaults_to_unread(self):
        from nodes.microsoft.mail_receive import _list_path, _query

        q = _query({"only_unread": True})
        assert q["$filter"] == "isRead eq false"
        assert _list_path({}) == "/me/mailFolders/inbox/messages"

    def test_query_with_filter_omits_orderby(self):
        # Graph rejects $filter (isRead/from) + $orderby (receivedDateTime)
        # with 400 InefficientFilter. When a filter is present, no $orderby.
        from nodes.microsoft.mail_receive import _query

        q = _query({"only_unread": True})
        assert "$orderby" not in q
        q2 = _query({"only_unread": True, "from_filter": "jane@contoso.com"})
        assert "$orderby" not in q2

    def test_query_without_filter_keeps_orderby(self):
        from nodes.microsoft.mail_receive import _query

        q = _query({"only_unread": False})
        assert "$filter" not in q
        assert q["$orderby"] == "receivedDateTime desc"

    def test_query_from_filter_and_folder(self):
        from nodes.microsoft.mail_receive import _list_path, _query

        q = _query({"only_unread": True, "from_filter": "jane@contoso.com"})
        assert "isRead eq false" in q["$filter"]
        assert "from/emailAddress/address eq 'jane@contoso.com'" in q["$filter"]
        assert _list_path({"folder": "sentitems"}) == "/me/mailFolders/sentitems/messages"

    def test_query_from_filter_escapes_single_quote(self):
        from nodes.microsoft.mail_receive import _query

        q = _query({"only_unread": False, "from_filter": "o'brien@contoso.com"})
        assert "from/emailAddress/address eq 'o''brien@contoso.com'" == q["$filter"]

    @respx.mock
    async def test_fetch_ids_hits_mailfolder_messages(self):
        from nodes.microsoft.mail_receive import MailReceiveNode

        route = respx.get(f"{GRAPH}/me/mailFolders/inbox/messages").mock(
            return_value=httpx.Response(200, json={"value": [{"id": "M1"}, {"id": "M2"}]})
        )
        node = MailReceiveNode()
        params = {"only_unread": True}
        with _fresh_token():
            ids = await node.fetch_ids(await node.setup_service(params), params)
        assert ids == {"M1", "M2"}
        assert route.called
        assert respx.calls.last.request.url.params["$filter"] == "isRead eq false"

    @respx.mock
    async def test_fetch_detail_summarizes(self):
        from nodes.microsoft.mail_receive import MailReceiveNode

        respx.get(f"{GRAPH}/me/messages/M1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "M1",
                    "subject": "Hello",
                    "from": {"emailAddress": {"address": "jane@contoso.com", "name": "Jane"}},
                    "toRecipients": [{"emailAddress": {"address": "me@contoso.com"}}],
                    "receivedDateTime": "2026-08-01T10:00:00Z",
                    "bodyPreview": "hi",
                    "body": {"content": "<p>hi</p>"},
                    "isRead": False,
                    "webLink": "https://outlook/M1",
                    "conversationId": "C1",
                },
            )
        )
        node = MailReceiveNode()
        with _fresh_token():
            detail = await node.fetch_detail(None, "M1", {})
        assert detail["id"] == "M1"
        assert detail["from"] == "jane@contoso.com"
        assert detail["subject"] == "Hello"
        assert detail["conversation_id"] == "C1"

    @respx.mock
    async def test_post_emit_marks_read_when_enabled(self):
        from nodes.microsoft.mail_receive import MailReceiveNode

        route = respx.patch(f"{GRAPH}/me/messages/M1").mock(return_value=httpx.Response(200, json={"id": "M1"}))
        node = MailReceiveNode()
        with _fresh_token():
            await node.post_emit(None, "M1", {"mark_as_read": True})
        assert route.called

    @respx.mock
    async def test_post_emit_noop_when_disabled(self):
        from nodes.microsoft.mail_receive import MailReceiveNode

        route = respx.patch(f"{GRAPH}/me/messages/M1").mock(return_value=httpx.Response(200))
        node = MailReceiveNode()
        with _fresh_token():
            await node.post_emit(None, "M1", {"mark_as_read": False})
        assert not route.called

    def test_output_params_flat(self):
        from nodes.microsoft.mail_receive import MailReceiveParams

        schema = MailReceiveParams.model_json_schema()
        assert "$defs" not in schema and "definitions" not in schema


# ============================================================================
# msMail attachments (list + download)
# ============================================================================

import base64  # noqa: E402

_FILE_ODATA = "#microsoft.graph.fileAttachment"
_ITEM_ODATA = "#microsoft.graph.itemAttachment"


def _attachment(name, *, odata=_FILE_ODATA, inline=False, content=b"", ctype="application/pdf", aid="A1"):
    a = {
        "@odata.type": odata,
        "id": aid,
        "name": name,
        "contentType": ctype,
        "size": len(content),
        "isInline": inline,
    }
    if content:
        a["contentBytes"] = base64.b64encode(content).decode()
    return a


class TestMsMailHasAttachments:
    def test_select_includes_hasattachments(self):
        from nodes.microsoft.mail import MailNode

        assert "hasAttachments" in MailNode._SELECT

    def test_receive_select_includes_hasattachments(self):
        from nodes.microsoft.mail_receive import _SELECT

        assert "hasAttachments" in _SELECT

    def test_summarize_surfaces_has_attachments(self):
        from nodes.microsoft.mail import _summarize

        out = _summarize({"id": "M1", "hasAttachments": True})
        assert out["has_attachments"] is True


class TestMsMailListAttachments:
    @respx.mock
    async def test_list_excludes_inline_by_default(self, harness):
        respx.get(f"{GRAPH}/me/messages/M1/attachments").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        _attachment("quote.pdf", aid="A1"),
                        _attachment("logo.png", inline=True, ctype="image/png", aid="A2"),
                        {"@odata.type": _ITEM_ODATA, "id": "A3", "name": "embedded.eml", "size": 10, "isInline": False},
                    ]
                },
            )
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "list_attachments", "message_id": "M1"})

        harness.assert_envelope(result, success=True)
        names = [a["name"] for a in result["result"]["attachments"]]
        assert "quote.pdf" in names
        assert "logo.png" not in names  # inline excluded by default
        # item attachment surfaces in listing but tagged non-file
        item = next(a for a in result["result"]["attachments"] if a["name"] == "embedded.eml")
        assert item["kind"] == "itemAttachment"
        pdf = next(a for a in result["result"]["attachments"] if a["name"] == "quote.pdf")
        assert pdf["kind"] == "file"
        # metadata-only fetch
        assert respx.calls.last.request.url.params["$select"] == "id,name,contentType,size,isInline"

    @respx.mock
    async def test_list_includes_inline_when_requested(self, harness):
        respx.get(f"{GRAPH}/me/messages/M1/attachments").mock(
            return_value=httpx.Response(200, json={"value": [_attachment("logo.png", inline=True, ctype="image/png")]})
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "list_attachments", "message_id": "M1", "include_inline": True},
            )
        assert result["result"]["count"] == 1

    async def test_list_requires_message_id(self, harness):
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "list_attachments"})
        harness.assert_envelope(result, success=False)
        assert "message_id" in result["error"].lower()


class TestMsMailDownloadAttachments:
    @respx.mock
    async def test_download_writes_file_and_returns_path(self, harness, tmp_path):
        pdf_bytes = b"%PDF-1.4 fake pdf body"
        respx.get(f"{GRAPH}/me/messages/M1/attachments").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        _attachment("quote.pdf", content=pdf_bytes, aid="A1"),
                        _attachment("logo.png", inline=True, content=b"img", ctype="image/png", aid="A2"),
                    ]
                },
            )
        )
        ctx = harness.build_context(workspace_dir=str(tmp_path))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "download_attachments", "message_id": "M1"},
                context=ctx,
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 1  # inline logo skipped
        att = payload["attachments"][0]
        assert att["filename"] == "quote.pdf"
        # file actually written under attachments/ with real bytes
        from pathlib import Path

        written = Path(att["path"])
        assert written.is_absolute() and written.exists()
        assert written.read_bytes() == pdf_bytes
        assert written.parent.name == "attachments"
        assert payload["download_dir"] == str(written.parent)
        # FileRef is kind=file (never audio), and NO raw bytes leak into output
        assert att["ref"]["kind"] == "file"
        assert "contentBytes" not in att and "data" not in att
        assert "contentBytes" not in str(payload)
        # inline recorded in skipped
        assert any(s["reason"] == "inline" for s in (payload.get("skipped") or []))

    @respx.mock
    async def test_download_single_attachment_by_id(self, harness, tmp_path):
        route = respx.get(f"{GRAPH}/me/messages/M1/attachments/A1").mock(
            return_value=httpx.Response(200, json=_attachment("quote.pdf", content=b"%PDF-1.4 x", aid="A1"))
        )
        ctx = harness.build_context(workspace_dir=str(tmp_path))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "download_attachments", "message_id": "M1", "attachment_id": "A1"},
                context=ctx,
            )
        harness.assert_envelope(result, success=True)
        assert route.called
        assert result["result"]["count"] == 1

    @respx.mock
    async def test_download_no_file_attachments_is_user_error(self, harness, tmp_path):
        respx.get(f"{GRAPH}/me/messages/M1/attachments").mock(
            return_value=httpx.Response(
                200,
                json={"value": [{"@odata.type": _ITEM_ODATA, "id": "A3", "name": "embedded.eml", "size": 10}]},
            )
        )
        ctx = harness.build_context(workspace_dir=str(tmp_path))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "download_attachments", "message_id": "M1"},
                context=ctx,
            )
        harness.assert_envelope(result, success=False)
        assert "no downloadable file attachments" in result["error"].lower()

    async def test_download_requires_message_id(self, harness, tmp_path):
        ctx = harness.build_context(workspace_dir=str(tmp_path))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "download_attachments"}, context=ctx)
        harness.assert_envelope(result, success=False)
        assert "message_id" in result["error"].lower()


# ============================================================================
# Shared mailbox (/users/{address}) routing
# ============================================================================

SHARED = "support@contoso.com"


class TestMailboxBaseHelper:
    def test_empty_is_me(self):
        from nodes.microsoft._base import mailbox_base

        assert mailbox_base("") == "/me"
        assert mailbox_base(None) == "/me"

    def test_address_is_users_path(self):
        from nodes.microsoft._base import mailbox_base

        assert mailbox_base(SHARED) == f"/users/{SHARED}"
        assert mailbox_base("  x@y.com  ") == "/users/x@y.com"

    def test_invalid_address_raises(self):
        from nodes.microsoft._base import mailbox_base
        from services.plugin import NodeUserError

        for bad in ("a/b", "has space"):
            with pytest.raises(NodeUserError):
                mailbox_base(bad)


class TestMsMailSharedMailbox:
    @respx.mock
    async def test_read_list_targets_shared_mailbox(self, harness):
        route = respx.get(f"{GRAPH}/users/{SHARED}/messages").mock(
            return_value=httpx.Response(200, json={"value": []})
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "read", "mailbox": SHARED})
        harness.assert_envelope(result, success=True)
        assert route.called

    @respx.mock
    async def test_send_from_shared_mailbox(self, harness):
        route = respx.post(f"{GRAPH}/users/{SHARED}/sendMail").mock(return_value=httpx.Response(202))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msMail",
                {"operation": "send", "mailbox": SHARED, "to": "a@contoso.com", "subject": "s", "body": "b"},
            )
        harness.assert_envelope(result, success=True)
        assert route.called

    @respx.mock
    async def test_empty_mailbox_still_uses_me(self, harness):
        route = respx.get(f"{GRAPH}/me/messages").mock(return_value=httpx.Response(200, json={"value": []}))
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute("msMail", {"operation": "read"})
        harness.assert_envelope(result, success=True)
        assert route.called


class TestMsMailReceiveSharedMailbox:
    def test_list_path_shared(self):
        from nodes.microsoft.mail_receive import _list_path

        assert _list_path({"mailbox": SHARED, "folder": "inbox"}) == f"/users/{SHARED}/mailFolders/inbox/messages"
        assert _list_path({"folder": "inbox"}) == "/me/mailFolders/inbox/messages"


class TestMsCalendarSharedMailbox:
    @respx.mock
    async def test_list_targets_shared_calendar(self, harness):
        route = respx.get(f"{GRAPH}/users/{SHARED}/calendarView").mock(
            return_value=httpx.Response(200, json={"value": []})
        )
        with _no_refresh(), patched_container(auth_oauth_tokens=_OWNER_TOKENS), patched_pricing():
            result = await harness.execute(
                "msCalendar",
                {"operation": "list", "mailbox": SHARED, "start_date": "2026-08-01T00:00:00", "end_date": "2026-08-31T00:00:00"},
            )
        harness.assert_envelope(result, success=True)
        assert route.called
