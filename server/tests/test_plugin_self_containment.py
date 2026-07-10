"""Plugin self-containment invariants (Wave 11.H plan, milestone H).

Nine invariant classes lock the contract that every migrated plugin
owns its full surface (handlers, router, service code) under
``server/nodes/<plugin>/`` and that nothing outside that folder
imports plugin internals by name.

Renaming a plugin file or moving plugin code back into ``services/``
or ``routers/`` will trip exactly one of these tests, the same
enforcement style as ``test_credential_broadcasts.py``.

Coverage map
------------
1. ``TestRoutersWebsocketHasNoPluginImports`` -- the central WS dispatch
   table imports zero plugin internals. Forbidden-fragment list.
2. ``TestNoPluginRouterOutsideNodes`` -- migrated plugins' FastAPI
   routers do not exist under ``server/routers/``. File-existence check.
3. ``TestPluginInitSelfRegisters`` -- every plugin folder with a
   ``_handlers.py`` or ``_router.py`` self-registers from its
   ``__init__.py``. Split into two parametrized tests against the
   explicit ``_PLUGINS_WITH_HANDLERS`` / ``_PLUGINS_WITH_ROUTERS``
   constants (no skips), plus a cross-check against the filesystem
   so the constants can't drift silently.
4. ``TestRegistryLookupsExist`` -- registry public API sanity.
5. ``TestStaleServiceFilesAbsent`` -- the 11 migrated old service
   paths must not be re-introduced. File-existence check.
6. ``TestMainPyDoesNotMountPluginRouters`` -- ``main.py`` does not
   wire plugin routers explicitly; they flow in via the plugin loop.
7. ``TestPluginHandlersDictsArePopulated`` -- when a plugin ships
   ``_handlers.py``, its registered surface is non-empty.
8. ``TestPluginFolderHasNodeFile`` -- every migrated plugin folder
   ships at least one public plugin file (a ``*.py`` not prefixed with
   ``_``). Parametrized; never skips. Covers the simple plugins
   (browser / code / email) that the conditional tests above skip.
9. ``TestPluginPackageImportsCleanly`` -- importing each migrated
   plugin package raises no exception. Parametrized; never skips.
   Catches circular imports / missing-dependency regressions before
   they hit a real startup.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import routers.websocket as ws_module
from services import ws_handler_registry


# Plugins migrated through Wave 11.H (commits A through F):
#   B = whatsapp     (commit 72b4ae7)
#   C = twitter      (commit 7ed846b)
#   D = google       (commit 1392cbb)
#   E = android      (commit 8306f47)
#   F = browser, email, code (commit 44e579e)
# telegram and stripe are the pre-Wave-11.H references.
_MIGRATED_PLUGINS = (
    "android",
    "browser",
    "code",
    "email",
    "github",
    "google",
    "stripe",
    "telegram",
    "twitter",
    "whatsapp",
)

# Plugins that ship a ``_handlers.py`` (credentials-modal WebSocket
# commands beyond Save / Load / Delete). Each entry MUST register via
# ``register_ws_handlers`` from its package ``__init__.py``.
_PLUGINS_WITH_HANDLERS = (
    "android",
    "github",
    "google",
    "stripe",
    "telegram",
    "twitter",
    "whatsapp",
)

# Plugins that ship a ``_router.py`` (FastAPI router for OAuth
# callbacks etc.). Each entry MUST register via ``register_router``
# from its package ``__init__.py``.
_PLUGINS_WITH_ROUTERS = (
    "android",
    "google",
    "twitter",
)

# Forbidden import substrings: any module path that would mean the
# plugin's surface still lives outside its plugin folder.
_FORBIDDEN_IMPORT_FRAGMENTS = (
    "services.whatsapp_service",
    "services.twitter_oauth",
    "services.google_oauth",
    "services.android",  # legacy relay sub-package
    "services.android_service",
    "services.browser_service",
    "services.email_service",
    "services.himalaya_service",
    "services.claude_code_service",
    "services.maps",  # Wave 11.I, N: -> nodes/location/_service
    "services.node_option_loaders",  # Wave 11.I, M: -> nodes/<plugin>/_option_loaders
    "routers.twitter",
    "routers.google",
    "routers.android",
    "routers.whatsapp",
    "routers.maps",  # Wave 11.I, N: deleted
)


_SERVER_ROOT = Path(__file__).resolve().parent.parent


class TestRoutersWebsocketHasNoPluginImports:
    """``routers/websocket.py`` is the central dispatch table only.

    It must not import any plugin's service or HTTP-router module by
    name. Plugin commands flow in via ``services.ws_handler_registry``.
    """

    def test_no_plugin_imports_in_websocket_router(self):
        src = inspect.getsource(ws_module)
        offenders = [frag for frag in _FORBIDDEN_IMPORT_FRAGMENTS if frag in src]
        assert not offenders, (
            "routers/websocket.py must not import migrated plugin modules. "
            f"Found references to: {offenders}. "
            "Move handler bodies into nodes/<plugin>/_handlers.py and "
            "self-register via register_ws_handlers."
        )


class TestNoPluginRouterOutsideNodes:
    """Once a plugin owns an HTTP router, the file must live under
    ``nodes/<plugin>/_router.py`` -- never in ``server/routers/``.

    ``server/routers/`` is reserved for cross-cutting routers
    (auth, websocket, webhook, workflow, database, maps,
    nodejs_compat, schemas, credentials). Maps/webhook are recorded
    here as still-shared dispatchers pending a future design pass.
    """

    _MUST_NOT_EXIST = (
        "twitter.py",
        "google.py",
        "android.py",
        "whatsapp.py",
    )

    def test_migrated_plugins_have_no_router_file_in_routers(self):
        routers_dir = _SERVER_ROOT / "routers"
        present = [name for name in self._MUST_NOT_EXIST if (routers_dir / name).exists()]
        assert not present, (
            f"server/routers/ contains files for migrated plugins: {present}. "
            "These belong under nodes/<plugin>/_router.py and mount via "
            "register_router from the plugin's __init__.py."
        )


class TestPluginInitSelfRegisters:
    """Every plugin folder that ships a ``_handlers.py`` or ``_router.py``
    must self-register from its ``__init__.py``. The package-import
    side effect is the single wiring point -- nothing elsewhere in the
    tree should be doing the registration on the plugin's behalf.

    Split into two parametrized tests against ``_PLUGINS_WITH_HANDLERS``
    and ``_PLUGINS_WITH_ROUTERS`` so no plugin is skipped: the lists
    explicitly enumerate which plugins ship which surfaces, and a new
    plugin shipping a handler / router file MUST add itself to the
    relevant list (otherwise the membership check below fails).
    """

    @pytest.mark.parametrize("plugin", _PLUGINS_WITH_HANDLERS)
    def test_plugin_with_handlers_self_registers(self, plugin: str):
        plugin_dir = _SERVER_ROOT / "nodes" / plugin
        handlers_path = plugin_dir / "_handlers.py"
        init_path = plugin_dir / "__init__.py"

        assert handlers_path.exists(), (
            f"nodes/{plugin}/_handlers.py missing -- remove {plugin!r} " "from _PLUGINS_WITH_HANDLERS or restore the file."
        )
        assert init_path.exists(), f"nodes/{plugin}/__init__.py missing"

        init_src = init_path.read_text(encoding="utf-8")
        assert "register_ws_handlers(" in init_src, (
            f"nodes/{plugin}/_handlers.py exists but "
            f"nodes/{plugin}/__init__.py does not call "
            "register_ws_handlers(...). The plugin's WS surface would "
            "never be wired up at startup."
        )

    @pytest.mark.parametrize("plugin", _PLUGINS_WITH_ROUTERS)
    def test_plugin_with_router_self_registers(self, plugin: str):
        plugin_dir = _SERVER_ROOT / "nodes" / plugin
        router_path = plugin_dir / "_router.py"
        init_path = plugin_dir / "__init__.py"

        assert router_path.exists(), (
            f"nodes/{plugin}/_router.py missing -- remove {plugin!r} " "from _PLUGINS_WITH_ROUTERS or restore the file."
        )
        assert init_path.exists(), f"nodes/{plugin}/__init__.py missing"

        init_src = init_path.read_text(encoding="utf-8")
        assert "register_router(" in init_src, (
            f"nodes/{plugin}/_router.py exists but "
            f"nodes/{plugin}/__init__.py does not call "
            "register_router(...). The plugin's HTTP router would "
            "never be mounted on the FastAPI app."
        )

    def test_handler_router_lists_match_filesystem(self):
        """Cross-check: every plugin folder with a ``_handlers.py`` or
        ``_router.py`` must appear in the corresponding constant.
        Catches a new plugin that ships a handler / router but forgot
        to add itself to the parametrize list.
        """
        actual_handlers = {p for p in _MIGRATED_PLUGINS if (_SERVER_ROOT / "nodes" / p / "_handlers.py").exists()}
        actual_routers = {p for p in _MIGRATED_PLUGINS if (_SERVER_ROOT / "nodes" / p / "_router.py").exists()}
        assert set(_PLUGINS_WITH_HANDLERS) == actual_handlers, (
            f"_PLUGINS_WITH_HANDLERS drifted from filesystem: "
            f"declared={sorted(_PLUGINS_WITH_HANDLERS)}, "
            f"actual={sorted(actual_handlers)}. Update the constant."
        )
        assert set(_PLUGINS_WITH_ROUTERS) == actual_routers, (
            f"_PLUGINS_WITH_ROUTERS drifted from filesystem: "
            f"declared={sorted(_PLUGINS_WITH_ROUTERS)}, "
            f"actual={sorted(actual_routers)}. Update the constant."
        )


class TestRegistryLookupsExist:
    """Sanity: the registries the plugin __init__.py call into must
    exist and expose the documented public functions. Catches accidental
    renames of the registry surface itself.
    """

    def test_register_ws_handlers_exists(self):
        assert hasattr(ws_handler_registry, "register_ws_handlers")
        assert callable(ws_handler_registry.register_ws_handlers)

    def test_register_router_exists(self):
        assert hasattr(ws_handler_registry, "register_router")
        assert callable(ws_handler_registry.register_router)

    def test_get_routers_exists(self):
        assert hasattr(ws_handler_registry, "get_routers")
        assert callable(ws_handler_registry.get_routers)


# Old service paths that were `git mv`'d into nodes/<plugin>/ during
# the migration. None of these should ever be re-created -- if a future
# refactor "needs" one, the work belongs in the plugin folder.
_STALE_SERVICE_PATHS = (
    "services/whatsapp_service.py",
    "services/twitter_oauth.py",
    "services/google_oauth.py",
    "services/handlers/google_auth.py",
    "services/android",  # the relay sub-package
    "services/android_service.py",
    "services/browser_service.py",
    "services/email_service.py",
    "services/himalaya_service.py",
    "services/claude_code_service.py",
    "services/websocket_client.py",  # dead re-export shim, deleted in E
    "services/maps.py",  # Wave 11.I, N: -> nodes/location/_service.py
    "services/node_option_loaders",  # Wave 11.I, M: -> nodes/<plugin>/_option_loaders.py
    "routers/twitter.py",
    "routers/google.py",
    "routers/android.py",
    "routers/maps.py",  # Wave 11.I, N: deleted (all 4 endpoints dead)
)


class TestStaleServiceFilesAbsent:
    """Files that were moved out of ``services/`` and ``routers/`` during
    the migration must not be re-introduced. Guards against an accidental
    revert via a fresh file (rather than a stale import, which test 1
    catches).
    """

    @pytest.mark.parametrize("relpath", _STALE_SERVICE_PATHS)
    def test_stale_path_does_not_exist(self, relpath: str):
        target = _SERVER_ROOT / relpath
        assert not target.exists(), (
            f"Stale path {relpath!r} re-appeared under server/. "
            "Migrated plugin code lives in nodes/<plugin>/ -- do not "
            "recreate the old location even with new contents."
        )


class TestMainPyDoesNotMountPluginRouters:
    """``server/main.py`` mounts framework routers explicitly
    (auth / websocket / workflow / database / maps / nodejs_compat /
    schemas / credentials / webhook). Plugin routers flow in via the
    ``for r in get_routers(): app.include_router(r)`` loop.

    Direct ``app.include_router(<plugin>.router)`` calls or
    ``from routers import <plugin>`` imports for migrated plugins are
    a regression: they short-circuit the plugin loop and double-mount
    the router under two different code paths.
    """

    _MIGRATED_ROUTER_NAMES = ("twitter", "google", "android", "whatsapp")

    def test_main_py_does_not_explicitly_mount_plugin_routers(self):
        main_path = _SERVER_ROOT / "main.py"
        assert main_path.exists(), "server/main.py missing"
        src = main_path.read_text(encoding="utf-8")
        offenders = [
            name
            for name in self._MIGRATED_ROUTER_NAMES
            if f"app.include_router({name}.router)" in src or f"from routers import {name}" in src or f"from routers.{name}" in src
        ]
        assert not offenders, (
            f"server/main.py explicitly mounts/imports migrated plugin routers: "
            f"{offenders}. These must flow in via the get_routers() plugin loop. "
            "Drop the explicit include_router(...) line and the routers.<name> "
            "import; plugin's __init__.py registers via register_router(...)."
        )

    def test_main_py_does_not_wire_plugin_modules(self):
        """``container.wire(modules=[...])`` should not name modules
        that have been migrated into nodes/<plugin>/. Stale wire entries
        for absent modules raise at startup."""
        main_path = _SERVER_ROOT / "main.py"
        src = main_path.read_text(encoding="utf-8")
        offenders = [f"routers.{name}" for name in self._MIGRATED_ROUTER_NAMES if f'"routers.{name}"' in src]
        assert not offenders, (
            f"server/main.py container.wire(...) names removed plugin modules: "
            f"{offenders}. Drop these entries -- the plugin packages wire their "
            "own dependencies."
        )


class TestPluginHandlersDictsArePopulated:
    """When a plugin ships a ``_handlers.py``, the ``WS_HANDLERS`` dict
    (or whatever the package's ``__init__.py`` imports under that name)
    must register at least one handler. An empty dict is the symptom of
    a partial migration where the file was created but the body wasn't
    moved over.

    The check is loose: we look for the literal ``WS_HANDLERS`` symbol
    in ``_handlers.py`` and assert it isn't an empty literal. This
    catches the most common partial-migration shape without forcing a
    specific dict-construction style.
    """

    @pytest.mark.parametrize("plugin", _PLUGINS_WITH_HANDLERS)
    def test_plugin_handlers_dict_non_empty(self, plugin: str):
        handlers_path = _SERVER_ROOT / "nodes" / plugin / "_handlers.py"
        assert handlers_path.exists(), (
            f"nodes/{plugin}/_handlers.py missing -- remove {plugin!r} " "from _PLUGINS_WITH_HANDLERS or restore the file."
        )

        src = handlers_path.read_text(encoding="utf-8")
        # Must export WS_HANDLERS (the documented surface used by
        # register_ws_handlers).
        assert "WS_HANDLERS" in src, (
            f"nodes/{plugin}/_handlers.py does not export WS_HANDLERS. "
            "The plugin's __init__.py reads this symbol; absence means "
            "the plugin self-registration is broken."
        )
        # Must not be the empty literal. Stripe builds via
        # make_lifecycle_handlers(...) so we accept either {...} with
        # at least one quoted key OR a function call.
        empty_literal_patterns = (
            "WS_HANDLERS = {}\n",
            "WS_HANDLERS={}\n",
            "WS_HANDLERS: dict = {}\n",
        )
        for pattern in empty_literal_patterns:
            assert pattern not in src, (
                f"nodes/{plugin}/_handlers.py defines an empty WS_HANDLERS dict. "
                "Move the handler bodies into _handlers.py (or wire via "
                "make_lifecycle_handlers) before declaring the migration done."
            )


# Files / subpackages the node-discovery walker treats as plugin entry
# points:
# 1. Top-level ``*.py`` not prefixed with ``_`` (legacy / Wave 11.H
#    single-plugin-per-group shape — e.g. nodes/telegram/telegram_send.py).
# 2. Top-level subpackages (subfolder + ``__init__.py``) not prefixed
#    with ``_`` (post-Phase-8 folder-default shape — e.g.
#    nodes/tool/calculator_tool/__init__.py).
# Underscore-prefixed siblings (``_service.py`` / ``_credentials.py``,
# ``_relay/``, etc.) are package-private and skipped by the walker.
def _public_plugin_files(plugin_dir: Path) -> list[Path]:
    flat = [p for p in plugin_dir.glob("*.py") if not p.name.startswith("_") and p.name != "__init__.py"]
    nested = [
        sub / "__init__.py"
        for sub in plugin_dir.iterdir()
        if sub.is_dir() and not sub.name.startswith("_") and sub.name != "__pycache__" and (sub / "__init__.py").exists()
    ]
    return flat + nested


class TestPluginFolderHasNodeFile:
    """Every migrated plugin folder must ship at least one public plugin
    file (a ``*.py`` not prefixed with ``_``). Catches the partial-
    extraction failure mode where the folder gets created with
    ``_service.py`` / ``_handlers.py`` but the actual ``BaseNode``
    subclass is forgotten.

    Unlike ``TestPluginInitSelfRegisters`` / ``TestPluginHandlersDictsArePopulated``
    this test never skips -- browser / code / email all ship plugin
    files even though they don't ship ``_handlers.py`` or ``_router.py``.
    """

    @pytest.mark.parametrize("plugin", _MIGRATED_PLUGINS)
    def test_plugin_folder_has_at_least_one_node_file(self, plugin: str):
        plugin_dir = _SERVER_ROOT / "nodes" / plugin
        assert plugin_dir.is_dir(), f"nodes/{plugin}/ is missing"
        plugin_files = _public_plugin_files(plugin_dir)
        assert plugin_files, (
            f"nodes/{plugin}/ has no public plugin files. The folder "
            "should contain at least one ``<name>.py`` declaring a "
            "BaseNode subclass; underscore-prefixed siblings "
            "(_service.py, _handlers.py, _credentials.py, ...) are "
            "package-private and skipped by the node-discovery walker."
        )


class TestPluginsUseTypedEventFactories:
    """Wave 11.I, milestone Q (locked in U).

    Plugins must construct :class:`WorkflowEvent` directly via the
    typed factory classmethods on ``WorkflowEvent`` (cross-cutting:
    ``credential``, ``connection_status``, ``oauth_completed``, ...) or
    via plugin-local typed factories in ``_events.py`` (plugin-specific
    payloads) -- not via the ``WorkflowEvent.from_legacy(event_type,
    data)`` shim. The shim exists so :func:`event_waiter.dispatch` can
    still accept the legacy ``(str, dict)`` form at the framework
    boundary; lifting it inside plugin code defeats the purpose.
    """

    @pytest.mark.parametrize("plugin", _MIGRATED_PLUGINS)
    def test_plugin_does_not_call_from_legacy(self, plugin: str):
        plugin_dir = _SERVER_ROOT / "nodes" / plugin
        offenders: list[str] = []
        for py in plugin_dir.rglob("*.py"):
            if "from_legacy" in py.read_text(encoding="utf-8"):
                offenders.append(str(py.relative_to(_SERVER_ROOT)))
        assert not offenders, (
            f"Plugin {plugin!r} uses WorkflowEvent.from_legacy in: "
            f"{offenders}. Use the typed factory classmethods "
            "(WorkflowEvent.credential / .message / .oauth_completed / "
            "etc.) for new dispatches; from_legacy is the framework-edge "
            "shim only."
        )


class TestPluginPackageImportsCleanly:
    """Every migrated plugin package must import without raising.

    Catches:
    - Circular imports introduced by mid-refactor ``from core.container``
      at module load time (the same class of bug the plugin-router
      cycle fix addressed in commit 9274072).
    - Missing dependencies that only surface at startup.
    - Syntax / decorator errors masked by lazy imports elsewhere.

    Parametrized over all 9 migrated plugins; never skips.
    """

    @pytest.mark.parametrize("plugin", _MIGRATED_PLUGINS)
    def test_plugin_package_imports(self, plugin: str):
        import importlib

        # If the package is already imported (likely, since the test
        # session does plugin discovery during setup), reload to
        # exercise the import path again -- catches regressions where
        # the original import succeeded only because of import order.
        module_name = f"nodes.{plugin}"
        module = importlib.import_module(module_name)
        importlib.reload(module)
        assert module.__name__ == module_name
