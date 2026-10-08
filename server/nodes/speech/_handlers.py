"""Dictation for the chat's message box.

``dictation_status`` says whether a recording can be turned into text: the
first provider in ``speech_defaults.json``'s ``dictation.providers`` with a
stored key (the microphone shows only when there is one; the browser's own
speech recognition is not used, it does not work in the desktop app).
``transcribe_audio`` transcribes a recording the chat uploaded into the
workflow's workspace (``uploads/``) with that provider, records the usage,
and deletes the recording: a dictation leaves nothing behind.

Both answer only the workflow's owner, like the chat itself
(``services/chat/access.py``).
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.plugin import NodeUserError
from services.plugin.ws import ws_response

from . import _config as speech_config
from . import _unifier
from ._base import track_usage
from ._protocol import SttRequest
from ._registry import stt_providers

logger = get_logger(__name__)


def dictation_providers() -> list[str]:
    """The providers dictation may use, in the order it tries them."""
    configured = (speech_config.SPEECH_DEFAULTS.get("dictation") or {}).get("providers") or []
    available = set(stt_providers())
    return [str(provider) for provider in configured if str(provider) in available]


async def dictation_provider(*, principal: str | None = None) -> Optional[str]:
    """The first dictation provider with a stored key, or None."""
    from core.container import container

    auth = container.auth_service()
    for provider in dictation_providers():
        try:
            if await auth.has_valid_key(speech_config.credential_id(provider), principal=principal):
                return provider
        except Exception:  # noqa: BLE001 - an unreadable key is no key
            logger.warning("Dictation key could not be read", provider=provider, exc_info=True)
    return None


async def _owner_session(data: Dict[str, Any], websocket: WebSocket) -> str:
    from core.container import container
    from services.chat.access import ChatAccessDenied, authorize_session, session_id_of

    session_id = session_id_of(data)
    try:
        scope = await authorize_session(container.database(), websocket, session_id)
    except ChatAccessDenied as exc:
        raise NodeUserError("access_denied") from exc
    if scope.workflow_id is None:
        raise NodeUserError("Dictation needs a workflow's chat.")
    return scope.workflow_id


@ws_response
async def handle_dictation_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """``{available, provider}``: whether a recording can be turned into
    text now."""
    await _owner_session(data, websocket)
    from services.authz.ws_surface import execution_principal
    provider = await dictation_provider(principal=execution_principal({}, websocket))
    return {"success": True, "available": provider is not None, "provider": provider}


@ws_response
async def handle_transcribe_audio(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Transcribe the recording at ``path`` (under ``uploads/``) and delete
    it. Answers ``{text, language, provider}``; ``speech_unavailable`` when no
    dictation provider has a key."""
    from core.container import container
    from services.media.workspace import UPLOAD_SUBDIR, resolve_media
    from services.workspace_locator import resolve_workspace_root

    workflow_id = await _owner_session(data, websocket)
    from services.authz.ws_surface import execution_principal
    principal = execution_principal({}, websocket)
    provider = await dictation_provider(principal=principal)
    if provider is None:
        return {"success": False, "error": "speech_unavailable"}
    rel = str(data.get("path") or "").replace("\\", "/")
    parts = PurePosixPath(rel).parts
    if len(parts) < 2 or parts[0] != UPLOAD_SUBDIR:
        raise NodeUserError("Only a recording uploaded to this chat can be transcribed.")
    database = container.database()
    root = await resolve_workspace_root(workflow_id, database, allow_default=False)
    path = resolve_media(rel, workspace_dir=str(root))
    if not path.is_file():
        raise NodeUserError("The recording is no longer there. Record it again.")
    max_bytes = speech_config.capability(provider, "stt", "max_upload_bytes", default=None)
    size = path.stat().st_size
    if isinstance(max_bytes, int) and size > max_bytes:
        raise NodeUserError("The recording is too long to transcribe. Record a shorter one.")
    try:
        api_key = await container.auth_service().resolve_api_key(speech_config.credential_id(provider), principal=principal)
        result = await _unifier.transcribe(
            provider=provider,
            api_key=str(api_key or ""),
            request=SttRequest(
                audio=path.read_bytes(),
                filename=path.name,
                mime_type=mimetypes.guess_type(path.name)[0] or "audio/webm",
                model=speech_config.default_model(provider, "stt"),
                language=str(data.get("language") or ""),
            ),
        )
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Dictation recording could not be deleted", path=rel, exc_info=True)
    usage = SimpleNamespace(raw={"session_id": workflow_id}, node_id="dictation", workflow_id=workflow_id)
    units = result.billed_units if result.billed_units is not None else result.duration_seconds
    await track_usage(usage, provider=provider, operation="speech_to_text", units=units, unit=result.billed_unit or "seconds")
    return {"success": True, "text": result.text, "language": result.language, "provider": provider}


WS_HANDLERS = {
    "dictation_status": handle_dictation_status,
    "transcribe_audio": handle_transcribe_audio,
}

__all__ = ["WS_HANDLERS", "dictation_provider", "dictation_providers", "handle_dictation_status", "handle_transcribe_audio"]
