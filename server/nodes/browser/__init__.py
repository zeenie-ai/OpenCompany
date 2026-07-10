"""Plugins for the 'browser' palette group. See ../__init__.py for the package layout.

Self-registration on import:
  - FastAPI lifespan shutdown hook (Wave 12 C4 sub-piece B) — the
    agent-browser daemon needs an explicit close to release file
    locks. Registered through ``services.plugin.shutdown_hooks`` so
    ``main.py`` lifespan teardown reaches us without cross-plugin
    imports.
  - Eager-import the inner ``browser`` subpackage so ``BrowserNode``
    auto-registers regardless of ``pkgutil.walk_packages`` recursion
    behaviour. The conftest plugin-discovery path was occasionally
    skipping nested subpackages on CI Linux when their parent had a
    failing intermediate import; this belt-and-braces approach makes
    the registration deterministic.
"""

from services.plugin.shutdown_hooks import register_shutdown_hook

from ._service import shutdown_browser_service
from . import browser as _browser  # noqa: F401 — side-effect: registers BrowserNode

# Wave 19: browser-harness sibling plugin (browser-use/browser-harness).
# Registers BrowserHarnessNode + its own "browser_harness" shutdown hook
# from inside the subpackage; eager import keeps discovery deterministic.
from . import browser_harness as _browser_harness  # noqa: F401

register_shutdown_hook("browser_service", shutdown_browser_service)
