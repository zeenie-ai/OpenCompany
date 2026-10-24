"""Where a WhatsApp send reads its media from (nodes/whatsapp/_service.py).

whatsappSend is an AI tool, so the model can choose a media path or URL. A
file is read only from the workflow's workspace, and a URL is downloaded only
after services/netpolicy.py accepts it.
"""

from __future__ import annotations

import base64
import ipaddress
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nodes.whatsapp import _service
from services import netpolicy


@pytest.fixture
def rpc():
    client = MagicMock(name="WhatsAppRpc")
    client.call = AsyncMock(return_value={"message_id": "m1"})
    with patch.object(_service, "get_client", AsyncMock(return_value=client)):
        yield client


def _media(message_type: str = "document", **extra) -> dict:
    return {"recipient_type": "phone", "phone": "15550001111", "message_type": message_type, **extra}


def _ctx(workspace) -> SimpleNamespace:
    return SimpleNamespace(workspace_dir=str(workspace), node_id="send")


async def test_reads_a_file_from_the_workspace(tmp_path, rpc):
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 fixture")
    result = await _service.handle_whatsapp_send(_media(media_source="file", file_path="report.pdf"), ctx=_ctx(tmp_path))
    assert result["success"] is True
    media = rpc.call.await_args.args[1]["media_data"]
    assert base64.b64decode(media["data"]) == b"%PDF-1.4 fixture"
    assert media["filename"] == "report.pdf"


@pytest.mark.parametrize("relative", [True, False])
async def test_refuses_a_file_outside_the_workspace(tmp_path, rpc, relative):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "credentials.db"
    secret.write_bytes(b"secret")
    path = "../credentials.db" if relative else str(secret)
    result = await _service.handle_whatsapp_send(_media(media_source="file", file_path=path), ctx=_ctx(workspace))
    assert result["success"] is False
    assert "outside this workflow's workspace" in result["error"]
    rpc.call.assert_not_awaited()


async def test_refuses_a_path_when_the_caller_has_no_workspace(tmp_path, rpc):
    secret = tmp_path / "credentials.db"
    secret.write_bytes(b"secret")
    result = await _service.handle_whatsapp_send(_media(media_source="file", file_path=str(secret)))
    assert result["success"] is False
    rpc.call.assert_not_awaited()


async def test_an_uploaded_file_still_sends(rpc):
    upload = {"type": "upload", "data": base64.b64encode(b"png").decode(), "mimeType": "image/png", "filename": "a.png"}
    result = await _service.handle_whatsapp_send(_media("image", media_source="file", file_path=upload))
    assert result["success"] is True
    assert rpc.call.await_args.args[1]["media_data"]["mime_type"] == "image/png"


async def test_refuses_a_metadata_url_without_downloading(rpc):
    with patch("httpx.AsyncClient") as http:
        result = await _service.handle_whatsapp_send(
            _media(media_source="url", media_url="http://169.254.169.254/latest/meta-data/"))
    assert result["success"] is False
    assert "metadata" in result["error"]
    http.assert_not_called()
    rpc.call.assert_not_awaited()


async def test_downloads_a_public_url(monkeypatch, rpc):
    async def resolve(host, port):
        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(netpolicy, "resolve_addresses", resolve)
    http = MagicMock(name="AsyncClient")
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.get = AsyncMock(return_value=MagicMock(content=b"jpeg"))
    with patch("httpx.AsyncClient", return_value=http):
        result = await _service.handle_whatsapp_send(
            _media("image", media_source="url", media_url="https://cdn.example.com/a.jpg"))
    assert result["success"] is True
    assert base64.b64decode(rpc.call.await_args.args[1]["media_data"]["data"]) == b"jpeg"
