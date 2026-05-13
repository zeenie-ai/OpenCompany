"""Google Drive — Wave 11.D.4 inlined."""

from __future__ import annotations

import base64
import io
from typing import List, Literal, Optional

import httpx
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import GoogleCredential

from .._base import build_google_service, run_sync, track_google_usage


_UPLOAD = {"displayOptions": {"show": {"operation": ["upload"]}}}
_DOWNLOAD = {"displayOptions": {"show": {"operation": ["download"]}}}
_LIST = {"displayOptions": {"show": {"operation": ["list"]}}}
_SHARE = {"displayOptions": {"show": {"operation": ["share"]}}}
_DOWNLOAD_OR_SHARE = {"displayOptions": {"show": {"operation": ["download", "share"]}}}


class DriveParams(BaseModel):
    operation: Literal["upload", "download", "list", "share"] = "list"

    file_id: Optional[str] = Field(default=None, json_schema_extra=_DOWNLOAD_OR_SHARE)

    # Upload
    filename: Optional[str] = Field(default=None, json_schema_extra=_UPLOAD)
    file_url: Optional[str] = Field(default=None, json_schema_extra=_UPLOAD)
    file_content: Optional[str] = Field(default=None, json_schema_extra=_UPLOAD)
    mime_type: str = Field(default="application/octet-stream", json_schema_extra=_UPLOAD)
    description: Optional[str] = Field(default=None, json_schema_extra=_UPLOAD)

    # List + Upload share folder_id
    folder_id: Optional[str] = Field(
        default=None,
        description="Target folder (upload) or scope folder (list).",
        json_schema_extra={
            "loadOptionsMethod": "googleDriveFolders",
            "displayOptions": {"show": {"operation": ["upload", "list"]}},
        },
    )

    # Download
    output_format: Literal["base64", "url"] = Field(default="base64", json_schema_extra=_DOWNLOAD)

    # List
    query: Optional[str] = Field(default=None, json_schema_extra=_LIST)
    file_types: str = Field(default="all", json_schema_extra=_LIST)
    order_by: str = Field(default="modifiedTime desc", json_schema_extra=_LIST)
    max_results: int = Field(default=20, ge=1, le=1000, json_schema_extra=_LIST)

    # Share
    email: Optional[str] = Field(default=None, json_schema_extra=_SHARE)
    role: Literal["reader", "commenter", "writer"] = Field(default="reader", json_schema_extra=_SHARE)
    send_notification: bool = Field(default=True, json_schema_extra=_SHARE)
    message: Optional[str] = Field(default=None, json_schema_extra=_SHARE)

    model_config = ConfigDict(extra="ignore")


class DriveOutput(BaseModel):
    operation: Optional[str] = None
    file_id: Optional[str] = None
    name: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    web_link: Optional[str] = None
    download_link: Optional[str] = None
    download_url: Optional[str] = None
    created_time: Optional[str] = None
    content_base64: Optional[str] = None
    files: Optional[List[dict]] = None
    count: Optional[int] = None
    next_page_token: Optional[str] = None
    permission_id: Optional[str] = None
    file_name: Optional[str] = None
    shared_with: Optional[str] = None

    model_config = ConfigDict(extra="allow")


_FILE_TYPE_QUERY = {
    'folder': "mimeType = 'application/vnd.google-apps.folder'",
    'document': "mimeType = 'application/vnd.google-apps.document'",
    'spreadsheet': "mimeType = 'application/vnd.google-apps.spreadsheet'",
    'image': "mimeType contains 'image/'",
}


