"""Shared fixtures for all server tests.

Stubs heavy / circular imports (logging, container, encryption, pricing)
before any handler/service is loaded so node tests don't pay the cost of
real DI or Temporal runtimes during collection.
"""

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Register shared fixtures (the real database, the usage recorders' stand-ins)
# through pytest rather than importing a sibling conftest, whose directory
# scope depends on collection order.
pytest_plugins = ("tests.database_fixtures", "tests.usage_fixtures")

# Ensure server/ is on sys.path
SERVER_DIR = Path(__file__).parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# The OpenCompany Gateway is on by default (core/config.py). Under test it is
# off, as on an install with no gateway key: once the real core.container is
# imported (middleware.auth does), its router would read the credentials
# database, which most tests never set up. Gateway tests build their router
# with explicit settings.
os.environ["OPENCOMPANY_GATEWAY_ENABLED"] = "false"


def _make_package(name: str) -> types.ModuleType:
    """Force a fresh stub package, replacing whatever is in sys.modules."""
    pkg = types.ModuleType(name)
    pkg.__path__ = []  # marks it as a package
    sys.modules[name] = pkg
    return pkg


def _make_submodule(parent_name: str, child: str, attrs: dict | None = None) -> types.ModuleType:
    """Register a stub submodule and attach it to its parent package."""
    full_name = f"{parent_name}.{child}"
    mod = types.ModuleType(full_name)
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[full_name] = mod
    parent = sys.modules.get(parent_name)
    if parent is not None:
        setattr(parent, child, mod)
    return mod


def _stub_log_execution_time(*_args, **_kwargs):
    """Identity decorator stub for `core.logging.log_execution_time`."""

    def _decorator(fn):
        return fn

    # Support both @log_execution_time and @log_execution_time("name")
    if _args and callable(_args[0]) and not _kwargs:
        return _args[0]
    return _decorator


# core.* package -- handlers do `from core.container import container`,
# `from core.logging import get_logger`, etc. Build a stub package with
# real submodule attributes so imports resolve without touching the real
# heavy deps (dependency_injector, cryptography, ...).
_core_pkg = _make_package("core")


def _stub_log_context(**_fields):
    """Stub for ``core.logging.log_context`` (async ctx manager) and
    ``log_context_sync``. Both are no-ops under test."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _async_cm():
        yield

    return _async_cm()


def _stub_log_context_sync(**_fields):
    from contextlib import contextmanager

    @contextmanager
    def _sync_cm():
        yield

    return _sync_cm()


_make_submodule(
    "core",
    "logging",
    {
        "get_logger": MagicMock(return_value=MagicMock()),
        "log_execution_time": _stub_log_execution_time,
        "log_api_call": MagicMock(),
        "log_context": _stub_log_context,
        "log_context_sync": _stub_log_context_sync,
    },
)
_make_submodule("core", "container", {"container": MagicMock()})
_make_submodule("core", "database", {"Database": MagicMock})
_make_submodule("core", "credentials_database", {"CredentialsDatabase": MagicMock})
_make_submodule("core", "config", {"Settings": MagicMock})
_make_submodule("core", "cache", {"CacheService": MagicMock})

# core.ansi — load the REAL module from file (it only depends on ``rich``, no
# heavy core deps), so the stubbed-core environment still exposes the genuine
# ``strip_ansi`` that process_service / the shell node import at module load.
# File-load (not a lambda) keeps tests exercising the actual implementation.
import importlib.util as _importlib_util  # noqa: E402

_ansi_spec = _importlib_util.spec_from_file_location("core.ansi", SERVER_DIR / "core" / "ansi.py")
_ansi_mod = _importlib_util.module_from_spec(_ansi_spec)
sys.modules["core.ansi"] = _ansi_mod
_ansi_spec.loader.exec_module(_ansi_mod)
setattr(_core_pkg, "ansi", _ansi_mod)

# core.auth_cookies is dependency-free and its canonical/legacy lookup is
# part of the auth migration contract, so expose the real module under the
# otherwise stubbed core package.
_auth_cookies_spec = _importlib_util.spec_from_file_location(
    "core.auth_cookies",
    SERVER_DIR / "core" / "auth_cookies.py",
)
_auth_cookies_mod = _importlib_util.module_from_spec(_auth_cookies_spec)
sys.modules["core.auth_cookies"] = _auth_cookies_mod
_auth_cookies_spec.loader.exec_module(_auth_cookies_mod)
setattr(_core_pkg, "auth_cookies", _auth_cookies_mod)

# core.approot is stdlib-only and is imported at module load by
# core.env_defaults, core.paths and core.config (app-tree root + env file
# locations, OPENCOMPANY_APP_ROOT override). It must be resolvable under
# the stubbed package BEFORE those modules are file-loaded below.
_approot_spec = _importlib_util.spec_from_file_location(
    "core.approot",
    SERVER_DIR / "core" / "approot.py",
)
_approot_mod = _importlib_util.module_from_spec(_approot_spec)
sys.modules["core.approot"] = _approot_mod
_approot_spec.loader.exec_module(_approot_mod)
setattr(_core_pkg, "approot", _approot_mod)

# core.env_defaults is stdlib-only (the .env.template-backed env
# resolver plugin folders use instead of hardcoded port fallbacks) —
# expose the real module so plugin imports resolve during collection.
_env_defaults_spec = _importlib_util.spec_from_file_location(
    "core.env_defaults",
    SERVER_DIR / "core" / "env_defaults.py",
)
_env_defaults_mod = _importlib_util.module_from_spec(_env_defaults_spec)
sys.modules["core.env_defaults"] = _env_defaults_mod
_env_defaults_spec.loader.exec_module(_env_defaults_mod)
setattr(_core_pkg, "env_defaults", _env_defaults_mod)

# core.encryption depends only on stdlib + cryptography, and its
# fingerprint_credential is imported at module load by services.llm.unifier
# and services.memory.vector_store, so expose the real module.
_encryption_spec = _importlib_util.spec_from_file_location(
    "core.encryption",
    SERVER_DIR / "core" / "encryption.py",
)
_encryption_mod = _importlib_util.module_from_spec(_encryption_spec)
sys.modules["core.encryption"] = _encryption_mod
_encryption_spec.loader.exec_module(_encryption_mod)
setattr(_core_pkg, "encryption", _encryption_mod)

# core.session_teardown is stdlib-only (the shielded rollback + close both
# databases run under a cancelled caller); the real core/database.py and
# core/credentials_database.py import it at module load, and tests that
# file-load those modules need it resolvable under the stubbed package.
_session_teardown_spec = _importlib_util.spec_from_file_location(
    "core.session_teardown",
    SERVER_DIR / "core" / "session_teardown.py",
)
_session_teardown_mod = _importlib_util.module_from_spec(_session_teardown_spec)
sys.modules["core.session_teardown"] = _session_teardown_mod
_session_teardown_spec.loader.exec_module(_session_teardown_mod)
setattr(_core_pkg, "session_teardown", _session_teardown_mod)

# core.paths — central path resolution. Stub the public surface with
# tmpdir-rooted Paths so plugin module imports don't trip over the
# real ``Path.home()`` lookup during test collection. Tests that
# actually exercise on-disk behaviour use ``tempfile.TemporaryDirectory``
# locally; the stub just keeps import-time ``OPENCOMPANY_CLAUDE_DIR =
# data_path("claude")`` calls from blowing up.
_TEST_OPENCOMPANY_ROOT = Path(__file__).parent / "_test_opencompany_root"
# safe_path_component is pure stdlib and consumed at plugin import time
# (process_manager's agent_dir), so expose the REAL implementation on the
# stub — file-load under a private name, same rationale as core.ansi above.
_paths_spec = _importlib_util.spec_from_file_location("_real_core_paths", SERVER_DIR / "core" / "paths.py")
_paths_mod = _importlib_util.module_from_spec(_paths_spec)
_paths_spec.loader.exec_module(_paths_mod)
_make_submodule(
    "core",
    "paths",
    {
        "safe_path_component": _paths_mod.safe_path_component,
        "project_root": lambda: _TEST_OPENCOMPANY_ROOT.parent,
        "opencompany_root": lambda: _TEST_OPENCOMPANY_ROOT,
        "machina_root": lambda: _TEST_OPENCOMPANY_ROOT,
        "data_path": lambda sub="": _TEST_OPENCOMPANY_ROOT / sub if sub else _TEST_OPENCOMPANY_ROOT,
        "packages_dir": lambda: _TEST_OPENCOMPANY_ROOT / "packages",
        "package_dir": lambda name: _TEST_OPENCOMPANY_ROOT / "packages" / name,
        "daemons_dir": lambda: _TEST_OPENCOMPANY_ROOT / "daemons",
        "workspaces_dir": lambda: _TEST_OPENCOMPANY_ROOT / "workspaces",
        "workspace_dir": lambda wf: _TEST_OPENCOMPANY_ROOT / "workspaces" / wf,
        "example_workflows_dir": lambda: _TEST_OPENCOMPANY_ROOT / "workflows",
    },
)

# core.js_runtime — how the backend finds bun and extends the shared
# packages tree. Stdlib + core.paths (the stub above), imported at module
# load by the JS executor runtime and the npm-shipped CLI plugin
# installers, so it must be resolvable under the stubbed package.
_js_runtime_spec = _importlib_util.spec_from_file_location(
    "core.js_runtime",
    SERVER_DIR / "core" / "js_runtime.py",
)
_js_runtime_mod = _importlib_util.module_from_spec(_js_runtime_spec)
sys.modules["core.js_runtime"] = _js_runtime_mod
_js_runtime_spec.loader.exec_module(_js_runtime_mod)
setattr(_core_pkg, "js_runtime", _js_runtime_mod)


# services.pricing -- pre-stub the singleton so handler modules that do
# `from services.pricing import get_pricing_service` at module load time
# pick up the stub instead of loading the real PricingService (which reads
# config/pricing.json and is irrelevant to handler contract tests).
def _make_pricing_stub():
    pricing = MagicMock(name="StubPricingService")
    pricing.calculate_api_cost = MagicMock(return_value={"operation": "stub", "total_cost": 0.0})
    pricing.calculate_cost = MagicMock(
        return_value={
            "input_cost": 0.0,
            "output_cost": 0.0,
            "cache_cost": 0.0,
            "total_cost": 0.0,
        }
    )
    pricing.get_pricing = MagicMock(return_value=None)
    return pricing


_pricing_singleton = _make_pricing_stub()
# Pre-stub services.pricing in sys.modules so subsequent
# `from services.pricing import get_pricing_service` resolves to the stub.
# We deliberately do NOT pre-create `services` as a stub package, because
# the real `services/` directory must remain importable for everything else.
if "services.pricing" not in sys.modules:
    _pricing_mod = types.ModuleType("services.pricing")
    _pricing_mod.get_pricing_service = MagicMock(return_value=_pricing_singleton)
    _pricing_mod.PricingService = MagicMock(return_value=_pricing_singleton)
    sys.modules["services.pricing"] = _pricing_mod


# Wave 10.C: discover node plugins once per test session so every test
# sees the fully-populated NODE_METADATA + handler + input/output
# registries. Previously the legacy hardcoded NODE_METADATA dict seeded
# entries at import; now plugin modules do, and they have to run before
# any test calls get_node_spec() / NODE_METADATA.get(...).
try:
    import nodes  # noqa: F401,E402  -- side-effect: register_node calls
except Exception:
    # If plugin discovery fails (e.g. stubs not complete), let individual
    # tests surface the error rather than crashing collection.
    pass


@pytest.fixture
async def harness():
    """Fresh NodeTestHarness per test.

    Imported lazily so the conftest stubs above land before NodeExecutor pulls
    in its handler chain.

    Side-effects:
    - Registers the harness's service mocks (ai, android, maps, text) in the
      active-services registry so any `patched_container(...)` call the test
      makes also wires those onto `core.container.container`. Scaling-branch
      plugins resolve services via `container.X()` rather than
      NodeExecutor-injected services, so without this the harness's
      `execute_chat` / `execute_action` mocks would be orphaned.
    - Installs a baseline `patched_container()` for the life of the fixture
      so tests that don't explicitly patch the container still see wired
      services (and callers of `ApiKeyCredential.resolve()` can fail cleanly
      with PermissionError instead of calling the real credentials db).
    """
    from tests.nodes._harness import NodeTestHarness
    from tests.nodes._mocks import (
        register_harness_services,
        clear_harness_services,
        patched_container,
    )

    h = NodeTestHarness()
    register_harness_services(
        ai_service=h.ai_service,
        android_service=h.android_service,
        maps_service=h.maps_service,
        text_service=h.text_service,
        database=h.database,
    )
    try:
        with patched_container():
            yield h
    finally:
        clear_harness_services()
