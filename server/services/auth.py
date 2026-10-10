"""API key management service with encrypted credentials database.

Source of truth: ``CredentialsDatabase`` (encrypted SQLite at credentials.db).
Two derived in-memory caches exist for performance — both keyed by composite
strings, both invalidated atomically on every DB write/delete:

- ``_api_key_cache``: ``Dict[str, ApiKeyCacheEntry]`` keyed by ``{session}_{provider}``.
  One entry carries decrypted key + models + stored_at. Replaces the previous
  pair of ``_memory_cache`` (key) + ``_models_cache`` (models) which shared the
  same key shape but had separate write/evict sites — invitation to drift.
- ``_oauth_cache``: ``Dict[str, Dict]`` keyed by ``{customer}_{provider}``.
  Different namespace, different shape, different lifecycle — kept separate.
  Per RFC 9700 (OAuth 2.0 BCP 2024) refresh tokens are NOT cached here;
  ``get_oauth_refresh_token()`` reads from the DB on every call.

Async-safety: every read / write is await-free across the cache check, so
the GIL guarantees atomicity at the bytecode level — no locks needed. Lazy
DB fallback inside ``get_api_key`` introduces an await, but the only race
is "two concurrent reads both miss cache and both fetch from DB" — benign,
since both writes set the same value.

Neither cache has a TTL today; eviction is on explicit ``remove_*`` or
``clear_cache()`` (logout). External revocation by an upstream provider is
not detected.
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.config import Settings
from core.database import Database
from core.cache import CacheService
from core.credentials_database import CredentialsDatabase
from core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ApiKeyCacheEntry:
    """One in-memory cache entry per ``{session}_{provider}``.

    Carries the decrypted API key, the models list discovered at validation
    time, and a monotonic timestamp for future TTL support. Single struct
    so the two values can never drift — one write site, one evict site.
    """

    key: str
    models: List[str] = field(default_factory=list)
    stored_at: float = field(default_factory=time.monotonic)


class AuthService:
    """API key management service using encrypted credentials database.

    Uses CredentialsDatabase for secure storage with Fernet encryption.
    Decrypted keys are cached in memory only (not Redis) for security.
    """

    def __init__(self, credentials_db: CredentialsDatabase, cache: CacheService, database: Database, settings: Settings):
        self.credentials_db = credentials_db
        self.cache = cache  # Kept for backward compatibility, not used for API keys
        self.database = database  # Kept for backward compatibility
        self.settings = settings
        # Memory-only cache for decrypted API keys + models (never persisted
        # to Redis/disk). Single entry per provider; one write site, one
        # evict site — see module docstring.
        self._api_key_cache: Dict[str, ApiKeyCacheEntry] = {}
        # Memory-only cache for OAuth tokens. Different key namespace
        # (``{customer}_{provider}``); kept separate from API keys.
        # Per RFC 9700 the refresh_token is NOT cached here — only
        # access_token + display fields. Refresh-token access goes via
        # ``get_oauth_refresh_token()`` which reads from the DB.
        self._oauth_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def distributed_credentials(self) -> bool:
        return bool(getattr(self.settings, "distributed_mode", False))

    def _sources(self):
        from services.credentials.sources import CredentialSources
        return CredentialSources(self.database)

    def require_local_credentials(self) -> None:
        if self.distributed_credentials:
            from services.credentials.onepassword import CredentialSourceError
            raise CredentialSourceError("unsupported_source", "Distributed execution requires an approved 1Password binding. Local, inline, ambient and OAuth credentials are unsupported.")

    async def get_credential_source(self, provider: str, session_id: str = "default", *, principal: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Saved metadata only. This method never reads 1Password values."""
        from services.credentials.onepassword import CredentialSourceError
        row = await self._sources().get(provider, session_id)
        if row is None:
            return None
        if principal is not None and row.owner_id != principal:
            raise CredentialSourceError("access_denied", "Credential binding access denied.")
        return {"source": "onepassword", "reference": row.reference, "models": list(row.models), "model_params": dict(row.model_params), "base_url": row.base_url}

    async def store_credential_source(self, provider: str, reference: str, *, principal: str, session_id: str = "default", models: Optional[List[str]] = None, model_params: Optional[Dict] = None, base_url: Optional[str] = None) -> bool:
        await self._sources().save(provider, reference, owner_id=principal, session_id=session_id, models=models, model_params=model_params, base_url=base_url)
        self._api_key_cache.pop(f"{session_id}_{provider}", None)
        self._bump_catalogue_version()
        return True

    async def credential_source_version(self, principal: str = "owner") -> str:
        """Cross-replica catalogue invalidation without resolving a credential."""
        import json
        rows = await self._sources().list()
        metadata = sorted((row.id, row.updated_at.isoformat()) for row in rows if row.owner_id == principal)
        return hashlib.sha256(json.dumps([principal, metadata], separators=(",", ":")).encode()).hexdigest()[:20]

    async def resolve_api_key(self, provider: str, session_id: str = "default", *, principal: Optional[str] = None) -> Optional[str]:
        """Runtime-only resolution. No application cache or source fallback."""
        from services.credentials.onepassword import CredentialSourceError, read_secret
        # Legacy trusted callers operate as the single-user owner. Omitting a
        # principal must never grant access to another user's source binding.
        principal = principal or "owner"
        source = await self.get_credential_source(provider, session_id, principal=principal)
        if source is not None:
            value = await read_secret(source["reference"], self.settings)
            current = await self.get_credential_source(provider, session_id, principal=principal)
            if current != source:
                raise CredentialSourceError("binding_changed", "The API credential binding changed during authorization. Retry using its current configuration.")
            return value
        # Endpoint URLs are public central metadata, never local secret rows.
        if provider.endswith("_proxy"):
            endpoint = await self.get_credential_source(provider[:-6], session_id, principal=principal)
            if endpoint is not None:
                if provider.startswith("openai_compatible:") and not endpoint.get("base_url"):
                    from services.credentials.onepassword import CredentialSourceError
                    raise CredentialSourceError("missing_endpoint", "The named endpoint has no approved base URL. Enroll it with a static 1Password credential.")
                return endpoint.get("base_url")
        self.require_local_credentials()
        return await self.get_api_key(provider, session_id)

    async def get_browser_credential_binding(self, binding_id: str, principal: str, *, profile_id: Optional[str] = None, workflow_id: Optional[str] = None, employee_id: Optional[str] = None) -> Dict:
        from services.credentials.sources import binding_metadata
        row = await self._sources().browser_binding(binding_id, principal, profile_id=profile_id, workflow_id=workflow_id, employee_id=employee_id)
        return binding_metadata(row)

    async def resolve_browser_credentials(self, binding_id: str, principal: str, *, profile_id: Optional[str] = None, workflow_id: Optional[str] = None, employee_id: Optional[str] = None) -> Dict:
        """Private browser-owner caller only, after its observation gate closes."""
        from services.credentials.onepassword import read_secret
        from services.credentials.sources import binding_metadata
        row = await self._sources().browser_binding(binding_id, principal, profile_id=profile_id, workflow_id=workflow_id, employee_id=employee_id)
        username = await read_secret(row.username_reference, self.settings)
        password = await read_secret(row.password_reference, self.settings)
        # Recheck enrollment after desktop authorization waits/edits/revocation.
        current = await self._sources().browser_binding(binding_id, principal, profile_id=profile_id, workflow_id=workflow_id, employee_id=employee_id)
        if current.model_dump() != row.model_dump():
            from services.credentials.onepassword import CredentialSourceError
            raise CredentialSourceError("binding_changed", "The login binding changed during authorization. Observe the page and retry.")
        return {**binding_metadata(row), "username": username, "password": password}

    def hash_api_key(self, api_key: str) -> str:
        """Create hash for API key identification."""
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]

    def _bump_catalogue_version(self) -> None:
        """Notify the credential registry that a credential has changed.

        Bumps the registry's mutation counter so the next call to
        ``CredentialRegistry.get_version()`` returns a new content hash.
        The frontend's conditional ``since: <prior version>`` fetch then
        receives a fresh catalogue with updated ``stored`` flags
        instead of ``{unchanged: true}``. Without this bump, the version
        is constant for the life of the process and the per-provider
        ``stored`` flag stays stale on every connected client until the
        process restarts.

        Local import to avoid an import cycle at module load time.
        Failures are swallowed (logged at WARNING) so a missing registry
        never blocks a credential mutation from completing.
        """
        try:
            from services.credential_registry import get_credential_registry

            get_credential_registry().invalidate_version()
        except Exception as e:  # noqa: BLE001 — best-effort signal
            logger.warning("Failed to bump credential catalogue version: %s", e)

    async def store_api_key(
        self,
        provider: str,
        api_key: str,
        models: List[str],
        session_id: str = "default",
        model_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        """Store API key with models in encrypted credentials database.

        Args:
            provider: API provider name (e.g., 'openai', 'anthropic')
            api_key: The API key to store (will be encrypted)
            models: List of available models for this key
            session_id: Session identifier for multi-user support
            model_params: Optional per-model parameters (context_length etc.)
                — used by local providers (Ollama, LM Studio) where the
                context window depends on what the user has loaded.
                Forwarded straight to the credentials DB so
                ``model_registry`` can read real values at runtime.

        Returns:
            True if stored successfully, False otherwise
        """
        self.require_local_credentials()
        if await self.get_credential_source(provider, session_id):
            from services.credentials.onepassword import CredentialSourceError
            raise CredentialSourceError("binding_exists", "Remove the 1Password binding before replacing it with a local key.")
        try:
            cache_key = f"{session_id}_{provider}"

            logger.info(f"Storing API key for provider: {provider}, session: {session_id}")

            # 1. DB write first (canonical source).
            await self.credentials_db.save_api_key(
                provider=provider,
                api_key=api_key,
                models=models,
                session_id=session_id,
                model_params=model_params,
            )

            # 2. Cache update only after DB write succeeds. One entry,
            #    not two parallel dicts — see ApiKeyCacheEntry docstring.
            self._api_key_cache[cache_key] = ApiKeyCacheEntry(
                key=api_key,
                models=list(models),
            )

            # 3. Bump the catalogue version so the frontend's conditional
            #    fetch (``since: <prior version>``) returns a fresh
            #    catalogue instead of ``{unchanged: true}`` — without
            #    this the per-provider ``stored`` flag stays stale on
            #    every connected client until the process restarts.
            self._bump_catalogue_version()

            logger.info(f"Stored and cached API key for {provider}")
            return True

        except Exception as e:
            logger.error("Failed to store API key", provider=provider, error=str(e))
            return False

    async def get_model_params(self, provider: str, session_id: str = "default", *, principal: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Return per-model params (context_length etc.) for the provider.

        Reads straight from the credentials DB — there's no in-memory
        cache for these because they're consulted at most once per
        chat-model execution and the DB read is cheap. Empty dict for
        cloud providers (whose per-model params live in
        ``model_registry.json``) and for any local provider that hasn't
        been validated yet.
        """
        row = await self._sources().get(provider, session_id)
        if row is not None:
            return dict(row.model_params) if row.owner_id == (principal or "owner") else {}
        if self.distributed_credentials:
            return {}
        try:
            return await self.credentials_db.get_api_key_model_params(provider, session_id)
        except Exception as e:
            logger.error("Failed to get model_params", provider=provider, error=str(e))
            return {}

    async def get_api_key(self, provider: str, session_id: str = "default") -> Optional[str]:
        """Get decrypted API key.

        Checks memory cache first, then falls back to encrypted database.

        Args:
            provider: API provider name
            session_id: Session identifier

        Returns:
            Decrypted API key or None if not found/expired
        """
        if await self.get_credential_source(provider, session_id) is not None:
            return None
        if self.distributed_credentials:
            return None
        try:
            cache_key = f"{session_id}_{provider}"

            # Check memory cache first (fastest, most secure).
            entry = self._api_key_cache.get(cache_key)
            if entry is not None:
                return entry.key

            # Fallback to encrypted database. The lazy fetch also pulls
            # the models list so we populate the cache entry fully — no
            # second roundtrip on the next get_stored_models() call.
            api_key = await self.credentials_db.get_api_key(provider, session_id)
            if api_key:
                models = await self.credentials_db.get_api_key_models(provider, session_id) or []
                self._api_key_cache[cache_key] = ApiKeyCacheEntry(
                    key=api_key,
                    models=models,
                )
                return api_key

            return None

        except Exception as e:
            logger.error("Failed to get API key", provider=provider, error=str(e))
            return None

    async def list_key_scopes(self, provider: str, *, principal: Optional[str] = None) -> List[str]:
        """List every session_id holding a key for one provider.

        Reads the database directly rather than the cache: the cache is
        populated lazily per lookup, so it only knows the scopes that
        happen to have been read already.

        Args:
            provider: API provider name

        Returns:
            Sorted list of session identifiers, empty on failure
        """
        try:
            shared = {row.session_id for row in await self._sources().list(provider=provider) if row.owner_id == (principal or "owner")}
            local = set() if self.distributed_credentials else set(await self.credentials_db.list_key_scopes(provider))
            return sorted(shared | local)
        except Exception as e:
            logger.error("Failed to list key scopes", provider=provider, error=str(e))
            return []

    async def list_api_key_providers(self, session_id: str = "default", *, principal: Optional[str] = None) -> List[str]:
        """List every provider name holding a key in one session.

        The other axis of :meth:`list_key_scopes`. Reads the database, not
        the lazily populated cache. Used to enumerate named OpenAI-compatible
        endpoints, whose provider names are ``openai_compatible:<slug>``.

        Args:
            session_id: Session identifier

        Returns:
            Provider names, empty on failure
        """
        try:
            shared = {row.provider for row in await self._sources().list(session_id=session_id) if row.owner_id == (principal or "owner")}
            local = set() if self.distributed_credentials else set(await self.credentials_db.list_api_keys(session_id))
            return sorted(shared | local)
        except Exception as e:
            logger.error("Failed to list API key providers", session_id=session_id, error=str(e))
            return []

    async def get_stored_models(self, provider: str, session_id: str = "default", *, principal: Optional[str] = None) -> List[str]:
        """Get stored models for provider.

        Args:
            provider: API provider name
            session_id: Session identifier

        Returns:
            List of model names or empty list
        """
        row = await self._sources().get(provider, session_id)
        if row is not None:
            return list(row.models) if row.owner_id == (principal or "owner") else []
        if self.distributed_credentials:
            return []
        try:
            cache_key = f"{session_id}_{provider}"

            # Check memory cache first
            entry = self._api_key_cache.get(cache_key)
            if entry is not None:
                return entry.models

            # Fallback to encrypted database. Pull the key alongside so
            # the cache entry is populated fully — symmetric with
            # get_api_key()'s lazy-populate path.
            models = await self.credentials_db.get_api_key_models(provider, session_id)
            if models:
                api_key = await self.credentials_db.get_api_key(provider, session_id)
                if api_key:
                    self._api_key_cache[cache_key] = ApiKeyCacheEntry(
                        key=api_key,
                        models=list(models),
                    )
                return models

            return []

        except Exception as e:
            logger.error("Failed to get stored models", provider=provider, error=str(e))
            return []

    async def remove_api_key(self, provider: str, session_id: str = "default", *, principal: Optional[str] = None) -> bool:
        """Remove API key from storage and cache.

        Args:
            provider: API provider name
            session_id: Session identifier

        Returns:
            True if removed successfully
        """
        if await self.get_credential_source(provider, session_id, principal=principal):
            # Explicit Disconnect must not uncover an older local credential
            # shadowed by the source. Delete it while the binding still shields
            # runtime reads; distributed mode never opens the local DB.
            if not self.distributed_credentials:
                await self.credentials_db.delete_api_key(provider, session_id)
            await self._sources().delete(provider, session_id, principal)
            self._api_key_cache.pop(f"{session_id}_{provider}", None)
            self._bump_catalogue_version()
            return True
        if self.distributed_credentials:
            return True
        try:
            cache_key = f"{session_id}_{provider}"

            # 1. DB delete first (canonical source).
            await self.credentials_db.delete_api_key(provider, session_id)

            # 2. Cache evict only after DB succeeds. One pop, not two.
            self._api_key_cache.pop(cache_key, None)

            # 3. Bump the catalogue version — same reason as store.
            self._bump_catalogue_version()

            logger.info(f"Removed API key for {provider}")
            return True

        except Exception as e:
            logger.error("Failed to remove API key", provider=provider, error=str(e))
            return False

    async def has_valid_key(self, provider: str, session_id: str = "default", *, principal: Optional[str] = None) -> bool:
        """Check if valid API key exists.

        Args:
            provider: API provider name
            session_id: Session identifier

        Returns:
            True if a valid key exists
        """
        row = await self._sources().get(provider, session_id)
        if row is not None:
            return row.owner_id == (principal or "owner")
        api_key = await self.get_api_key(provider, session_id)
        return api_key is not None

    def clear_cache(self) -> None:
        """Clear all memory caches.

        Should be called on user logout to ensure decrypted keys
        don't persist in memory longer than necessary.
        """
        self._api_key_cache.clear()
        self._oauth_cache.clear()
        logger.debug("Cleared all credential memory caches")

    # --- OAuth Token Methods ---

    async def store_oauth_tokens(
        self,
        provider: str,
        access_token: str,
        refresh_token: str,
        email: Optional[str] = None,
        name: Optional[str] = None,
        scopes: Optional[str] = None,
        customer_id: str = "owner",
        expiry: Optional[datetime] = None,
    ) -> bool:
        """Store OAuth tokens in encrypted credentials database.

        Args:
            provider: OAuth provider name (e.g., 'google', 'twitter')
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            email: User email or identifier
            name: User display name
            scopes: Comma-separated scopes
            customer_id: Customer identifier (default 'owner' for single-user)
            expiry: When the access token expires (UTC), when known

        Returns:
            True if stored successfully
        """
        self.require_local_credentials()
        try:
            cache_key = f"{customer_id}_{provider}"

            # 1. DB write first (canonical source).
            await self.credentials_db.save_oauth_tokens(
                provider=provider,
                access_token=access_token,
                refresh_token=refresh_token,
                email=email,
                name=name,
                expiry=expiry,
                scopes=scopes,
                customer_id=customer_id,
            )

            # 2. Cache only the access token + display fields. Refresh
            #    tokens are long-lived secrets per RFC 9700 (OAuth 2.0
            #    BCP 2024) and are NOT cached in memory — readers go
            #    through ``get_oauth_refresh_token()`` which hits the DB.
            self._oauth_cache[cache_key] = {
                "access_token": access_token,
                "email": email,
                "name": name,
                "scopes": scopes,
            }

            # 3. Bump the catalogue version so the frontend's conditional
            #    fetch returns fresh ``stored`` flags after this OAuth
            #    save (login).
            self._bump_catalogue_version()

            logger.info(f"Stored OAuth tokens for {provider}")
            return True

        except Exception as e:
            logger.error("Failed to store OAuth tokens", provider=provider, error=str(e))
            return False

    async def get_oauth_tokens(self, provider: str, customer_id: str = "owner") -> Optional[Dict[str, Any]]:
        """Get OAuth display tokens from cache or encrypted database.

        Returns ``{access_token, email, name, scopes}`` only — the
        refresh token is intentionally NOT included. Callers that need
        the refresh token (token-refresh + revoke flows) must call
        :meth:`get_oauth_refresh_token` explicitly. This split implements
        RFC 9700 (OAuth 2.0 BCP 2024) §5.1 — refresh tokens are
        long-lived secrets and must not live in process memory.

        Args:
            provider: OAuth provider name
            customer_id: Customer identifier

        Returns:
            Dict with ``access_token``, ``email``, ``name``, ``scopes`` or
            ``None`` if no tokens are stored.
        """
        self.require_local_credentials()
        try:
            cache_key = f"{customer_id}_{provider}"

            # Check memory cache first.
            if cache_key in self._oauth_cache:
                return self._oauth_cache[cache_key]

            # Fallback to encrypted database. Strip refresh_token before
            # caching so the in-memory copy is short-lived-display-only.
            tokens = await self.credentials_db.get_oauth_tokens(provider, customer_id)
            if tokens:
                display = {
                    "access_token": tokens.get("access_token"),
                    "email": tokens.get("email"),
                    "name": tokens.get("name"),
                    "scopes": tokens.get("scopes"),
                }
                self._oauth_cache[cache_key] = display
                return display

            return None

        except Exception as e:
            logger.error("Failed to get OAuth tokens", provider=provider, error=str(e))
            return None

    async def get_oauth_refresh_token(
        self,
        provider: str,
        customer_id: str = "owner",
    ) -> Optional[str]:
        """Read the OAuth refresh token directly from the encrypted DB.

        Per RFC 9700 (OAuth 2.0 BCP 2024) §5.1 the refresh token is a
        long-lived bearer secret and must not be cached in process
        memory. Every call decrypts from disk; this is acceptable
        because refresh tokens are accessed rarely (only at access-token
        renewal + on revoke / logout).

        Args:
            provider: OAuth provider name
            customer_id: Customer identifier

        Returns:
            The decrypted refresh token, or ``None`` if no tokens are
            stored for ``(provider, customer_id)``.
        """
        self.require_local_credentials()
        try:
            tokens = await self.credentials_db.get_oauth_tokens(provider, customer_id)
            if tokens:
                return tokens.get("refresh_token")
            return None
        except Exception as e:
            logger.error("Failed to get OAuth refresh token", provider=provider, error=str(e))
            return None

    async def get_stored_oauth_tokens(self, provider: str, customer_id: str = "owner") -> Optional[Dict[str, Any]]:
        """The tokens as stored, read from the encrypted DB every time:
        ``access_token``, ``refresh_token``, ``token_expiry`` and ``scopes``.

        For a caller that refreshes its own tokens (a custom connector's
        sign-in, nodes/mcp): another process may have refreshed them since
        this one last read them, so neither copy is cached here.
        """
        self.require_local_credentials()
        try:
            tokens = await self.credentials_db.get_oauth_tokens(provider, customer_id)
        except Exception as e:
            logger.error("Failed to read OAuth tokens", provider=provider, error=str(e))
            return None
        if not tokens:
            return None
        return {key: tokens.get(key) for key in ("access_token", "refresh_token", "token_expiry", "scopes")}

    async def refresh_oauth_tokens_with_breaker(
        self,
        provider: str,
        refresh_fn,
    ) -> Dict[str, Any]:
        """Run an OAuth refresh call under a circuit breaker.

        Phase 7.5c of the credentials-scaling plan. Protects downstream
        OAuth providers from cascading failures: after 3 consecutive
        refresh failures within 60 s for a given provider, the breaker
        opens and all subsequent calls short-circuit for 30 s with a
        clear error instead of piling on the upstream.

        Args:
            provider: Provider name (e.g. 'google', 'twitter') — used as
                the breaker scope so one provider failing does not trip
                the breaker for another.
            refresh_fn: Zero-arg callable returning the refresh result
                dict. Can be sync or async. Typical shape:
                    ``{"success": True, "access_token": "..."}``
                or
                    ``{"success": False, "error": "..."}``.

        Returns:
            The refresh_fn result on success, or a dict of
            ``{"success": False, "error": "...", "circuit_open": True,
               "retry_after_seconds": N}`` when the breaker is open.

        This method is opt-in: legacy call sites that call their OAuth
        helpers directly are unaffected. New/migrated call sites gain
        breaker protection by routing through here instead of invoking
        the refresh helper directly.
        """
        import asyncio
        from services.circuit_breaker import get_circuit_breaker, CircuitBreakerOpen

        breaker = get_circuit_breaker(
            f"{provider}_oauth_refresh",
            failure_threshold=3,
            failure_window=60.0,
            cooldown_seconds=30.0,
        )

        async def _call():
            if asyncio.iscoroutinefunction(refresh_fn):
                result = await refresh_fn()
            else:
                # Run sync callables in a thread to avoid blocking the
                # event loop when the refresh does a blocking HTTP call
                # (e.g. google-auth's Credentials.refresh()).
                result = await asyncio.to_thread(refresh_fn)

            # Treat dict-shaped {"success": False} as a breaker-relevant
            # failure too, not just raised exceptions. Most OAuth helpers
            # in this codebase return {"success": False, "error": "..."}
            # instead of raising.
            if isinstance(result, dict) and result.get("success") is False:
                raise RuntimeError(result.get("error") or "oauth refresh reported failure")
            return result

        try:
            return await breaker.run(_call)
        except CircuitBreakerOpen as e:
            logger.warning(
                "auth: OAuth refresh breaker open for %s (retry after %.0fs)",
                provider,
                e.retry_after_seconds,
            )
            return {
                "success": False,
                "error": str(e),
                "circuit_open": True,
                "retry_after_seconds": e.retry_after_seconds,
                "needs_reauth": False,
            }
        except Exception as e:  # noqa: BLE001 — surface non-breaker errors
            logger.error("auth: OAuth refresh failed for %s: %s", provider, e)
            return {
                "success": False,
                "error": str(e),
                "circuit_open": False,
                "needs_reauth": True,
            }

    async def remove_oauth_tokens(self, provider: str, customer_id: str = "owner") -> bool:
        """Remove OAuth tokens from storage and cache.

        Args:
            provider: OAuth provider name
            customer_id: Customer identifier

        Returns:
            True if removed successfully
        """
        try:
            cache_key = f"{customer_id}_{provider}"

            # 1. DB delete first (canonical source).
            await self.credentials_db.delete_oauth_tokens(provider, customer_id)

            # 2. Cache evict after DB succeeds.
            self._oauth_cache.pop(cache_key, None)

            # 3. Bump the catalogue version — same reason as store.
            self._bump_catalogue_version()

            logger.info(f"Removed OAuth tokens for {provider}")
            return True

        except Exception as e:
            logger.error("Failed to remove OAuth tokens", provider=provider, error=str(e))
            return False