class DriveNode(ActionNode):
    type = "googleDrive"
    display_name = "Drive"
    subtitle = "File Operations"
    group = ("google", "tool")
    description = "Google Drive upload / download / list / share files"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (GoogleCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = DriveParams
    Output = DriveOutput

    @Operation("dispatch", cost={"service": "drive", "action": "op", "count": 1})
    async def dispatch(self, ctx: NodeContext, params: DriveParams) -> DriveOutput:
        svc = await build_google_service("drive", "v3", params.model_dump(), ctx.raw)
        files_svc = svc.files()
        op = params.operation

        if op == "upload":
            if not params.filename:
                raise RuntimeError("Filename is required")
            if not params.file_url and not params.file_content:
                raise RuntimeError("Either file_url or file_content is required")

            metadata = {'name': params.filename}
            if params.folder_id:
                metadata['parents'] = [params.folder_id]
            if params.description:
                metadata['description'] = params.description

            mime_type = params.mime_type
            if params.file_url:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(params.file_url, timeout=60.0)
                    resp.raise_for_status()
                    file_bytes = resp.content
                    if mime_type == 'application/octet-stream' and 'content-type' in resp.headers:
                        mime_type = resp.headers['content-type'].split(';')[0]
            else:
                file_bytes = base64.b64decode(params.file_content)

            media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
            result = await run_sync(lambda: files_svc.create(
                body=metadata, media_body=media,
                fields='id, name, mimeType, size, webViewLink, webContentLink, createdTime',
            ).execute())
            await track_google_usage("google_drive", ctx.node_id, "upload", 1, ctx.raw)
            return DriveOutput(
                operation="upload",
                file_id=result.get('id'),
                name=result.get('name'),
                mime_type=result.get('mimeType'),
                size=int(result.get('size')) if result.get('size') else None,
                web_link=result.get('webViewLink'),
                download_link=result.get('webContentLink'),
                created_time=result.get('createdTime'),
            )

        if op == "download":
            if not params.file_id:
                raise RuntimeError("File ID is required")

            metadata = await run_sync(lambda: files_svc.get(
                fileId=params.file_id,
                fields='id, name, mimeType, size, webViewLink, webContentLink',
            ).execute())

            if params.output_format == "url":
                await track_google_usage("google_drive", ctx.node_id, "download", 1, ctx.raw)
                return DriveOutput(
                    operation="download",
                    file_id=metadata.get('id'),
                    name=metadata.get('name'),
                    mime_type=metadata.get('mimeType'),
                    size=int(metadata.get('size')) if metadata.get('size') else None,
                    download_url=metadata.get('webContentLink'),
                    web_link=metadata.get('webViewLink'),
                )

            def _download():
                request = files_svc.get_media(fileId=params.file_id)
                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                return buf.getvalue()

            file_bytes = await run_sync(_download)
            await track_google_usage("google_drive", ctx.node_id, "download", 1, ctx.raw)
            return DriveOutput(
                operation="download",
                file_id=metadata.get('id'),
                name=metadata.get('name'),
                mime_type=metadata.get('mimeType'),
                size=len(file_bytes),
                content_base64=base64.b64encode(file_bytes).decode('utf-8'),
            )

        if op == "list":
            query_parts = []
            if params.folder_id:
                query_parts.append(f"'{params.folder_id}' in parents")
            if params.query:
                query_parts.append(params.query)
            if params.file_types in _FILE_TYPE_QUERY:
                query_parts.append(_FILE_TYPE_QUERY[params.file_types])
            query_parts.append("trashed = false")
            full_query = ' and '.join(query_parts)

            list_kwargs = {
                'pageSize': min(params.max_results, 1000),
                'fields': 'nextPageToken, files(id, name, mimeType, size, webViewLink, webContentLink, createdTime, modifiedTime, parents, owners)',
                'orderBy': params.order_by,
            }
            if full_query:
                list_kwargs['q'] = full_query

            result = await run_sync(lambda: files_svc.list(**list_kwargs).execute())
            raw = result.get('files', [])
            formatted = [{
                "file_id": f.get('id'),
                "name": f.get('name'),
                "mime_type": f.get('mimeType'),
                "size": f.get('size'),
                "web_link": f.get('webViewLink'),
                "download_link": f.get('webContentLink'),
                "created_time": f.get('createdTime'),
                "modified_time": f.get('modifiedTime'),
                "parent_ids": f.get('parents', []),
                "owner": f.get('owners', [{}])[0].get('emailAddress') if f.get('owners') else None,
            } for f in raw]
            await track_google_usage("google_drive", ctx.node_id, "list", len(formatted), ctx.raw)
            return DriveOutput(
                operation="list",
                files=formatted,
                count=len(formatted),
                next_page_token=result.get('nextPageToken'),
            )

        if op == "share":
            if not params.file_id:
                raise RuntimeError("File ID is required")
            if not params.email:
                raise RuntimeError("Email address is required")

            perm = await run_sync(lambda: svc.permissions().create(
                fileId=params.file_id,
                body={'type': 'user', 'role': params.role, 'emailAddress': params.email},
                sendNotificationEmail=params.send_notification,
                emailMessage=params.message or None,
                fields='id, type, role, emailAddress',
            ).execute())
            file_info = await run_sync(lambda: files_svc.get(
                fileId=params.file_id, fields='id, name, webViewLink',
            ).execute())
            await track_google_usage("google_drive", ctx.node_id, "share", 1, ctx.raw)
            return DriveOutput(
                operation="share",
                permission_id=perm.get('id'),
                file_id=params.file_id,
                file_name=file_info.get('name'),
                shared_with=params.email,
                role=params.role,
                web_link=file_info.get('webViewLink'),
            )

        raise RuntimeError(f"Unknown Drive operation: {op}")
