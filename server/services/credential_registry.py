"""Credential registry - loads server/config/credential_providers.json.

Lazy singleton service backed by a single JSON file. Supports `extends`
inheritance with array-merge-by-key semantics (port of n8n's
NodeHelpers.mergeNodeProperties pattern).

Served to clients via the `get_credential_catalogue` WebSocket handler,
which returns `{providers, categories, version}`. The version is a
content-sha256 of the resolved catalogue so the client can warm-start from
IndexedDB and revalidate only when the server content changes.

Follows the same lazy-singleton pattern as services/email_service.py.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.logging import get_logger

logger = get_logger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "credential_providers.json"

# Arrays that are merged by `key` instead of replaced wholesale when a child
# extends a parent. Matches the shape defined in credential_providers.json.
_MERGE_BY_KEY_ARRAYS: Set[str] = {"fields", "status_rows", "actions"}


class CredentialRegistryError(RuntimeError):
    """Raised for malformed registry files or resolution cycles."""


class CredentialRegistry:
    """Lazy singleton wrapper around credential_providers.json."""

    _instance: Optional["CredentialRegistry"] = None

    def __init__(self) -> None:
        self._raw: Optional[Dict[str, Any]] = None
        self._resolved_providers: Optional[Dict[str, Dict[str, Any]]] = None
        self._version: Optional[str] = None
        # Bumped on every credential mutation; folded into the version
        # hash so the conditional fetch returns fresh ``stored`` flags.
        # See ``get_version()`` + ``invalidate_version()``.
        self._mutation_seq: int = 0

    @classmethod
    def get_instance(cls) -> "CredentialRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----- loading -----

    def _load_raw(self) -> Dict[str, Any]:
        """Parse the JSON file once, cache in memory for app lifetime."""
        if self._raw is None:
            try:
                with _CONFIG_PATH.open(encoding="utf-8") as f:
                    self._raw = json.load(f)
            except FileNotFoundError as e:
                raise CredentialRegistryError(f"credential_providers.json not found at {_CONFIG_PATH}") from e
            except json.JSONDecodeError as e:
                raise CredentialRegistryError(f"credential_providers.json is not valid JSON: {e}") from e
        return self._raw

    def _resolve_all(self) -> Dict[str, Dict[str, Any]]:
        """Resolve every provider's `extends` chain into flat dicts."""
        if self._resolved_providers is not None:
            return self._resolved_providers

        raw = self._load_raw().get("providers", {})
        if not isinstance(raw, dict):
            raise CredentialRegistryError("providers must be a dict")

        resolved: Dict[str, Dict[str, Any]] = {}
        for provider_id in raw:
            resolved[provider_id] = self._resolve_one(provider_id, raw, visiting=set())

        self._resolved_providers = resolved
        return resolved

    def _resolve_one(
        self,
        provider_id: str,
        raw: Dict[str, Dict[str, Any]],
        visiting: Set[str],
    ) -> Dict[str, Any]:
        """Walk the extends chain, deep-merge parent then child overrides."""
        if provider_id in visiting:
            raise CredentialRegistryError(
                f"extends cycle detected involving provider {provider_id!r}: " f"{' -> '.join(sorted(visiting))} -> {provider_id}"
            )
        if provider_id not in raw:
            raise CredentialRegistryError(f"provider {provider_id!r} referenced via extends but not defined")

        entry = raw[provider_id]
        extends = entry.get("extends")
        if not extends:
            merged = copy.deepcopy(entry)
            merged["id"] = provider_id
            return merged

        # Resolve parent first (recursive), then overlay child.
        parent_visiting = visiting | {provider_id}
        parent = self._resolve_one(extends, raw, parent_visiting)

        merged = _deep_merge(parent, entry)
        merged["id"] = provider_id
        # `extends` itself is an implementation detail; strip from output.
        merged.pop("extends", None)
        # `_abstract` must not be inherited — only concrete entries that set
        # it explicitly should be treated as abstract. Otherwise every child
        # of `_ai_base` would silently become abstract and vanish from
        # get_all_providers().
        if "_abstract" not in entry:
            merged.pop("_abstract", None)
        return merged

    # ----- public API -----

    def get_all_providers(self) -> List[Dict[str, Any]]:
        """Return all concrete (non-abstract) providers in JSON order."""
        resolved = self._resolve_all()
        # Preserve insertion order of the raw providers dict.
        raw = self._load_raw().get("providers", {})
        out: List[Dict[str, Any]] = []
        for provider_id in raw:
            entry = resolved[provider_id]
            if entry.get("_abstract"):
                continue
            out.append(entry)
        return out

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        """Return a single resolved provider by id, or None if abstract/missing."""
        resolved = self._resolve_all()
        entry = resolved.get(provider_id)
        if entry is None or entry.get("_abstract"):
            return None
        return entry

    def get_categories(self) -> List[Dict[str, Any]]:
        """Return ordered category metadata derived from the raw file."""
        raw = self._load_raw()
        categories = raw.get("categories", {})
        if not isinstance(categories, dict):
            return []
        out = [{"key": key, "label": cfg.get("label", key), "order": cfg.get("order", 0)} for key, cfg in categories.items()]
        out.sort(key=lambda c: (c["order"], c["key"]))
        return out

    def get_consumer_categories(self) -> List[Dict[str, Any]]:
        """Ordered Normal-mode Connectors categories, apps first and AI last.
        Every provider declares one (locked by
        tests/test_credential_catalogue_consumer_fields.py), so the
        Connectors page lists the whole catalogue."""
        categories = self._load_raw().get("consumer_categories", {})
        if not isinstance(categories, dict):
            return []
        out = [{"key": key, "label": cfg.get("label", key), "order": cfg.get("order", 0)} for key, cfg in categories.items()]
        out.sort(key=lambda c: (c["order"], c["key"]))
        return out

    def get_version(self) -> str:
        """Content-sha256 of the resolved catalogue (providers + categories +
        mutation counter).

        Used by clients for warm-start cache invalidation: when the hash
        changes, the client fetches a fresh catalogue; otherwise it serves
        from IndexedDB with zero network traffic.

        The mutation counter is bumped by ``invalidate_version()`` on every
        credential save / delete / oauth-disconnect — this is what makes
        the conditional fetch ("`since: <prior version>`") actually return
        fresh data after a mutation. Without it the hash would only depend
        on the static catalogue JSON, the version would be constant for
        the life of the process, and the conditional fetch would always
        return ``{unchanged: true}`` even after a key was deleted — leaving
        the per-provider ``stored`` flag stuck at ``true`` on every client.
        """
        if self._version is None:
            payload = {
                "providers": self.get_all_providers(),
                "categories": self.get_categories(),
                "consumer_categories": self.get_consumer_categories(),
                "mutation_seq": self._mutation_seq,
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._version = hashlib.sha256(encoded).hexdigest()
        return self._version

    def get_live_version(self) -> str:
        """``get_version()`` plus the live state behind status-backed
        ``connected_check``s (WhatsApp pairing), which changes with no
        credential mutation. This is the version the catalogue handler
        serves and compares ``since`` against, so a revalidating client
        refetches when a pairing comes or goes."""
        base = self.get_version()
        live = _status_fingerprint(self.get_all_providers())
        return f"{base}+{live}" if live else base

    def invalidate_version(self) -> None:
        """Bump the mutation counter so the next ``get_version()`` returns a
        new hash. Call this from every credential mutation path (store /
        remove / oauth-disconnect) so the frontend's conditional fetch
        actually returns fresh ``stored`` flags. Cheap — just a counter
        increment + clearing the cached hash.
        """
        self._mutation_seq += 1
        self._version = None

    def get_catalogue(self) -> Dict[str, Any]:
        """Full payload returned by the `get_credential_catalogue` WebSocket handler."""
        return {
            "providers": self.get_all_providers(),
            "categories": self.get_categories(),
            "consumer_categories": self.get_consumer_categories(),
            "version": self.get_version(),
        }

    def reload(self) -> None:
        """Drop all caches; next call re-parses the JSON file."""
        self._raw = None
        self._resolved_providers = None
        self._version = None


# ----- deep-merge helper (n8n mergeNodeProperties pattern) -----


def _deep_merge(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict: parent deep-merged with child overrides.

    Rules:
      - Nested dicts merge recursively.
      - Arrays named in _MERGE_BY_KEY_ARRAYS merge by the `key` field: child
        entries replace parent entries with the same key; unmatched child
        entries append to the parent list.
      - All other arrays and scalars are replaced wholesale by the child.
      - The child's `extends` key is ignored (resolved at a higher level).
    """
    result: Dict[str, Any] = copy.deepcopy(parent)
    for key, child_value in child.items():
        if key == "extends":
            continue
        parent_value = result.get(key)
        if isinstance(parent_value, dict) and isinstance(child_value, dict):
            result[key] = _deep_merge(parent_value, child_value)
        elif key in _MERGE_BY_KEY_ARRAYS and isinstance(parent_value, list) and isinstance(child_value, list):
            result[key] = _merge_array_by_key(parent_value, child_value)
        else:
            result[key] = copy.deepcopy(child_value)
    return result


def _merge_array_by_key(parent: List[Any], child: List[Any]) -> List[Any]:
    """Merge two lists of dicts by their `key` field.

    Entries in the child that share a `key` with a parent entry recursively
    deep-merge (child wins on scalar conflicts). Unmatched child entries
    append to the end. Entries without a `key` field on either side are
    treated as identity replacements (parent dropped, child appended).
    """
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def _item_key(item: Any, prefix: str, index: int) -> str:
        if isinstance(item, dict) and "key" in item:
            return str(item["key"])
        return f"{prefix}:{index}"

    for i, item in enumerate(parent):
        k = _item_key(item, "parent", i)
        by_key[k] = copy.deepcopy(item) if isinstance(item, dict) else item
        order.append(k)

    for i, item in enumerate(child):
        if isinstance(item, dict) and "key" in item:
            k = str(item["key"])
            existing = by_key.get(k)
            if isinstance(existing, dict):
                by_key[k] = _deep_merge(existing, item)
            else:
                by_key[k] = copy.deepcopy(item)
                if k not in order:
                    order.append(k)
        else:
            k = f"child:{i}"
            by_key[k] = copy.deepcopy(item) if isinstance(item, dict) else item
            order.append(k)

    return [by_key[k] for k in order]


# ----- module-level singleton accessor -----


def get_credential_registry() -> CredentialRegistry:
    """Return the process-wide CredentialRegistry singleton."""
    return CredentialRegistry.get_instance()


# ----- live connection state -----


def _status_slot_on(check: Dict[str, Any]) -> bool:
    from services.status_broadcaster import get_status_broadcaster

    slot = get_status_broadcaster().get_status().get(str(check.get("key", "")))
    return isinstance(slot, dict) and bool(slot.get(str(check.get("field", "connected"))))


def _status_fingerprint(providers: List[Dict[str, Any]]) -> str:
    """``id=0|1`` for every provider whose ``connected_check`` reads a live
    status slot; empty when there are none."""
    parts = []
    for provider in providers:
        check = provider.get("connected_check") or {}
        if check.get("type") == "status":
            parts.append(f"{provider.get('id', '')}={int(_status_slot_on(check))}")
    return ",".join(sorted(parts))


async def _connected_check(check: Dict[str, Any], auth_service: Any) -> bool:
    """Evaluate a provider's declarative ``connected_check``.

    ``{"type": "status", "key": K, "field": F}``: the live status slot the
    provider's plugin keeps on the status broadcaster (WhatsApp's pairing).
    ``{"type": "api_keys", "keys": [...]}``: every listed key is stored
    (the IMAP/SMTP account, which is several keys and no OAuth token).
    ``{"type": "builtin"}``: nothing to connect, always usable (the web
    browser, whose panel manages optional login profiles).
    """
    kind = check.get("type")
    if kind == "builtin":
        return True
    if kind == "status":
        return _status_slot_on(check)
    if kind == "api_keys":
        keys = check.get("keys") or []
        if not keys:
            return False
        for key in keys:
            if not await auth_service.has_valid_key(str(key)):
                return False
        return True
    logger.warning("Unknown connected_check type in credential_providers.json", check_type=kind)
    return False


async def provider_connection_state(provider: Dict[str, Any], auth_service: Any) -> Dict[str, Any]:
    """Live credential state for one resolved catalogue provider.

    Returns ``{"stored", "connected", "account_label"}`` plus whatever the
    plugin's Credential class adds through ``catalogue_extras`` (which may
    replace ``stored``, e.g. several named OpenAI-compatible endpoints).

    ``stored``: a key or token is saved. ``connected``: the provider is
    usable right now. They are the same unless the provider declares a
    ``connected_check``; a provider with no stored credential of its own
    (the IMAP/SMTP account) takes ``stored`` from that check too.

    Shared by the ``get_credential_catalogue`` handler and the Normal-mode
    employee summaries, so both answer "is this app connected" the same way.
    """
    from services.plugin.credential import CREDENTIAL_REGISTRY

    pid = provider.get("id", "")
    kind = provider.get("kind", "")
    status_hook = provider.get("status_hook")
    check = provider.get("connected_check")

    state: Dict[str, Any] = {}
    tokens = None
    checked: Dict[str, bool] = {}

    async def check_passes() -> bool:
        if "result" not in checked:
            checked["result"] = await _connected_check(check, auth_service)
        return checked["result"]

    # Declarative per-provider override for the "stored" check. Lets Telegram
    # (kind=oauth + status_hook, but the bot token is stored as an api key)
    # report key presence instead of the default get_oauth_tokens(status_hook)
    # lookup. Providers without it keep the kind/status_hook logic, so
    # Google's saved client secret does NOT read as connected before the
    # OAuth flow completes.
    stored_check = provider.get("stored_check")
    if stored_check and stored_check.get("type") == "api_key":
        state["stored"] = await auth_service.has_valid_key(stored_check.get("key", pid))
    elif status_hook:
        # Status-hook providers (whatsapp, android, twitter, google,
        # claude_code, codex_cli) use OAuth tokens for the connection state.
        tokens = await auth_service.get_oauth_tokens(status_hook)
        state["stored"] = tokens is not None
    elif kind == "apiKey":
        state["stored"] = await auth_service.has_valid_key(pid)
    elif kind == "oauth":
        tokens = await auth_service.get_oauth_tokens(pid)
        state["stored"] = tokens is not None
    elif check:
        state["stored"] = await check_passes()
    else:
        state["stored"] = False

    # "Connected as foo@bar.com" without a per-provider status hook: Twitter,
    # Google, Stripe and Claude all populate email / name via
    # auth_service.store_oauth_tokens.
    state["account_label"] = (tokens.get("email") or tokens.get("name")) if tokens else None

    cred_cls = CREDENTIAL_REGISTRY.get(pid)
    if cred_cls is not None:
        extras = await cred_cls.catalogue_extras()
        if extras:
            state.update(extras)

    state["connected"] = await check_passes() if check else bool(state.get("stored"))
    return state
