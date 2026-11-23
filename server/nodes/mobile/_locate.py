"""Finding an element the phone's UI tree does not name, on its screenshot.

When the engine's tap by text finds no element in the accessibility tree (a
canvas, a web view, an icon drawn without a label), the worker asks the
broker to ``locate`` it (``runtime/worker.py`` ``find_element``). The engine
tries that tap last, after a tap at the model's coordinates and one by
resource id. The screenshot goes to the judgment service
(``services/judgment``) with two questions: a noul, "An element showing
"<text>" is visible on this screen", and a box for that element. A box is
always returned, so the noul decides whether there is anything to tap.

It counts as found only when:
- the judgment is available (Jev: the chat-model fallback takes no images);
- the noul is at least ``ON_SCREEN_AT_LEAST``;
- the box's edges are ordered and inside the image, and still ordered once
  they are whole pixels.

The fractions become pixels with the screenshot's own size, which is the
space the driver taps in, and the tap that follows still passes the driver's
range and geometry checks. Anything else is "not found", as before.

The screenshot leaves the machine, for TypeSafe or OpenRouter.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Dict, Mapping

#: The least probability that the element is on screen before it is tapped.
ON_SCREEN_AT_LEAST = 0.8
#: The judgment's own time limit; the worker's broker request allows 35 s.
JUDGE_TIMEOUT_SECONDS = 15.0


def _size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        return image.size


def _questions(text: str) -> Dict[str, Dict[str, str]]:
    return {
        "on_screen": {"type": "noul", "description": f'An element showing "{text}" is visible on this screen.'},
        "where": {"type": "box", "description": f'The element showing "{text}".'},
    }


async def locate_on_screen(png_base64: str, text: str, run: Mapping[str, Any]) -> Dict[str, Any]:
    """``{"found": True, "bounds": {x1, y1, x2, y2}}`` in screenshot pixels, or
    ``{"found": False}``, with the judgment's ``reason`` when there was none.

    ``run`` says whose judgment it is (``MobileRuntime.run`` keeps it):
    ``workflow_id``, ``node_id`` and ``principal`` for the usage row and the
    key, and ``log``, the run's log context. The broker answers in the
    context of whichever run started it, so the run's own is bound while the
    judgment runs, and its cost counts for this run's chat run and employee
    job.
    """
    import structlog

    from services.judgment import JudgmentImage, judge

    png = base64.b64decode(png_base64)
    width, height = _size(png)
    workflow_id = run.get("workflow_id")
    # structlog's own binder (``core.logging.log_context`` wraps it): the
    # usage recorders read this context.
    with structlog.contextvars.bound_contextvars(**dict(run.get("log") or {})):
        judgment = await judge(
            "A phone screen.",
            _questions(text),
            node_id=str(run.get("node_id") or "mobile"),
            workflow_id=workflow_id,
            session_id=str(workflow_id or "default"),
            principal=run.get("principal"),
            images=[JudgmentImage(data=png, format="png")],
            timeout=JUDGE_TIMEOUT_SECONDS,
        )
    if not judgment.available:
        return {"found": False, "reason": judgment.reason}
    seen = judgment.answers["on_screen"].probability
    box = judgment.answers["where"]
    edges = (box.left, box.top, box.right, box.bottom)
    if seen is None or seen < ON_SCREEN_AT_LEAST or any(edge is None for edge in edges):
        return {"found": False}
    if not (0.0 <= box.left < box.right <= 1.0 and 0.0 <= box.top < box.bottom <= 1.0):
        return {"found": False}
    x1, x2 = round(box.left * width), round(box.right * width)
    y1, y2 = round(box.top * height), round(box.bottom * height)
    if x1 >= x2 or y1 >= y2:
        return {"found": False}
    return {"found": True, "bounds": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


__all__ = ["JUDGE_TIMEOUT_SECONDS", "ON_SCREEN_AT_LEAST", "locate_on_screen"]
