"""Download a remote file straight into a workflow workspace.

Separate module rather than part of :mod:`services.media.workspace` because
that one is synchronous by contract (see its ``workspace_root`` docstring --
it must never do the database read the id->slug translation would need) and
this is unavoidably async.

The alternative every caller reaches for otherwise is ``httpx.get`` followed
by ``open(path, "wb").write(...)``, which is what ``fileDownloader`` does:
no containment, a filename taken from the remote URL, a non-atomic write and
no size cap. This exists so that is never the easy option.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import PurePosixPath
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from core.logging import get_logger
from services.media.limits import MEDIA_MAX_READ_BYTES
from services.media.refs import FileKind, FileRef
from services.media.workspace import MEDIA_SUBDIR, write_media

logger = get_logger(__name__)

# Bounded so a hostile Content-Length cannot make us allocate; the running
# total is what enforces the cap, mirroring the upload route.
_CHUNK_BYTES = 64 * 1024

_DEFAULT_TIMEOUT_SECONDS = 30.0


def _extension_for(url: str, content_type: Optional[str]) -> str:
    """Best available extension, preferring what the server actually served.

    The URL path is the weaker signal: providers routinely serve media from
    extensionless, query-signed URLs (Meta's lookaside CDN is one), so the
    Content-Type is tried first and the path is the fallback.
    """
    if content_type:
        # Strip parameters: "audio/ogg; codecs=opus" -> "audio/ogg".
        base = content_type.split(";")[0].strip()
        guessed = mimetypes.guess_extension(base)
        if guessed:
            return guessed.lstrip(".")

    suffix = PurePosixPath(unquote(urlparse(url).path)).suffix
    if suffix:
        return suffix.lstrip(".")
    return "bin"


async def fetch_to_workspace(
    url: str,
    *,
    ctx: Any,
    stem: str,
    kind: FileKind = "file",
    headers: Optional[Dict[str, str]] = None,
    max_bytes: int = MEDIA_MAX_READ_BYTES,
    subdir: str = MEDIA_SUBDIR,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ext: Optional[str] = None,
) -> FileRef:
    """Stream ``url`` into the workspace and return a reference to it.

    ``headers`` carries provider authentication -- several media APIs sign
    their download URLs *and* still require the bearer token, and hand out
    URLs that expire in minutes, so resolve immediately before calling this
    rather than storing one.

    Raises :class:`NodeUserError` for every failure a caller can act on: a
    blocked host, a non-2xx response, an oversize body, an empty body. Those
    are user- or provider-correctable, so they log one warning rather than a
    traceback.
    """
    import httpx

    from services.net import is_public_url
    from services.plugin import NodeUserError

    ok, reason = is_public_url(url)
    if not ok:
        # Deliberately not echoing the URL: it may carry a signed query
        # string, and this message reaches logs and the LLM context.
        raise NodeUserError(f"Refusing to download from that URL: {reason}.")

    if kind == "audio":
        # Same rule as write_media: that kind asserts a real probe.
        raise NodeUserError(
            "fetch_to_workspace cannot produce kind='audio': that claims the "
            "container was probed. Download as 'file', then write with write_audio."
        )

    async def check(request: httpx.Request) -> None:
        # httpx runs request hooks before every request, so each redirect hop
        # is checked too, by the name httpx connects to.
        ok, reason = await asyncio.to_thread(is_public_url, str(request.url))
        if not ok:
            raise NodeUserError(f"Refusing to download from that URL: {reason}.")

    chunks: list[bytes] = []
    total = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, event_hooks={"request": [check]}) as client:
            async with client.stream("GET", url, headers=headers or {}) as response:
                if response.status_code >= 400:
                    # Read nothing further; the body of an error page is not
                    # something we want in the workspace.
                    raise NodeUserError(
                        f"Download failed with HTTP {response.status_code}. "
                        "If the URL was issued by a provider it may have expired -- "
                        "re-resolve it and retry."
                    )
                content_type = response.headers.get("content-type")
                async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                    total += len(chunk)
                    if total > max_bytes:
                        raise NodeUserError(
                            f"Download exceeds the {max_bytes // (1024 * 1024)} MB limit."
                        )
                    chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise NodeUserError(f"Download timed out after {timeout:g}s.") from exc
    except httpx.HTTPError as exc:
        raise NodeUserError(f"Download failed: {exc}") from exc

    if not total:
        raise NodeUserError("Download returned an empty file.")

    ref = write_media(
        b"".join(chunks),
        ctx=ctx,
        stem=stem,
        ext=ext or _extension_for(url, content_type),
        kind=kind,
        mime_type=(content_type or "").split(";")[0].strip() or None,
        subdir=subdir,
    )

    logger.info(
        "fetched media into workspace",
        path=ref.path,
        size_bytes=ref.size_bytes,
        kind=ref.kind,
    )
    return ref


__all__ = ["fetch_to_workspace"]
