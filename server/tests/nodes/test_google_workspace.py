"""Contract tests for Google Workspace nodes.

Covers: gmail, gmailReceive, calendar, drive, sheets, tasks, contacts.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/google_workspace/`. All handlers build a
googleapiclient service via `build(...)` and depend on
`get_google_credentials()` for OAuth. We patch both at the handler-module
import site so no real OAuth or googleapiclient traffic is required.

Patching strategy
-----------------
- `services.handlers.<file>.get_google_credentials` -> AsyncMock returning a
  MagicMock that quacks like `google.oauth2.credentials.Credentials`.
- `services.handlers.<file>.build` -> MagicMock returning a chainable service
  so expressions like
  `service.users().messages().send(userId=..., body=...).execute()`
  resolve without real API traffic.

Handlers run the google client calls via `loop.run_in_executor(None, fn)` so
the MagicMock `.execute()` call happens synchronously inside the executor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytestmark = pytest.mark.node_contract


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _patch_creds(module_name: str, creds_return=None, side_effect=None):
    """Patch `get_google_credentials` inside a handler module.

    By default returns a MagicMock that stands in for a Credentials instance.
    Pass `side_effect=ValueError(...)` to simulate missing credentials.
    """
    # Scaling-branch: shared helper lives at nodes.google._auth_helper.
    # `module_name` is retained for API symmetry but not part of the patch path.
    _ = module_name
    target = "nodes.google._auth_helper.get_google_credentials"
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = creds_return if creds_return is not None else MagicMock(name="Credentials")
    return patch(target, new=AsyncMock(**kwargs))


def _patch_build(module_name: str, service_mock):
    """Patch googleapiclient `build(...)` to return a provided service MagicMock."""
    # Scaling-branch: googleapiclient.build is imported once in
    # nodes.google._base and all 6 service plugins share it.
    _ = module_name
    target = "nodes.google._base.build"
    return patch(target, return_value=service_mock)


# ============================================================================
# gmail
# ============================================================================


class TestGmail:
    async def test_send_happy_path(self, harness):
        service = MagicMock(name="GmailService")
        send_exec = service.users().messages().send.return_value.execute
        send_exec.return_value = {
            "id": "msg-1",
            "threadId": "thr-1",
            "labelIds": ["SENT"],
        }

        with _patch_creds("googleGmail"), _patch_build("googleGmail", service):
            result = await harness.execute(
                "googleGmail",
                {
                    "operation": "send",
                    "to": "bob@example.com",
                    "subject": "hi",
                    "body": "hello world",
                    "body_type": "text",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["message_id"] == "msg-1"
        assert payload["thread_id"] == "thr-1"
        assert payload["to"] == "bob@example.com"
        assert payload["subject"] == "hi"
        # send was called with userId=me and a base64 raw body
        send_call = service.users().messages().send.call_args
        assert send_call.kwargs["userId"] == "me"
        assert "raw" in send_call.kwargs["body"]
        assert isinstance(send_call.kwargs["body"]["raw"], str)

    async def test_send_missing_recipient_errors(self, harness):
        service = MagicMock(name="GmailService")
        with _patch_creds("googleGmail"), _patch_build("googleGmail", service):
            result = await harness.execute(
                "googleGmail",
                {
                    "operation": "send",
                    # no to
                    "subject": "x",
                    "body": "y",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "recipient" in result["error"].lower() or "to" in result["error"].lower()

    async def test_search_happy_path_without_body(self, harness):
        service = MagicMock(name="GmailService")
        # messages.list -> two message ids
        service.users().messages().list.return_value.execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}],
            "resultSizeEstimate": 2,
        }

        # messages.get returns metadata for each
        def _make_msg(mid):
            return {
                "id": mid,
                "threadId": f"t-{mid}",
                "snippet": f"snippet-{mid}",
                "labelIds": ["INBOX"],
                "sizeEstimate": 10,
                "payload": {
                    "headers": [
                        {"name": "From", "value": "alice@example.com"},
                        {"name": "Subject", "value": f"Subject {mid}"},
                    ],
                },
            }

        service.users().messages().get.return_value.execute.side_effect = [
            _make_msg("m1"),
            _make_msg("m2"),
        ]

        with _patch_creds("googleGmail"), _patch_build("googleGmail", service):
            result = await harness.execute(
                "googleGmail",
                {
                    "operation": "search",
                    "query": "from:alice",
                    "max_results": 2,
                    "include_body": False,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 2
        assert payload["query"] == "from:alice"
        assert payload["messages"][0]["message_id"] == "m1"
        assert payload["messages"][0]["from"] == "alice@example.com"
        assert payload["messages"][1]["subject"] == "Subject m2"

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_creds("googleGmail"), _patch_build("googleGmail", MagicMock()):
            result = await harness.execute(
                "googleGmail",
                {"operation": "bogus"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_missing_credentials_short_circuits(self, harness):
        with (
            _patch_creds("googleGmail", side_effect=ValueError("Google Workspace not connected")),
            _patch_build("googleGmail", MagicMock()),
        ):
            result = await harness.execute(
                "googleGmail",
                {
                    "operation": "send",
                    "to": "bob@example.com",
                    "subject": "x",
                    "body": "y",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "not connected" in result["error"].lower()


# ============================================================================
# gmailReceive (polling trigger - single-tick happy path)
# ============================================================================


class TestGmailReceive:
    async def test_happy_path_returns_newest_email(self, harness):
        """Baseline empty -> first poll finds new id -> fetch details -> return."""
        service = MagicMock(name="GmailService")
        # Baseline call: empty
        # Subsequent call: returns one new message
        list_exec = service.users().messages().list.return_value.execute
        list_exec.side_effect = [
            {"messages": []},  # baseline
            {"messages": [{"id": "new-1"}]},  # new
        ]
        service.users().messages().get.return_value.execute.return_value = {
            "id": "new-1",
            "threadId": "t-new-1",
            "snippet": "hello",
            "labelIds": ["INBOX", "UNREAD"],
            "sizeEstimate": 42,
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Greetings"},
                ],
            },
        }

        async def _instant_sleep(_seconds):
            return None

        with (
            _patch_creds("googleGmail"),
            _patch_build("googleGmail", service),
            patch("asyncio.sleep", new=_instant_sleep),
            patch("services.status_broadcaster.get_status_broadcaster") as gsb,
            patch("services.event_waiter.dispatch", return_value=1),
        ):
            gsb.return_value = MagicMock(update_node_status=AsyncMock())
            result = await harness.execute(
                "googleGmailReceive",
                {
                    "filter_query": "is:unread",
                    "label_filter": "INBOX",
                    "mark_as_read": False,
                    "poll_interval": 30,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["message_id"] == "new-1"
        assert payload["subject"] == "Greetings"
        assert payload["from"] == "alice@example.com"

    async def test_mark_as_read_calls_modify(self, harness):
        service = MagicMock(name="GmailService")
        list_exec = service.users().messages().list.return_value.execute
        list_exec.side_effect = [
            {"messages": []},
            {"messages": [{"id": "new-2"}]},
        ]
        service.users().messages().get.return_value.execute.return_value = {
            "id": "new-2",
            "threadId": "t2",
            "snippet": "s",
            "labelIds": ["UNREAD"],
            "sizeEstimate": 1,
            "payload": {"headers": []},
        }
        modify_exec = service.users().messages().modify.return_value.execute

        async def _instant_sleep(_seconds):
            return None

        with (
            _patch_creds("googleGmail"),
            _patch_build("googleGmail", service),
            patch("asyncio.sleep", new=_instant_sleep),
            patch("services.status_broadcaster.get_status_broadcaster") as gsb,
            patch("services.event_waiter.dispatch", return_value=1),
        ):
            gsb.return_value = MagicMock(update_node_status=AsyncMock())
            result = await harness.execute(
                "googleGmailReceive",
                {
                    "filter_query": "is:unread",
                    "label_filter": "INBOX",
                    "mark_as_read": True,
                    "poll_interval": 30,
                },
            )

        harness.assert_envelope(result, success=True)
        # modify must have been called (to remove UNREAD)
        assert modify_exec.called
        modify_call = service.users().messages().modify.call_args
        assert modify_call.kwargs["id"] == "new-2"
        assert modify_call.kwargs["body"] == {"removeLabelIds": ["UNREAD"]}

    async def test_missing_credentials_returns_error(self, harness):
        with _patch_creds("googleGmail", side_effect=ValueError("Google Workspace not connected")):
            result = await harness.execute(
                "googleGmailReceive",
                {
                    "filter_query": "is:unread",
                    "poll_interval": 30,
                },
            )

        harness.assert_envelope(result, success=False)
        assert "not connected" in result["error"].lower()


# ============================================================================
# calendar
# ============================================================================


class TestCalendar:
    async def test_create_happy_path(self, harness):
        service = MagicMock(name="CalendarService")
        service.events().insert.return_value.execute.return_value = {
            "id": "evt-1",
            "summary": "Standup",
            "start": {"dateTime": "2026-04-15T09:00:00Z"},
            "end": {"dateTime": "2026-04-15T09:30:00Z"},
            "htmlLink": "https://cal/evt-1",
            "status": "confirmed",
            "created": "2026-04-14T10:00:00Z",
        }

        with _patch_creds("googleCalendar"), _patch_build("googleCalendar", service):
            result = await harness.execute(
                "googleCalendar",
                {
                    "operation": "create",
                    "title": "Standup",
                    "start_time": "2026-04-15T09:00:00Z",
                    "end_time": "2026-04-15T09:30:00Z",
                    "attendees": "a@example.com, b@example.com",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["event_id"] == "evt-1"
        assert payload["title"] == "Standup"
        # insert was called with sendUpdates=all
        insert_call = service.events().insert.call_args
        assert insert_call.kwargs["sendUpdates"] == "all"
        body = insert_call.kwargs["body"]
        assert body["summary"] == "Standup"
        assert len(body["attendees"]) == 2

    async def test_delete_missing_event_id_errors(self, harness):
        service = MagicMock(name="CalendarService")
        with _patch_creds("googleCalendar"), _patch_build("googleCalendar", service):
            result = await harness.execute(
                "googleCalendar",
                {"operation": "delete"},
            )

        harness.assert_envelope(result, success=False)
        assert "event id" in result["error"].lower()

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_creds("googleCalendar"), _patch_build("googleCalendar", MagicMock()):
            result = await harness.execute(
                "googleCalendar",
                {"operation": "archive"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_missing_credentials_short_circuits(self, harness):
        with (
            _patch_creds("googleCalendar", side_effect=ValueError("Google Workspace not connected")),
            _patch_build("googleCalendar", MagicMock()),
        ):
            result = await harness.execute(
                "googleCalendar",
                {
                    "operation": "create",
                    "title": "x",
                    "start_time": "2026-04-15T09:00:00Z",
                    "end_time": "2026-04-15T09:30:00Z",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "not connected" in result["error"].lower()


# ============================================================================
# drive
# ============================================================================


class TestDrive:
    async def test_list_happy_path(self, harness):
        service = MagicMock(name="DriveService")
        service.files().list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "f1",
                    "name": "Report.pdf",
                    "mimeType": "application/pdf",
                    "size": "1024",
                    "webViewLink": "https://drive/f1",
                    "createdTime": "2026-04-01T00:00:00Z",
                    "modifiedTime": "2026-04-10T00:00:00Z",
                    "parents": ["root"],
                    "owners": [{"emailAddress": "alice@example.com"}],
                },
            ],
            "nextPageToken": None,
        }

        with _patch_creds("googleDrive"), _patch_build("googleDrive", service):
            result = await harness.execute(
                "googleDrive",
                {
                    "operation": "list",
                    "max_results": 50,
                    "file_types": "all",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 1
        assert payload["files"][0]["file_id"] == "f1"
        assert payload["files"][0]["owner"] == "alice@example.com"
        # query always includes trashed=false
        list_call = service.files().list.call_args
        assert "trashed = false" in list_call.kwargs["q"]

    async def test_share_happy_path(self, harness):
        service = MagicMock(name="DriveService")
        service.permissions().create.return_value.execute.return_value = {
            "id": "perm-1",
            "type": "user",
            "role": "reader",
            "emailAddress": "bob@example.com",
        }
        service.files().get.return_value.execute.return_value = {
            "id": "file-x",
            "name": "Shared.txt",
            "webViewLink": "https://drive/shared",
        }

        with _patch_creds("googleDrive"), _patch_build("googleDrive", service):
            result = await harness.execute(
                "googleDrive",
                {
                    "operation": "share",
                    "file_id": "file-x",
                    "email": "bob@example.com",
                    "role": "reader",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["permission_id"] == "perm-1"
        assert payload["shared_with"] == "bob@example.com"
        assert payload["file_name"] == "Shared.txt"

    async def test_upload_missing_source_errors(self, harness):
        service = MagicMock(name="DriveService")
        with _patch_creds("googleDrive"), _patch_build("googleDrive", service):
            result = await harness.execute(
                "googleDrive",
                {
                    "operation": "upload",
                    "filename": "x.txt",
                    # no file_url and no file_content
                },
            )

        harness.assert_envelope(result, success=False)
        assert "file_url" in result["error"].lower() or "file_content" in result["error"].lower()

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_creds("googleDrive"), _patch_build("googleDrive", MagicMock()):
            result = await harness.execute(
                "googleDrive",
                {"operation": "trash"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# sheets
# ============================================================================


class TestSheets:
    async def test_read_happy_path(self, harness):
        service = MagicMock(name="SheetsService")
        service.spreadsheets().values().get.return_value.execute.return_value = {
            "range": "Sheet1!A1:B2",
            "majorDimension": "ROWS",
            "values": [["a", "b"], ["c", "d"]],
        }

        with _patch_creds("googleSheets"), _patch_build("googleSheets", service):
            result = await harness.execute(
                "googleSheets",
                {
                    "operation": "read",
                    "spreadsheet_id": "sheet-123",
                    "range": "Sheet1!A1:B2",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["rows"] == 2
        assert payload["columns"] == 2
        assert payload["values"] == [["a", "b"], ["c", "d"]]

    async def test_write_happy_path_with_1d_list(self, harness):
        """1D list is auto-wrapped to 2D before sending to API."""
        service = MagicMock(name="SheetsService")
        service.spreadsheets().values().update.return_value.execute.return_value = {
            "updatedRange": "Sheet1!A1:C1",
            "updatedRows": 1,
            "updatedColumns": 3,
            "updatedCells": 3,
        }

        with _patch_creds("googleSheets"), _patch_build("googleSheets", service):
            result = await harness.execute(
                "googleSheets",
                {
                    "operation": "write",
                    "spreadsheet_id": "sheet-123",
                    "range": "Sheet1!A1",
                    "values": ["x", "y", "z"],
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["updated_cells"] == 3
        # body must have been wrapped to 2D
        update_call = service.spreadsheets().values().update.call_args
        body_values = update_call.kwargs["body"]["values"]
        assert body_values == [["x", "y", "z"]]

    async def test_read_missing_range_errors(self, harness):
        service = MagicMock(name="SheetsService")
        with _patch_creds("googleSheets"), _patch_build("googleSheets", service):
            result = await harness.execute(
                "googleSheets",
                {
                    "operation": "read",
                    "spreadsheet_id": "sheet-123",
                    # no range
                },
            )

        harness.assert_envelope(result, success=False)
        assert "range" in result["error"].lower()

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_creds("googleSheets"), _patch_build("googleSheets", MagicMock()):
            result = await harness.execute(
                "googleSheets",
                {"operation": "clear"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# tasks
# ============================================================================


class TestTasks:
    async def test_create_happy_path(self, harness):
        service = MagicMock(name="TasksService")
        service.tasks().insert.return_value.execute.return_value = {
            "id": "task-1",
            "title": "Buy milk",
            "notes": "2 gallons",
            "due": "2026-04-20T00:00:00.000Z",
            "status": "needsAction",
            "selfLink": "https://tasks/task-1",
        }

        with _patch_creds("googleTasks"), _patch_build("googleTasks", service):
            result = await harness.execute(
                "googleTasks",
                {
                    "operation": "create",
                    "title": "Buy milk",
                    "notes": "2 gallons",
                    "due_date": "2026-04-20",  # no T - handler should upgrade
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["task_id"] == "task-1"
        assert payload["title"] == "Buy milk"
        # due_date without T was upgraded to T00:00:00.000Z
        insert_call = service.tasks().insert.call_args
        assert insert_call.kwargs["body"]["due"] == "2026-04-20T00:00:00.000Z"

    async def test_complete_reads_then_updates(self, harness):
        """Complete should do a get then update with status=completed."""
        service = MagicMock(name="TasksService")
        service.tasks().get.return_value.execute.return_value = {
            "id": "task-9",
            "title": "Review",
            "status": "needsAction",
        }
        service.tasks().update.return_value.execute.return_value = {
            "id": "task-9",
            "title": "Review",
            "status": "completed",
            "completed": "2026-04-15T12:00:00Z",
        }

        with _patch_creds("googleTasks"), _patch_build("googleTasks", service):
            result = await harness.execute(
                "googleTasks",
                {"operation": "complete", "task_id": "task-9"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["status"] == "completed"
        update_call = service.tasks().update.call_args
        assert update_call.kwargs["body"]["status"] == "completed"

    async def test_delete_missing_task_id_errors(self, harness):
        service = MagicMock(name="TasksService")
        with _patch_creds("googleTasks"), _patch_build("googleTasks", service):
            result = await harness.execute(
                "googleTasks",
                {"operation": "delete"},
            )

        harness.assert_envelope(result, success=False)
        assert "task id" in result["error"].lower()

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_creds("googleTasks"), _patch_build("googleTasks", MagicMock()):
            result = await harness.execute(
                "googleTasks",
                {"operation": "star"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# contacts
# ============================================================================


class TestContacts:
    async def test_create_happy_path(self, harness):
        service = MagicMock(name="PeopleService")
        service.people().createContact.return_value.execute.return_value = {
            "resourceName": "people/c123",
            "names": [{"displayName": "Alice Smith", "givenName": "Alice", "familyName": "Smith"}],
            "emailAddresses": [{"value": "alice@example.com", "metadata": {"primary": True}}],
            "phoneNumbers": [],
            "organizations": [],
            "photos": [],
        }

        with _patch_creds("googleContacts"), _patch_build("googleContacts", service):
            result = await harness.execute(
                "googleContacts",
                {
                    "operation": "create",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "email": "alice@example.com",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        contact = payload["contact"]
        assert contact["resource_name"] == "people/c123"
        assert contact["display_name"] == "Alice Smith"
        assert contact["email"] == "alice@example.com"

    async def test_search_happy_path(self, harness):
        service = MagicMock(name="PeopleService")
        service.people().searchContacts.return_value.execute.return_value = {
            "results": [
                {
                    "person": {
                        "resourceName": "people/c1",
                        "names": [{"displayName": "Bob", "givenName": "Bob", "familyName": ""}],
                        "emailAddresses": [{"value": "bob@example.com", "metadata": {"primary": True}}],
                        "phoneNumbers": [],
                        "organizations": [],
                        "photos": [],
                    }
                }
            ]
        }

        with _patch_creds("googleContacts"), _patch_build("googleContacts", service):
            result = await harness.execute(
                "googleContacts",
                {
                    "operation": "search",
                    "query": "bob",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 1
        assert payload["contacts"][0]["email"] == "bob@example.com"

    async def test_create_missing_first_name_errors(self, harness):
        service = MagicMock(name="PeopleService")
        with _patch_creds("googleContacts"), _patch_build("googleContacts", service):
            result = await harness.execute(
                "googleContacts",
                {
                    "operation": "create",
                    # no first_name
                    "email": "x@example.com",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "first name" in result["error"].lower()

    async def test_unknown_operation_returns_error(self, harness):
        with _patch_creds("googleContacts"), _patch_build("googleContacts", MagicMock()):
            result = await harness.execute(
                "googleContacts",
                {"operation": "merge"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_missing_credentials_short_circuits(self, harness):
        with (
            _patch_creds("googleContacts", side_effect=ValueError("Google Workspace not connected")),
            _patch_build("googleContacts", MagicMock()),
        ):
            result = await harness.execute(
                "googleContacts",
                {
                    "operation": "create",
                    "first_name": "Alice",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "not connected" in result["error"].lower()
