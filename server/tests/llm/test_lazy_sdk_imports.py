"""Boot-path purity: registering LLM providers must not import their SDKs.

The provider registration blocks used to do eager ``import anthropic`` /
``import openai`` / ``from google.genai import errors`` at module bottom
just to populate ``ProviderSpec.sdk_exception_types`` — reintroducing the
eager-LLM-SDK-at-boot anti-pattern (docs-internal/performance.md) at
~7s warm / ~45s cold. The specs now carry lazy ``"module:ClassName"``
refs resolved via ``pkgutil.resolve_name`` at except/read time.

These tests run the import in a clean subprocess (the pytest process
itself has SDKs loaded by other tests) and assert none of the heavy SDK
modules land in ``sys.modules``. They are the guard that keeps the next
provider from quietly reintroducing the eager import.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[2]

_HEAVY_SDKS = ("openai", "anthropic", "google.genai")


def _run_probe(code: str) -> str:
    """Run ``code`` in a clean interpreter with cwd=server/ and return stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=SERVER_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed (rc={result.returncode}):\n{result.stderr}"
    )
    return result.stdout


def test_importing_llm_package_does_not_import_sdks():
    out = _run_probe(
        "import sys\n"
        "import services.llm\n"
        "import services.llm.providers\n"
        f"leaked = [m for m in {_HEAVY_SDKS!r} if m in sys.modules]\n"
        "print('LEAKED=' + ','.join(leaked))\n"
    )
    assert "LEAKED=\n" in out or out.strip().endswith("LEAKED="), (
        f"provider registration imported heavy SDKs at boot: {out!r}"
    )


def test_importing_services_ai_does_not_import_openai():
    out = _run_probe(
        "import sys\n"
        "import services.ai\n"
        "print('LEAKED=' + ('openai' if 'openai' in sys.modules else ''))\n"
    )
    assert out.strip().endswith("LEAKED="), (
        f"services.ai imported the openai SDK at module level: {out!r}"
    )


def test_all_registered_refs_resolve_to_exception_classes():
    """Every declared ref must resolve — typo guard, run in-process.

    Complements ``test_plugin_shape.py``'s non-empty/exception-class
    checks: this exercises the actual lazy resolution path end-to-end
    for every registered provider.
    """
    import services.llm.providers  # noqa: F401 — triggers registration

    from services.llm.registry import all_providers, get_provider

    for name in all_providers():
        spec = get_provider(name)
        resolved = spec.sdk_exception_types
        assert resolved, f"provider {name!r} resolved to an empty tuple"
        for exc in resolved:
            assert isinstance(exc, type) and issubclass(exc, BaseException), (
                f"provider {name!r} ref resolved to non-exception: {exc!r}"
            )
