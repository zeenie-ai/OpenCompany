"""Contract for ``fetch_to_workspace``.

The failure modes that matter are the ones the hand-rolled downloaders get
wrong: trusting Content-Length, writing outside the workspace, taking the
filename from the remote URL, and treating an expired provider URL as a
server fault rather than something the caller can retry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from services.media import MEDIA_SUBDIR, fetch_to_workspace, read_media_bytes


pytestmark = pytest.mark.unit


def _ctx(tmp_path: Path, *, node_id: str = "wa-node-1", workflow_id: str = "wf-1"):
    return SimpleNamespace(
        node_id=node_id,
        workflow_id=workflow_id,
        workspace_dir=str(tmp_path),
        raw={"workspace_dir": str(tmp_path)},
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _transport(handler):
    """Route httpx through a stub so no test touches the network."""
    return httpx.MockTransport(handler)


@pytest.fixture
def public_url(monkeypatch):
    """Neutralise the SSRF guard; example.com does not resolve in CI."""
    monkeypatch.setattr("services.net.is_public_url", lambda _url: (True, ""))


@pytest.fixture
def stub_http(monkeypatch):
    """Install a MockTransport into every AsyncClient fetch_to_workspace builds."""

    def _install(handler):
        original = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = _transport(handler)
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

    return _install


class TestFetchToWorkspace:
    def test_writes_body_into_the_workspace(self, tmp_path, public_url, stub_http):
        stub_http(lambda _req: httpx.Response(200, content=b"binary-body", headers={"content-type": "image/png"}))
        ctx = _ctx(tmp_path)

        ref = _run(fetch_to_workspace("https://example.com/x", ctx=ctx, stem="photo", kind="image"))

        assert ref.kind == "image"
        assert ref.path.startswith(f"{MEDIA_SUBDIR}/")
        assert read_media_bytes(ref, ctx=ctx)[1] == b"binary-body"

    def test_extension_comes_from_content_type_not_the_url(self, tmp_path, public_url, stub_http):
        """Provider CDNs serve media from extensionless, query-signed URLs."""
        stub_http(lambda _req: httpx.Response(200, content=b"x", headers={"content-type": "image/jpeg"}))

        ref = _run(
            fetch_to_workspace(
                "https://example.com/whatsapp_business/attachments/?mid=123&ext=1",
                ctx=_ctx(tmp_path),
                stem="inbound",
                kind="image",
            )
        )
        assert ref.filename.endswith(".jpg") or ref.filename.endswith(".jpeg")
        assert ref.mime_type == "image/jpeg"

    def test_falls_back_to_the_url_suffix(self, tmp_path, public_url, stub_http):
        stub_http(lambda _req: httpx.Response(200, content=b"x"))

        ref = _run(fetch_to_workspace("https://example.com/report.pdf", ctx=_ctx(tmp_path), stem="r"))
        assert ref.filename.endswith(".pdf")

    def test_filename_is_ours_not_the_servers(self, tmp_path, public_url, stub_http):
        """A server-controlled filename is how fileDownloader escapes its dir."""
        stub_http(
            lambda _req: httpx.Response(
                200,
                content=b"x",
                headers={"content-disposition": 'attachment; filename="../../evil.sh"'},
            )
        )

        ref = _run(fetch_to_workspace("https://example.com/../../evil.sh", ctx=_ctx(tmp_path), stem="safe"))
        assert ".." not in ref.path
        assert ref.path.startswith(f"{MEDIA_SUBDIR}/")
        assert ref.filename.startswith("safe-")

    def test_cap_is_enforced_on_bytes_actually_read(self, tmp_path, public_url, stub_http):
        """Content-Length is attacker-controlled; the running total is not."""
        from services.plugin import NodeUserError

        big = b"a" * 5000
        stub_http(lambda _req: httpx.Response(200, content=big, headers={"content-length": "1"}))

        with pytest.raises(NodeUserError, match="limit"):
            _run(fetch_to_workspace("https://example.com/x", ctx=_ctx(tmp_path), stem="x", max_bytes=1000))

    def test_nothing_is_written_when_the_cap_trips(self, tmp_path, public_url, stub_http):
        from services.plugin import NodeUserError

        stub_http(lambda _req: httpx.Response(200, content=b"a" * 5000))
        with pytest.raises(NodeUserError):
            _run(fetch_to_workspace("https://example.com/x", ctx=_ctx(tmp_path), stem="x", max_bytes=10))

        assert not (tmp_path / MEDIA_SUBDIR).exists()

    def test_expired_url_is_a_user_error_not_a_crash(self, tmp_path, public_url, stub_http):
        """Provider media URLs expire in minutes; that is retryable, not a bug."""
        from services.plugin import NodeUserError

        stub_http(lambda _req: httpx.Response(404, content=b"gone"))

        with pytest.raises(NodeUserError, match="expired"):
            _run(fetch_to_workspace("https://example.com/x", ctx=_ctx(tmp_path), stem="x"))

    def test_empty_body_is_refused(self, tmp_path, public_url, stub_http):
        from services.plugin import NodeUserError

        stub_http(lambda _req: httpx.Response(200, content=b""))

        with pytest.raises(NodeUserError, match="empty"):
            _run(fetch_to_workspace("https://example.com/x", ctx=_ctx(tmp_path), stem="x"))

    def test_auth_headers_are_forwarded(self, tmp_path, public_url, stub_http):
        """Meta's media URLs are signed AND still require the bearer token."""
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, content=b"x")

        stub_http(handler)
        _run(
            fetch_to_workspace(
                "https://example.com/x",
                ctx=_ctx(tmp_path),
                stem="x",
                headers={"Authorization": "Bearer tok"},
            )
        )
        assert seen["auth"] == "Bearer tok"

    def test_refuses_to_claim_audio(self, tmp_path, public_url, stub_http):
        from services.plugin import NodeUserError

        stub_http(lambda _req: httpx.Response(200, content=b"x"))
        with pytest.raises(NodeUserError, match="write_audio"):
            _run(fetch_to_workspace("https://example.com/x", ctx=_ctx(tmp_path), stem="x", kind="audio"))


