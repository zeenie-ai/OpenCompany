"""Wave 12 C4 sub-piece C: service-factories registry contract + invariant.

Locks three layers:

1. **Registry surface**: idempotent registration, retrieval, snapshot.

2. **Architectural invariant on core/container.py**: the DI container
   must NOT carry top-level imports of plugin service classes. The
   ``from nodes.location._service import MapsService`` /
   ``from nodes.android._dispatcher import AndroidService`` lines that
   lived at container.py:25 / :30 are gone — replaced by
   ``_build_registered_service("<name>", ...)`` lazy thunks that
   resolve through the registry at provider-instantiation time.

3. **Plugin self-registration smoke**: importing the location and
   android plugins populates the registry.
"""

from __future__ import annotations

import re
import sys
import types
from unittest.mock import MagicMock

import pytest


if "machina" not in sys.modules:
    _machina = types.ModuleType("cli")
    _machina.__path__ = []
    sys.modules["cli"] = _machina
    _machina_tcp = types.ModuleType("cli.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _machina_tcp


@pytest.fixture
def fresh_registry(monkeypatch):
    """Reset registry's backing dict for test isolation."""
    from services.plugin import service_factories as sf

    fresh = type(sf._REGISTRY)(sf._REGISTRY._name)  # type: ignore[attr-defined]
    monkeypatch.setattr(sf, "_REGISTRY", fresh)
    return sf


class TestRegistryContract:
    def test_unregistered_name_returns_none(self, fresh_registry):
        assert fresh_registry.get_service_factory("maps") is None

    def test_register_then_get_returns_callable(self, fresh_registry):
        class FakeService:
            def __init__(self, *, auth_service=None, settings=None):
                self.auth_service = auth_service
                self.settings = settings

        fresh_registry.register_service_factory("maps", FakeService)
        factory = fresh_registry.get_service_factory("maps")

        assert factory is FakeService
        # Calling it constructs the instance with kwargs.
        instance = factory(auth_service="A", settings="S")
        assert instance.auth_service == "A"
        assert instance.settings == "S"

    def test_idempotent_same_callable(self, fresh_registry):
        class FakeService:
            pass

        fresh_registry.register_service_factory("maps", FakeService)
        fresh_registry.register_service_factory("maps", FakeService)
        assert fresh_registry.registered_service_names() == frozenset({"maps"})

    def test_conflicting_register_raises(self, fresh_registry):
        class A:
            pass

        class B:
            pass

        fresh_registry.register_service_factory("maps", A)
        with pytest.raises(ValueError, match="already registered"):
            fresh_registry.register_service_factory("maps", B)

    def test_multiple_services_coexist(self, fresh_registry):
        class A:
            pass

        class B:
            pass

        fresh_registry.register_service_factory("maps", A)
        fresh_registry.register_service_factory("android", B)
        assert fresh_registry.registered_service_names() == frozenset({"maps", "android"})


class TestContainerHasNoCrossPluginServiceImports:
    """The DI container must NOT carry plugin-implementation imports
    at module scope. Reads container.py as source to bypass its own
    runtime import requirements.
    """

    _CONTAINER_PY = "core/container.py"

    _FORBIDDEN_IMPORTS = (
        ("nodes.location._service", "MapsService"),
        ("nodes.android._dispatcher", "AndroidService"),
    )

    def _read_container_source(self) -> str:
        import pathlib

        path = pathlib.Path(__file__).resolve().parent.parent / self._CONTAINER_PY
        assert path.exists(), f"Could not locate container.py at {path}"
        return path.read_text(encoding="utf-8")

    def test_container_drops_legacy_service_imports(self):
        src = self._read_container_source()

        for module_path, symbol in self._FORBIDDEN_IMPORTS:
            pattern = re.compile(
                rf"from\s+{re.escape(module_path)}\s+import\s+{re.escape(symbol)}",
                re.MULTILINE,
            )
            assert pattern.search(src) is None, (
                f"core/container.py still imports {symbol} from {module_path}. "
                "Service classes must be looked up via "
                "services.plugin.service_factories.get_service_factory at "
                "instantiation time. Plugin should call "
                "register_service_factory(...) from its own __init__.py."
            )

    def test_container_uses_lazy_factory_resolver(self):
        src = self._read_container_source()
        assert "_build_registered_service" in src, (
            "core/container.py must declare _build_registered_service "
            "(or an equivalent lazy lookup) so providers resolve plugin "
            "factories via the registry instead of importing the classes."
        )


class TestPluginSelfRegistration:
    """Smoke: importing each plugin populates the registry."""

    @pytest.mark.parametrize("plugin_module,expected_name", [
        ("nodes.location", "maps"),
        ("nodes.android", "android"),
    ])
    def test_plugin_import_registers_its_factory(self, plugin_module, expected_name):
        from services.plugin import service_factories as sf

        try:
            __import__(plugin_module)
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"plugin module {plugin_module} not importable: {exc}")

        factory = sf.get_service_factory(expected_name)
        assert factory is not None, (
            f"Importing {plugin_module} should call "
            f"register_service_factory({expected_name!r}, ...). "
            f"Current registered names: {sf.registered_service_names()}"
        )
        # Sanity: it's a class (or at least callable).
        assert callable(factory)
