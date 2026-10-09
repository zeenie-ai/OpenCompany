"""What the Browser node puts in the Workspace's step log
(services/workspace_steps.py): one line per site action or screenshot, naming
what happened and on which site. Never what was typed, selected or pasted,
and nothing while a protected login is in progress."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from core.logging import get_logger

logger = get_logger(__name__)

#: ``{site}`` is the page's host after the action.
_WORDS = {
    "navigate": "Opened {site}",
    "click": "Clicked on {site}",
    "hover": "Pointed at something on {site}",
    "type": "Typed into a field on {site}",
    "press": "Pressed a key on {site}",
    "select": "Chose an option on {site}",
    "scroll": "Scrolled {site}",
    "back": "Went back on {site}",
    "forward": "Went forward on {site}",
    "reload": "Reloaded {site}",
    "webmcp_call": "Used a page tool on {site}",
    "evaluate": "Ran a script on {site}",
    "run_python": "Ran a script on {site}",
    "screenshot": "Took a screenshot of {site}",
}
_TABS = {"new": "Opened a new tab on {site}", "switch": "Switched to {site}", "close": "Closed a tab"}


def step_text(op: str, tab_action: str, url: Optional[str]) -> Optional[str]:
    """The step for ``op``, or None when it is not one (reads, waits)."""
    words = _TABS.get(tab_action) if op == "tabs" else _WORDS.get(op)
    if words is None:
        return None
    site = urlsplit(url or "").hostname or "the page"
    return words.format(site=site)


async def record_browser_step(ctx: Any, op: str, call: Any, out: Any, controller: Any) -> None:
    """Best effort: the action stands whether or not its step is kept."""
    if getattr(controller, "sensitive_login", False) or getattr(out, "success", True) is False:
        return
    text = step_text(op, str(getattr(call, "tab_action", "") or ""), getattr(out, "url", None))
    if text is None or not ctx.workflow_id:
        return
    try:
        from services.plugin.deps import get_database
        from services.workspace_steps import record_step

        await record_step(get_database(), workflow_id=ctx.workflow_id, surface="browser", text=text, node_id=ctx.node_id)
    except Exception:
        logger.warning("Browser step not recorded", node_id=ctx.node_id, exc_info=True)
