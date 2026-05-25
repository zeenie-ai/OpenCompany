"""MachinaOs MCP server (VSCode pattern, no custom IPC).

Hosts a `FastMCP` ASGI sub-app at ``/mcp/ide`` that spawned CLI sessions
auto-discover via the lockfile written by ``lockfile.py``. Each session
gets a per-batch bearer token; the middleware validates it and binds the
matching ``BatchContext`` into a contextvar so tool implementations can
scope to the calling session's workspace_dir / connected_skill_names /
allowed_credentials without explicit plumbing.

Tools (5 in v1, mirroring Claude Code's progressive-disclosure pattern):
  - ``getWorkspaceFiles`` — glob/read inside the session's worktree
  - ``listSkills`` — metadata for skills connected to the parent agent
  - ``getSkill`` — full skill markdown + scripts + references
  - ``getCredential`` — gated by per-batch allowlist
  - ``broadcastLog`` — write to MachinaOs Terminal tab

The server module exposes:
  - :func:`get_mcp_app` — Starlette/ASGI sub-app for ``app.mount(...)``
  - :func:`register_batch` / :func:`unregister_batch` — `AICliService`
    calls these around `run_batch()` to register/expire tokens
  - :class:`BatchContext` — scoping data attached to each token

Tools deferred to v2: ``getDiagnostics``, ``executeCode``.
"""

from __future__ import annotations

import contextvars
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# BatchContext + token registry
# ---------------------------------------------------------------------------


@dataclass
class BatchContext:
    """Scoping data attached to one batch's bearer token.

    Populated at ``AICliService.run_batch()`` entry; deregistered in the
    ``finally`` block. Tools dereference the calling batch via the
    bearer token in `Authorization` header.
    """

    workflow_id: str
    node_id: str
    workspace_dir: Path
    connected_skill_names: Set[str] = field(default_factory=set)
    allowed_credentials: Set[str] = field(default_factory=set)
    # Connected ``input-tools`` nodes (entries from
    # ``services.plugin.edge_walker.collect_agent_connections``). Each
    # dict: ``{node_id, node_type, label, parameters, ...}``. Drives the
    # ``listMachinaOsTools`` / ``callMachinaOsTool`` MCP tools so the
    # CLI agent sees the same tool surface the AI Agent does.
    connected_tools: List[Dict[str, Any]] = field(default_factory=list)
    # Optional broadcaster for `broadcastLog`. Lazily resolved from the
    # global container if None.
    broadcaster: Optional[Any] = None


# Token -> BatchContext registry. Lives in-memory only; tokens never
# touch disk or the credentials.db.
_active_tokens: Dict[str, BatchContext] = {}


def issue_token() -> str:
    """Mint a new bearer token (32 bytes hex)."""
    return secrets.token_hex(32)


def register_batch(token: str, ctx: BatchContext) -> None:
    """Register a batch's auth token + expose its connected workflow
    tools on the FastMCP server. Idempotent on identical context."""
    if token in _active_tokens:
        existing = _active_tokens[token]
        if existing is not ctx:
            raise ValueError("Token collision in MCP server batch registry")
        return
    _active_tokens[token] = ctx
    tool_names = [t.get("node_type") for t in ctx.connected_tools]
    logger.info(
        "[CC-Agent MCP register_batch] node=%s wf=%s token=%s... " "skills=%d tools=%d creds=%d %s",
        ctx.node_id,
        ctx.workflow_id,
        token[:8],
        len(ctx.connected_skill_names),
        len(ctx.connected_tools),
        len(ctx.allowed_credentials),
        f"tools={tool_names}" if tool_names else "(no tools wired)",
    )
    # Per-batch workflow-tool exposure lives in workflow_tools.py.
    from services.cli_agent.workflow_tools import expose_workflow_tools

    expose_workflow_tools(ctx.connected_tools)


def unregister_batch(token: str) -> None:
    """Drop a batch's token + un-expose its tools when the refcount
    hits zero. Safe to call twice."""
    ctx = _active_tokens.pop(token, None)
    if ctx is None:
        return
    logger.debug("[CC-Agent MCP] unregistered batch token=%s...", token[:8])
    from services.cli_agent.workflow_tools import unexpose_workflow_tools

    unexpose_workflow_tools(ctx.connected_tools)


def lookup_batch(token: str) -> Optional[BatchContext]:
    return _active_tokens.get(token)


