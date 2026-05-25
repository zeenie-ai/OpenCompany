"""Gmail-specific shared helpers (Wave 11.D.4).

Used by both gmail (send/search/read) and gmail_receive (polling).
Lives here because it's Gmail-internal — not reusable across other
Google Workspace plugins.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Set

from ._base import run_sync


async def poll_gmail_ids(service, query: str, max_results: int = 20) -> Set[str]:
    """Fetch message IDs matching a Gmail query."""
    result = await run_sync(
        lambda: service.users()
        .messages()
        .list(
            userId="me",
            q=query,
            maxResults=max_results,
        )
        .execute()
    )
    return {m.get("id") for m in result.get("messages", []) if m.get("id")}


async def fetch_email_details(service, message_id: str) -> Dict[str, Any]:
    """Fetch full email message + format it for output."""
    result = await run_sync(
        lambda: service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",
            metadataHeaders=["From", "To", "Subject", "Date", "Cc", "Bcc"],
        )
        .execute()
    )
    return format_message(result, include_body=True)


async def mark_email_as_read(service, message_id: str) -> None:
    """Remove the UNREAD label from a message."""
    await run_sync(
        lambda: service.users()
        .messages()
        .modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        )
        .execute()
    )


def format_message(message: Dict[str, Any], include_body: bool = False) -> Dict[str, Any]:
    """Convert a raw Gmail message payload into a flat, LLM-friendly dict."""
    headers: Dict[str, str] = {}
    payload = message.get("payload", {})
    for header in payload.get("headers", []):
        name = header.get("name", "").lower()
        if name in ("from", "to", "subject", "date", "cc", "bcc"):
            headers[name] = header.get("value", "")

    formatted: Dict[str, Any] = {
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", ""),
        "labels": message.get("labelIds", []),
        "size_estimate": message.get("sizeEstimate", 0),
    }

    if include_body:
        formatted["body"] = _extract_body(payload)

    attachments = _extract_attachments(payload)
    if attachments:
        formatted["attachments"] = attachments

    return formatted


def _extract_body(payload: Dict[str, Any]) -> str:
    """Extract text body from a (possibly multipart) Gmail payload."""
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    body = ""
    for part in payload.get("parts", []):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        if mime == "text/html" and data and not body:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif mime.startswith("multipart/"):
            nested = _extract_body(part)
            if nested:
                return nested
    return body


def _extract_attachments(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten attachment metadata from a multipart payload."""
    attachments: List[Dict[str, Any]] = []
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size": part.get("body", {}).get("size", 0),
                    "attachment_id": part.get("body", {}).get("attachmentId", ""),
                }
            )
        if part.get("parts"):
            attachments.extend(_extract_attachments(part))
    return attachments
