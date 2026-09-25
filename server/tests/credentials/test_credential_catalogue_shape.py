"""Pytest invariant: every catalogue provider lives under ``providers``.

``services.credential_registry.CredentialRegistry`` reads ONLY
``raw["providers"]`` from ``config/credential_providers.json``. A provider
object accidentally written as a sibling of ``providers`` at the JSON root
parses fine, passes ``json.load``, and simply never becomes a credential
tile — which is exactly how DeepL shipped without one. This test locks the
file's top-level shape and cross-checks the registered credential classes
against the resolved catalogue so the mistake fails CI instead of silently
dropping a provider from the modal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.credentials


SERVER_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = SERVER_DIR / "config" / "credential_providers.json"

# The only keys the file may carry at its root. Metadata + the sections the
# registry actually reads (``categories`` / ``consumer_categories`` /
# ``providers``).
EXPECTED_TOP_LEVEL_KEYS = frozenset(
    {"version", "last_updated", "_description", "categories", "consumer_categories", "providers"}
)


def _load_raw() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class TestTopLevelShape:
    def test_top_level_keys_are_exactly_the_expected_set(self):
        raw = _load_raw()
        actual = frozenset(raw.keys())
        stray = actual - EXPECTED_TOP_LEVEL_KEYS
        missing = EXPECTED_TOP_LEVEL_KEYS - actual
        assert not stray, (
            f"Unexpected top-level keys in credential_providers.json: {sorted(stray)}. "
            "Provider objects must live under 'providers' — the registry never reads root-level siblings."
        )
        assert not missing, f"Missing top-level keys in credential_providers.json: {sorted(missing)}"

    def test_no_provider_shaped_object_at_root(self):
        """A root-level object carrying ``fields`` / ``kind`` / ``extends`` is
        a provider that fell outside ``providers``."""
        raw = _load_raw()
        provider_markers = {"fields", "kind", "extends", "category"}
        for key, value in raw.items():
            if key == "providers" or not isinstance(value, dict):
                continue
            if key == "categories":
                continue
            assert not (provider_markers & set(value)), (
                f"Root-level key {key!r} looks like a provider entry ({sorted(provider_markers & set(value))}); "
                "move it inside 'providers'."
            )


class TestRegisteredCredentialsHaveCatalogueTiles:
    def test_deepl_resolves_through_the_real_registry(self):
        from services.credential_registry import CredentialRegistry

        registry = CredentialRegistry()  # fresh instance, not the singleton
        entry = registry.get_provider("deepl")
        assert entry is not None, "DeepL must resolve from providers via CredentialRegistry.get_provider"
        assert entry["name"] == "DeepL"
        # ``extends: _ai_base`` merges ``fields`` BY KEY, so the child must
        # override the base's ``apiKey`` field rather than declare a second
        # key — otherwise the tile renders two inputs.
        assert [f["key"] for f in entry["fields"]] == ["apiKey"]
        assert entry["fields"][0]["secret"] is True
        assert entry["fields"][0]["placeholder"].endswith("[:fx]")
        ids = [p["id"] for p in registry.get_all_providers()]
        assert "deepl" in ids

    def test_every_registered_credential_with_a_catalogue_key_is_under_providers(self):
        """For each ``Credential`` subclass id in ``CREDENTIAL_REGISTRY``: if the
        JSON mentions that id as a key ANYWHERE at the root, it must be the
        ``providers`` section that carries it. Registered credentials without
        any catalogue entry are allowed (some are declared inline by nodes
        that share another provider's stored key)."""
        import nodes  # noqa: F401 — triggers plugin discovery + credential registration
        from services.plugin.credential import CREDENTIAL_REGISTRY

        raw = _load_raw()
        providers = raw["providers"]
        misplaced = sorted(cid for cid in CREDENTIAL_REGISTRY if cid in raw and cid not in providers)
        assert not misplaced, f"Credential ids present at the JSON root instead of under 'providers': {misplaced}"
        # DeepL specifically must have a tile.
        assert "deepl" in CREDENTIAL_REGISTRY
        assert "deepl" in providers