class TestSsrfGuard:
    """The guard is shared with monty_executor rather than duplicated."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/admin",
            "http://localhost/admin",
            "file:///etc/passwd",
            "ftp://example.com/x",
            "not-a-url",
            "http://a..b.example/x",  # getaddrinfo's codec raises UnicodeError
            "http://[::1/x",  # urlparse raises ValueError
        ],
    )
    def test_blocked_targets_are_refused(self, tmp_path, url):
        from services.plugin import NodeUserError

        with pytest.raises(NodeUserError, match="Refusing to download"):
            _run(fetch_to_workspace(url, ctx=_ctx(tmp_path), stem="x"))

    def test_refusal_does_not_echo_the_url(self, tmp_path):
        """Signed query strings must not leak into logs or LLM context."""
        from services.plugin import NodeUserError

        secret = "https://127.0.0.1/x?sig=SUPERSECRET"
        with pytest.raises(NodeUserError) as exc:
            _run(fetch_to_workspace(secret, ctx=_ctx(tmp_path), stem="x"))
        assert "SUPERSECRET" not in str(exc.value)

    def test_a_redirect_to_a_blocked_host_is_refused_at_that_hop(self, tmp_path, monkeypatch, stub_http):
        from services.plugin import NodeUserError

        def guard(url):
            blocked = "169.254.169.254" in url
            return (False, "blocked non-public address 169.254.169.254") if blocked else (True, "")

        monkeypatch.setattr("services.net.is_public_url", guard)
        sent = []

        def handler(request):
            sent.append(str(request.url))
            if request.url.host == "files.example":
                location = "http://169.254.169.254/latest/meta-data/?sig=SUPERSECRET"
                return httpx.Response(302, headers={"location": location})
            return httpx.Response(200, content=b"secret")

        stub_http(handler)
        with pytest.raises(NodeUserError, match="Refusing to download") as exc:
            _run(fetch_to_workspace("https://files.example/a.jpg", ctx=_ctx(tmp_path), stem="x"))
        assert sent == ["https://files.example/a.jpg"]
        assert "SUPERSECRET" not in str(exc.value)

    @pytest.mark.parametrize("url", ["http://100.100.100.200/latest/meta-data/",
                                     "http://[::ffff:100.100.100.200]/latest/meta-data/"])
    def test_the_shared_address_space_is_refused(self, tmp_path, stub_http, url):
        # RFC 6598 space, where Alibaba Cloud serves its metadata: none of the
        # ipaddress flags covers it, mapped into IPv6 or not.
        from services.plugin import NodeUserError

        stub_http(lambda _req: httpx.Response(200, content=b"secret"))
        with pytest.raises(NodeUserError, match="Refusing to download"):
            _run(fetch_to_workspace(url, ctx=_ctx(tmp_path), stem="x"))
