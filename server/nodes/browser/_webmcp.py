"""WebMCP: the tools a web page offers to agents.

A page registers tools with ``document.modelContext.registerTool(...)``
(on by default in Chrome 150+, secure contexts only). Chrome reports them
over its experimental CDP ``WebMCP`` domain, per page session:

- ``WebMCP.enable`` replays ``toolsAdded`` for tools already registered;
- ``toolsAdded`` / ``toolsRemoved`` carry ``{name, description, inputSchema,
  annotations{readOnly, untrustedContent, consequential}, frameId}``;
- ``invokeTool{frameId, toolName, input}`` answers ``{invocationId}`` and the
  result arrives later as ``toolResponded{invocationId, status, output,
  errorText}``.

Chrome sends no ``toolsRemoved`` when a page navigates away, so a frame's
tools are dropped here on ``Page.frameNavigated``. Chrome does not validate
the input against ``inputSchema`` either. The output is the page's words, so
it is returned marked untrusted and capped.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError

from ._cdp import CDPError, CDPSession

logger = get_logger(__name__)

OUTPUT_LIMIT_BYTES = 32 * 1024
INVOKE_TIMEOUT = 60.0


def _cap(value: Any) -> tuple[Any, bool]:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if len(text.encode("utf-8")) <= OUTPUT_LIMIT_BYTES:
        return value, False
    clipped = text.encode("utf-8")[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="ignore")
    return clipped + " ... [truncated]", True


class WebMcpTracker:
    """Tools per page target, kept current from CDP events."""

    def __init__(self) -> None:
        #: target_id -> frame_id -> tool name -> tool
        self._tools: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
        self._main_frames: Dict[str, str] = {}
        self._sessions: Dict[str, CDPSession] = {}
        self._pending: Dict[str, asyncio.Future] = {}
        self._early: Dict[str, Dict[str, Any]] = {}

    # -- wiring ---------------------------------------------------------------

    async def attach(self, target_id: str, session: CDPSession) -> None:
        """Start tracking a page (called on every auto-attached page session)."""
        self._sessions[target_id] = session
        session.on("WebMCP.toolsAdded", lambda p: self._added(target_id, p))
        session.on("WebMCP.toolsRemoved", lambda p: self._removed(target_id, p))
        session.on("WebMCP.toolResponded", self._responded)
        session.on("Page.frameNavigated", lambda p: self._navigated(target_id, p))
        try:
            await session.send("WebMCP.enable", timeout=10)
        except CDPError as exc:
            if not exc.method_not_found:
                logger.debug("[browser] WebMCP.enable failed on %s: %s", target_id, exc)

    def detach(self, target_id: str) -> None:
        self._sessions.pop(target_id, None)
        self._tools.pop(target_id, None)
        self._main_frames.pop(target_id, None)

    def _added(self, target_id: str, params: Dict[str, Any]) -> None:
        frames = self._tools.setdefault(target_id, {})
        for tool in params.get("tools") or []:
            name, frame = tool.get("name"), tool.get("frameId")
            if name and frame:
                frames.setdefault(frame, {})[name] = tool

    def _removed(self, target_id: str, params: Dict[str, Any]) -> None:
        frames = self._tools.get(target_id, {})
        for tool in params.get("tools") or []:
            frames.get(tool.get("frameId"), {}).pop(tool.get("name"), None)

    def _navigated(self, target_id: str, params: Dict[str, Any]) -> None:
        frame = params.get("frame") or {}
        frame_id = frame.get("id")
        if not frame_id:
            return
        if not frame.get("parentId"):
            # The page navigated: every frame's registrations are gone.
            self._main_frames[target_id] = frame_id
            self._tools[target_id] = {}
        else:
            self._tools.get(target_id, {}).pop(frame_id, None)

    def _responded(self, params: Dict[str, Any]) -> None:
        invocation = params.get("invocationId") or ""
        future = self._pending.get(invocation)
        if future is not None:
            if not future.done():
                future.set_result(params)
            return
        # The reply to invokeTool arrives first, but the event can be read
        # before invoke() gets to register for it; keep it briefly.
        self._early[invocation] = params
        while len(self._early) > 64:
            self._early.pop(next(iter(self._early)))

    # -- queries -------------------------------------------------------------

    def tools(self, target_id: Optional[str]) -> List[Dict[str, Any]]:
        """The page's tools, shaped for the agent (no stack traces or node ids)."""
        if not target_id:
            return []
        out = []
        main = self._main_frames.get(target_id)
        for frame_id, tools in self._tools.get(target_id, {}).items():
            for tool in tools.values():
                annotations = tool.get("annotations") or {}
                out.append(
                    {
                        "name": tool.get("name"),
                        "description": tool.get("description") or "",
                        "input_schema": tool.get("inputSchema") or {"type": "object"},
                        "read_only": bool(annotations.get("readOnly")),
                        "consequential": bool(annotations.get("consequential")),
                        "untrusted_output": bool(annotations.get("untrustedContent")),
                        "frame_id": frame_id,
                        "main_frame": frame_id == main,
                    }
                )
        return sorted(out, key=lambda t: (not t["main_frame"], t["name"]))

    def count(self, target_id: Optional[str]) -> int:
        return len(self.tools(target_id))

    async def invoke(
        self,
        target_id: Optional[str],
        name: str,
        tool_input: Dict[str, Any],
        *,
        frame_id: Optional[str] = None,
        mode: str = "read_only",
        timeout: float = INVOKE_TIMEOUT,
    ) -> Dict[str, Any]:
        if mode == "disabled":
            raise NodeUserError("WebMCP tools are turned off for this Browser node.")
        session = self._sessions.get(target_id or "")
        if session is None:
            raise NodeUserError("No page is open; navigate first.")
        matches = [t for t in self.tools(target_id) if t["name"] == name and (frame_id is None or t["frame_id"] == frame_id)]
        if not matches:
            raise NodeUserError(f"This page has no WebMCP tool named {name!r}. Call webmcp_list to see what it offers.")
        if len(matches) > 1:
            raise NodeUserError(f"Several frames offer {name!r}; pass frame_id (see webmcp_list).")
        tool = matches[0]
        if mode == "read_only" and not tool["read_only"]:
            raise NodeUserError(
                f"{name!r} can change things on the site, and this Browser node only allows read-only WebMCP tools. "
                "Do the step through the page instead, or ask the owner (request_user)."
            )
        if not isinstance(tool_input, dict):
            raise NodeUserError("WebMCP tool input must be a JSON object.")

        result = await session.send(
            "WebMCP.invokeTool", {"frameId": tool["frame_id"], "toolName": name, "input": tool_input}, timeout=15
        )
        invocation = result.get("invocationId") or ""
        future = asyncio.get_running_loop().create_future()
        early = self._early.pop(invocation, None)
        if early is not None:
            future.set_result(early)
        self._pending[invocation] = future
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            try:
                await session.send("WebMCP.cancelInvocation", {"invocationId": invocation}, timeout=5)
            except Exception:  # noqa: BLE001
                pass
            raise NodeUserError(f"The page's {name!r} tool did not answer within {int(timeout)} s.") from None
        finally:
            self._pending.pop(invocation, None)

        status = response.get("status")
        if status == "Completed":
            output, truncated = _cap(response.get("output"))
            return {"status": "completed", "output": output, "truncated": truncated, "untrusted": True, "tool": name}
        detail = response.get("errorText") or ((response.get("exception") or {}).get("description")) or status
        return {"status": (status or "error").lower(), "error": str(detail)[:1000], "untrusted": True, "tool": name}


__all__ = ["OUTPUT_LIMIT_BYTES", "WebMcpTracker"]
