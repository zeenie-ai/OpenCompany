"""Install the Browser node's runtime ahead of first use.

Normally the installed browser is selected and the browser-use CLI is fetched
the first time a Browser node runs. This entry point validates that browser
and installs the CLI ahead of time. With explicit ``BROWSER_RUNTIME=testing``,
it also downloads the pinned Chrome for Testing build::

    uv run python -m nodes.browser._install        # from server/
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict


async def ensure_browser_runtime(*, wait: float) -> Dict[str, Any]:
    from ._install_bu import get_browser_use_installer
    from ._install_chrome import get_chrome_installer

    chrome = await get_chrome_installer().ensure(wait=wait)
    cli = await get_browser_use_installer().ensure(wait=wait)
    return {"chrome": str(chrome), "cli": str(cli["cli"])}


def _main() -> int:
    try:
        result = asyncio.run(ensure_browser_runtime(wait=3600.0))
    except Exception as exc:  # noqa: BLE001 - a CLI reports and exits non-zero
        print(f"browser runtime install failed: {exc}", file=sys.stderr)
        return 1
    print(f"Chrome: {result['chrome']}")
    print(f"browser-use: {result['cli']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