def rebind_batch(
    token: str,
    *,
    connected_tools: Optional[List[Dict[str, Any]]] = None,
    connected_skill_names: Optional[Set[str]] = None,
    allowed_credentials: Optional[Set[str]] = None,
    workspace_dir: Optional[Path] = None,
) -> Optional[BatchContext]:
    """In-place rebind of an existing batch's surface.

    Used by :class:`ClaudeSessionPool` on warm-subprocess reuse:
    claude bakes the bearer token into its argv at spawn time (via
    ``--mcp-config``), so a warm subprocess keeps hitting the SAME
    token across batches. If the operator disconnects a tool between
    batches, the bearer-token's :class:`BatchContext` must be updated
    in place — otherwise the per-handler scope check in
    ``workflow_tools._build_handler`` still sees the stale
    ``connected_tools`` and lets disconnected tools fire.

    For ``connected_tools``: refcount diff is applied to FastMCP — any
    node_type that's in the OLD list but not the new gets one
    :func:`unexpose_workflow_tools` decrement (may remove the tool
    from FastMCP when its refcount hits zero); any node_type that's
    new gets one :func:`expose_workflow_tools` increment. Tools that
    survive in both lists are left alone but their ``parameters`` /
    ``label`` fields are refreshed via the in-place list assignment.

    Returns the rebound context, or ``None`` if the token is unknown.
    """
    ctx = _active_tokens.get(token)
    if ctx is None:
        return None
    if connected_tools is not None:
        from services.cli_agent.workflow_tools import (
            expose_workflow_tools,
            unexpose_workflow_tools,
        )

        old_by_type = {t.get("node_type"): t for t in ctx.connected_tools if t.get("node_type")}
        new_by_type = {t.get("node_type"): t for t in connected_tools if t.get("node_type")}
        added = [v for k, v in new_by_type.items() if k not in old_by_type]
        removed = [v for k, v in old_by_type.items() if k not in new_by_type]
        if removed:
            unexpose_workflow_tools(removed)
        if added:
            expose_workflow_tools(added)
        ctx.connected_tools = list(connected_tools)
        logger.info(
            "[CC-Agent MCP rebind_batch] node=%s wf=%s token=%s... " "+%d -%d kept=%d (now %d tools)",
            ctx.node_id,
            ctx.workflow_id,
            token[:8],
            len(added),
            len(removed),
            len(old_by_type.keys() & new_by_type.keys()),
            len(ctx.connected_tools),
        )
    if connected_skill_names is not None:
        ctx.connected_skill_names = set(connected_skill_names)
    if allowed_credentials is not None:
        ctx.allowed_credentials = set(allowed_credentials)
    if workspace_dir is not None:
        ctx.workspace_dir = Path(workspace_dir).resolve()
    return ctx


def active_batch_count() -> int:
    return len(_active_tokens)


# ---------------------------------------------------------------------------
# ContextVar — thread/task-local handle to the current batch
# ---------------------------------------------------------------------------

_current_batch: contextvars.ContextVar[Optional[BatchContext]] = contextvars.ContextVar("machina_current_batch", default=None)


def _require_batch() -> BatchContext:
    ctx = _current_batch.get()
    if ctx is None:
        raise RuntimeError("MCP tool called without an active batch context. " "This indicates the auth middleware was bypassed.")
    return ctx


