"""Where a custom connector's OAuth sign-in comes back
(``_oauth.CALLBACK_PATH``): the server's authorization server sends the
owner's browser here once they signed in. The route sits behind the app's
own sign-in like the rest of ``/api``, and the page says how the sign-in
went (``render_oauth_callback_html`` escapes what the server sent).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from services.events.oauth_lifecycle import render_oauth_callback_html

from ._oauth import CALLBACK_PATH, finish

#: The plugin's colour (meta.json).
_COLOR = "#8be9fd"

router = APIRouter(tags=["mcp"])


@router.get(CALLBACK_PATH)
async def sign_in_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
) -> HTMLResponse:
    ok, message = await finish(state or "", code=code, error=error_description or error)
    page = render_oauth_callback_html("mcp", status="success" if ok else "error", message=message, color_hex=_COLOR, title="Signed in")
    return HTMLResponse(content=page, status_code=200)


__all__ = ["router"]
