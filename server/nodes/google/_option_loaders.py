"""Google Workspace ``loadOptionsMethod`` loaders.

Wave 11.I, milestone M.2. Each function is registered with
``services.ws_handler_registry.register_option_loader`` from
``__init__.py``.

Reuses :func:`._auth_helper.get_google_credentials` so the OAuth dance
is identical to the workflow-execution path. ``params`` may carry
``account_mode`` / ``customer_id`` (multi-tenant customer mode) -- the
auth helper falls back to owner tokens otherwise.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from googleapiclient.discovery import build

from ._auth_helper import get_google_credentials


async def _google_service(api: str, version: str, params: Dict[str, Any]):
    """Build a googleapiclient service under the right OAuth credentials."""
    creds = await get_google_credentials(params, {})
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: build(api, version, credentials=creds))


async def load_gmail_labels(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Gmail labels for the label-filter selector on gmailReceive and
    gmail (search)."""
    service = await _google_service("gmail", "v1", params)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: service.users().labels().list(userId="me").execute())
    labels = response.get("labels", [])
    # Stable sort: system labels alphabetical first, then user labels.
    labels.sort(
        key=lambda label: (
            label.get("type") != "system",
            (label.get("name") or "").lower(),
        )
    )
    return [{"value": label["id"], "label": label.get("name") or label["id"]} for label in labels]


async def load_calendar_list(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Calendar list for the calendarId picker on calendar CRUD."""
    service = await _google_service("calendar", "v3", params)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: service.calendarList().list().execute())
    entries = response.get("items", [])
    # Primary first, rest alphabetised.
    entries.sort(key=lambda c: (not c.get("primary", False), (c.get("summary") or "").lower()))
    return [
        {
            "value": c.get("id", ""),
            "label": c.get("summary") or c.get("id", ""),
            "description": "Primary" if c.get("primary") else c.get("description", ""),
        }
        for c in entries
    ]


async def load_drive_folders(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Drive folders for the folderId picker on drive upload/list."""
    service = await _google_service("drive", "v3", params)
    loop = asyncio.get_event_loop()
    query = "mimeType='application/vnd.google-apps.folder' and trashed=false"
    response = await loop.run_in_executor(
        None,
        lambda: service.files().list(q=query, fields="files(id, name, parents)", pageSize=200).execute(),
    )
    folders = response.get("files", [])
    folders.sort(key=lambda f: (f.get("name") or "").lower())
    return [{"value": f.get("id", ""), "label": f.get("name") or f.get("id", "")} for f in folders]


async def load_tasklists(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Task lists for the tasklistId picker on tasks CRUD."""
    service = await _google_service("tasks", "v1", params)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: service.tasklists().list().execute())
    lists = response.get("items", [])
    lists.sort(key=lambda label: (label.get("title") or "").lower())
    return [{"value": label.get("id", ""), "label": label.get("title") or label.get("id", "")} for label in lists]