# Per-batch workflow-tool exposure happens in `_expose_workflow_tools`
# (above). Each connected node lands as its own
# `mcp__machinaos__<node_type>` entry; FastMCP infers the inputSchema
# from the typed `params` annotation. The legacy generic wrapper has
# been removed.


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate `Authorization: Bearer <token>` against the registry.

    On success: bind the matching `BatchContext` into the contextvar.
    On failure: 401.

    The MCP spec uses MCP-Protocol-Version + Authorization headers; we
    care only about the Bearer here. Health checks under `/healthz`
    bypass auth (they're for dev sanity).
    """

    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        method = request.method
        if path.endswith("/healthz"):
            return await call_next(request)

        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        token: Optional[str] = None
        if auth and auth.lower().startswith("bearer "):
            token = auth[7:].strip() or None

        if not token:
            ua = request.headers.get("user-agent", "")
            logger.warning(
                "[CC-Agent MCP auth] %s %s -> 401 (no Bearer token; "
                "auth_header=%r ua=%r) — claude CLI either didn't read the "
                "lockfile or is hitting the wrong URL.",
                method,
                path,
                auth,
                ua,
            )
            return JSONResponse(
                {"error": "missing or malformed Authorization header"},
                status_code=401,
            )

        ctx = lookup_batch(token)
        if ctx is None:
            logger.warning(
                "[CC-Agent MCP auth] %s %s -> 401 (token=%s... not in " "active registry — batch may have ended)",
                method,
                path,
                token[:8],
            )
            return JSONResponse(
                {"error": "invalid or expired token"},
                status_code=401,
            )

        logger.info(
            "[CC-Agent MCP auth] %s %s -> OK (node=%s wf=%s token=%s...)",
            method,
            path,
            ctx.node_id,
            ctx.workflow_id,
            token[:8],
        )

        reset_token = _current_batch.set(ctx)
        try:
            return await call_next(request)
        finally:
            _current_batch.reset(reset_token)


# ---------------------------------------------------------------------------
# Tool registration helper — defers FastMCP import so module import is cheap
# ---------------------------------------------------------------------------


def _build_tools(mcp: Any) -> None:  # FastMCP type
    """Register the 5 v1 tools on a `FastMCP` instance."""

    @mcp.tool(
        name="getWorkspaceFiles",
        description=(
            "List or read files inside the calling session's per-task git "
            "worktree. Use `read=False` for metadata-only listings; "
            "`read=True` to fetch file contents (capped at 1MB per file)."
        ),
    )
    def get_workspace_files(
        path: str = ".",
        pattern: str = "*",
        read: bool = False,
        max_bytes: int = 1_000_000,
    ) -> Dict[str, Any]:
        ctx = _require_batch()
        try:
            base = ctx.workspace_dir.resolve()
            target = (base / path).resolve()
            # Path-traversal guard
            try:
                target.relative_to(base)
            except ValueError:
                return {
                    "error": "path escapes workspace_dir",
                    "path": str(path),
                }

            if not target.exists():
                return {"files": [], "path": str(path)}

            entries: List[Dict[str, Any]] = []
            if target.is_file():
                files_iter = [target]
            else:
                files_iter = sorted(target.rglob(pattern))

            for p in files_iter:
                if not p.is_file():
                    continue
                try:
                    rel = str(p.relative_to(base))
                    info: Dict[str, Any] = {
                        "path": rel,
                        "size": p.stat().st_size,
                        "mtime": p.stat().st_mtime,
                    }
                    if read and p.stat().st_size <= max_bytes:
                        try:
                            info["content"] = p.read_text(encoding="utf-8", errors="replace")
                        except OSError as exc:
                            info["read_error"] = str(exc)
                    entries.append(info)
                except OSError:
                    continue

                if len(entries) >= 1000:
                    break

            return {"files": entries, "path": str(path)}
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("[CC-Agent MCP] getWorkspaceFiles failed")
            return {"error": str(exc), "path": str(path)}

    @mcp.tool(
        name="listSkills",
        description=(
            "List skills connected to the parent agent node. Returns "
            "metadata only (~100 tokens per skill). Call `getSkill(name)` "
            "to fetch the full instructions."
        ),
    )
    def list_skills() -> Dict[str, Any]:
        ctx = _require_batch()
        try:
            from services.skill_loader import get_skill_loader

            loader = get_skill_loader()
            registry = loader.scan_skills()
            results = []
            for name in sorted(ctx.connected_skill_names):
                meta = registry.get(name)
                if meta is None:
                    continue
                results.append(
                    {
                        "name": meta.name,
                        "description": meta.description,
                        "allowed_tools": list(meta.allowed_tools),
                        "category": (meta.metadata.get("category") if isinstance(meta.metadata, dict) else None),
                    }
                )
            return {"skills": results}
        except Exception as exc:  # pragma: no cover
            logger.exception("[CC-Agent MCP] listSkills failed")
            return {"error": str(exc), "skills": []}

    @mcp.tool(
        name="getSkill",
        description=(
            "Fetch full content for one skill: instructions (markdown), "
            "scripts (executable code samples), and references (extra "
            "docs). The skill must be connected to the parent agent node."
        ),
    )
    def get_skill(name: str) -> Dict[str, Any]:
        ctx = _require_batch()
        if name not in ctx.connected_skill_names:
            return {
                "error": f"skill {name!r} is not connected to this agent node",
                "name": name,
            }
        try:
            from services.skill_loader import get_skill_loader

            loader = get_skill_loader()
            skill = loader.load_skill(name)
            if skill is None:
                return {"error": f"skill {name!r} not found", "name": name}
            return {
                "name": skill.metadata.name,
                "description": skill.metadata.description,
                "instructions": skill.instructions,
                "allowed_tools": list(skill.metadata.allowed_tools),
                "metadata": dict(skill.metadata.metadata) if skill.metadata.metadata else {},
                "scripts": dict(skill.scripts),
                "references": dict(skill.references),
                # `assets` (binary) excluded by default — too big for MCP responses.
            }
        except Exception as exc:  # pragma: no cover
            logger.exception("[CC-Agent MCP] getSkill failed for %r", name)
            return {"error": str(exc), "name": name}

    @mcp.tool(
        name="getCredential",
        description=(
            "Fetch a credential by provider name. Only credentials in the "
            "batch's allowlist are returned; everything else returns 403. "
            "Use sparingly — prefer the CLI's own auth where possible."
        ),
    )
    async def get_credential(name: str) -> Dict[str, Any]:
        ctx = _require_batch()
        if name not in ctx.allowed_credentials:
            return {
                "error": f"credential {name!r} not in allowlist for this batch",
                "name": name,
                "status": 403,
            }
        try:
            from core.container import container

            auth = container.auth_service()
            value = await auth.get_api_key(name)
            if not value:
                return {
                    "error": f"credential {name!r} not configured",
                    "name": name,
                    "status": 404,
                }
            return {"name": name, "value": value}
        except Exception as exc:  # pragma: no cover
            logger.exception("[CC-Agent MCP] getCredential failed for %r", name)
            return {"error": str(exc), "name": name, "status": 500}

    # Per-batch workflow tools are exposed dynamically on
    # `register_batch` via `_expose_workflow_tools` — each connected
    # node lands as its own `mcp__machinaos__<node_type>` entry. No
    # generic `listMachinaOsTools` / `callMachinaOsTool` wrapper.

    @mcp.tool(
        name="broadcastLog",
        description=(
            "Write a log line to the MachinaOs Terminal tab. Use to "
            "surface intermediate progress that would otherwise be lost "
            "between CLI sessions."
        ),
    )
    async def broadcast_log(
        message: str,
        level: str = "info",
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        ctx = _require_batch()
        if level not in ("debug", "info", "warning", "error"):
            level = "info"
        try:
            broadcaster = ctx.broadcaster
            if broadcaster is None:
                from services.status_broadcaster import get_status_broadcaster

                broadcaster = get_status_broadcaster()
            payload = {
                "source": source or f"mcp:{ctx.node_id}",
                "level": level,
                "message": message[:5000],
            }
            await broadcaster.broadcast_terminal_log(payload)
            return {"success": True}
        except Exception as exc:  # pragma: no cover
            logger.exception("[CC-Agent MCP] broadcastLog failed")
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# ASGI sub-app factory (mounted in main.py lifespan)
# ---------------------------------------------------------------------------

_app_singleton: Optional[Any] = None  # Starlette app
_mcp_singleton: Optional[Any] = None  # FastMCP instance — used by per-batch
# dynamic tool (un)registration.


def get_mcp_app() -> Any:
    """Return the Starlette/ASGI app to mount under `/mcp/ide`.

    Idempotent — multiple calls return the same instance, so the FastAPI
    lifespan can wire it without worrying about duplicate registration.
    """
    global _app_singleton, _mcp_singleton
    if _app_singleton is not None:
        return _app_singleton

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name="machinaos-cli-agent",
        instructions=(
            "MachinaOs IDE MCP server. Exposes workspace files, connected "
            "skills, scoped credentials, and a Terminal-tab log channel "
            "to the calling CLI session."
        ),
        # FastMCP's HTTP path defaults are fine; we mount the whole app
        # under /mcp/ide externally.
    )
    _build_tools(mcp)
    _mcp_singleton = mcp

    asgi_app = mcp.streamable_http_app()
    asgi_app.add_middleware(_BearerAuthMiddleware)

    _app_singleton = asgi_app
    return _app_singleton


# ---------------------------------------------------------------------------
# Test/diagnostic helpers (used by tests + verification step #11/#12)
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:  # pragma: no cover
    """Wipe the token registry + per-batch workflow-tool refcounts.
    ONLY use in tests."""
    global _app_singleton, _mcp_singleton
    _active_tokens.clear()
    _app_singleton = None
    _mcp_singleton = None
    from services.cli_agent.workflow_tools import _reset_for_tests as _wt_reset

    _wt_reset()
