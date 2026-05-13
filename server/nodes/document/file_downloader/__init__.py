"""File Downloader — Wave 11.D.7 inlined."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


class FileDownloaderParams(BaseModel):
    urls: List[str] = Field(default_factory=list)
    output_dir: str = Field(default="downloads")
    max_workers: int = Field(default=4, ge=1, le=32)
    skip_existing: bool = Field(default=True)
    timeout: int = Field(default=60, ge=1, le=600)

    model_config = ConfigDict(extra="allow")


class FileDownloaderOutput(BaseModel):
    downloaded: Optional[int] = None
    skipped: Optional[int] = None
    failed: Optional[int] = None
    files: Optional[list] = None
    output_dir: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class FileDownloaderNode(ActionNode):
    type = "fileDownloader"
    display_name = "File Downloader"
    subtitle = "Parallel DL"
    group = ("document",)
    description = "Download files from URLs in parallel"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.REST_API

    Params = FileDownloaderParams
    Output = FileDownloaderOutput

    @Operation("download")
    async def download(self, ctx: NodeContext, params: FileDownloaderParams) -> FileDownloaderOutput:
        p = params.model_dump()
        items = p.get('items') or [{'url': u} for u in p.get('urls', [])]
        workspace_dir = ctx.raw.get('workspace_dir', '')
        default_dir = str(Path(workspace_dir) / 'downloads') if workspace_dir else p.get('outputDir', 'downloads')
        output_dir = Path(p.get('outputDir') or default_dir)
        max_workers = int(p.get('maxWorkers', 8))
        skip_existing = p.get('skipExisting', True)
        timeout = float(p.get('timeout', 60))

        if not items:
            return FileDownloaderOutput(
                downloaded=0, skipped=0, failed=0, files=[], output_dir=str(output_dir),
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(max_workers)

        async def dl(item):
            async with semaphore:
                url = item.get('url', '') if isinstance(item, dict) else str(item)
                if not url:
                    return {'status': 'failed', 'error': 'Empty URL'}
                filename = unquote(Path(urlparse(url).path).name or 'download')
                file_path = output_dir / filename
                if skip_existing and file_path.exists():
                    return {'status': 'skipped', 'path': str(file_path), 'url': url}
                try:
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        file_path.write_bytes(resp.content)
                        return {
                            'status': 'downloaded', 'path': str(file_path),
                            'url': url, 'size': len(resp.content), 'filename': filename,
                        }
                except Exception as e:
                    return {'status': 'failed', 'url': url, 'error': str(e)}

        results = await asyncio.gather(*[dl(i) for i in items], return_exceptions=True)
        downloaded, skipped, failed = [], [], []
        for r in results:
            if isinstance(r, Exception):
                failed.append({'error': str(r)})
            elif r.get('status') == 'downloaded':
                downloaded.append(r)
            elif r.get('status') == 'skipped':
                skipped.append(r)
            else:
                failed.append(r)

        return FileDownloaderOutput(
            downloaded=len(downloaded), skipped=len(skipped), failed=len(failed),
            files=downloaded, output_dir=str(output_dir),
        )
