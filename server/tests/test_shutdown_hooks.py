"""Wave 12 C4 sub-piece B: shutdown-hooks registry contract + invariant.

Locks three layers:

1. **Registry surface**: idempotent registration, deterministic
   ordering, per-hook failure isolation.

2. **Architectural invariant on main.py**: the FastAPI lifespan must
   NOT cross-import plugin internals for shutdown. The
   ``from nodes.android._relay.manager import close_relay_client`` /
   ``from nodes.browser._service import shutdown_browser_service``
   lines that lived at main.py:370/374 are gone — replaced by
   ``run_shutdown_hooks()`` from this registry.

3. **Plugin self-registration smoke**: importing the android and
   browser plugins populates the registry.

Same source-introspection pattern as ``test_canary_registry.py`` and
``test_social_provider_registry.py``.
"""

from __future__ import annotations

import re
import sys
import types
from typing import List
from unittest.mock import MagicMock

import pytest


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


@pytest.fixture
def fresh_registry(monkeypatch):
    """Reset registry's backing dict for test isolation."""
    from services.plugin import shutdown_hooks as sh

    fresh = type(sh._REGISTRY)(sh._REGISTRY._name)  # type: ignore[attr-defined]
    monkeypatch.setattr(sh, "_REGISTRY", fresh)
    return sh


class TestRegistryContract:
    @pytest.mark.asyncio
    async def test_register_then_run_invokes_hook(self, fresh_registry):
        calls: List[str] = []

        async def hook():
            calls.append("ran")

        fresh_registry.register_shutdown_hook("test_hook", hook)
        await fresh_registry.run_shutdown_hooks()

        assert calls == ["ran"]

    @pytest.mark.asyncio
    async def test_run_with_no_hooks_is_noop(self, fresh_registry):
        # No exception, no log spam, just returns.
        await fresh_registry.run_shutdown_hooks()
        assert fresh_registry.registered_labels() == ()

    @pytest.mark.asyncio
    async def test_hooks_run_in_registration_order(self, fresh_registry):
        order: List[str] = []

        async def first():
            order.append("first")

        async def second():
            order.append("second")

        async def third():
            order.append("third")

        fresh_registry.register_shutdown_hook("a_first", first)
        fresh_registry.register_shutdown_hook("b_second", second)
        fresh_registry.register_shutdown_hook("c_third", third)

        await fresh_registry.run_shutdown_hooks()
        assert order == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_block_siblings(self, fresh_registry):
        """One broken plugin can't strand the rest of teardown."""
        survivors: List[str] = []

        async def broken():
            raise RuntimeError("simulated plugin shutdown crash")

        async def survives():
            survivors.append("ok")

        fresh_registry.register_shutdown_hook("broken_first", broken)
        fresh_registry.register_shutdown_hook("survives_second", survives)

        # No exception bubbles up — log + continue.
        await fresh_registry.run_shutdown_hooks()
        assert survivors == ["ok"]

    def test_idempotent_same_callable(self, fresh_registry):
        async def h():
            return None

        fresh_registry.register_shutdown_hook("x", h)
        fresh_registry.register_shutdown_hook("x", h)
        assert fresh_registry.registered_labels() == ("x",)

    def test_conflicting_register_raises(self, fresh_registry):
        async def h1():
            return None

        async def h2():
            return None

        fresh_registry.register_shutdown_hook("x", h1)
        with pytest.raises(ValueError, match="already registered"):
            fresh_registry.register_shutdown_hook("x", h2)


class TestNoCrossPluginShutdownReachesInMain:
    """Lifespan teardown in main.py must not import plugin internals.

    Reads ``main.py`` as a source file (not via import) — the module
    has top-level imports that aren't satisfiable in the test env, and
    importing isn't needed for the architectural assertion: we're
    checking what the file *says*, not what it does at runtime.
    """

    _MAIN_PY_PATH = "main.py"

    _FORBIDDEN_LIFESPAN_IMPORTS = (
        ("nodes.android._relay.manager", "close_relay_client"),
        ("nodes.browser._service", "shutdown_browser_service"),
    )

    def _read_main_source(self) -> str:
        # `tests/` and `main.py` are siblings under server/.
        import pathlib

        main_py = pathlib.Path(__file__).resolve().parent.parent / self._MAIN_PY_PATH
        assert main_py.exists(), f"Could not locate main.py at {main_py}"
        return main_py.read_text(encoding="utf-8")

    def test_main_drops_legacy_shutdown_imports(self):
        src = self._read_main_source()

        for module_path, symbol in self._FORBIDDEN_LIFESPAN_IMPORTS:
            pattern = re.compile(
                rf"from\s+{re.escape(module_path)}\s+import\s+{re.escape(symbol)}",
                re.MULTILINE,
            )
            assert pattern.search(src) is None, (
                f"main.py still imports {symbol} from {module_path}. "
                "Lifespan teardown must route through "
                "services.plugin.shutdown_hooks.run_shutdown_hooks(). "
                "Plugin should call register_shutdown_hook(...) from its "
                "own __init__.py."
            )

    def test_main_invokes_run_shutdown_hooks(self):
        src = self._read_main_source()
        assert "run_shutdown_hooks" in src, (
            "main.py lifespan teardown must call "
            "services.plugin.shutdown_hooks.run_shutdown_hooks(). "
            "Without it, plugins that register hooks never drain."
        )


class TestPluginSelfRegistration:
    """Smoke: importing each plugin populates the registry."""

    @pytest.mark.parametrize(
        "plugin_module,expected_label",
        [
            ("nodes.android", "android_relay"),
            ("nodes.browser", "browser_service"),
        ],
    )
    def test_plugin_import_registers_its_hook(self, plugin_module, expected_label):
        from services.plugin import shutdown_hooks as sh

        try:
            __import__(plugin_module)
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"plugin module {plugin_module} not importable: {exc}")

        labels = sh.registered_labels()
        assert expected_label in labels, (
            f"Importing {plugin_module} should call " f"register_shutdown_hook({expected_label!r}, ...). " f"Current labels: {labels}"
        )
