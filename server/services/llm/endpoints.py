"""Where an OpenAI-compatible API is rooted, decided once, when it is saved.

RFC-0003 §6. A user-supplied base URL is ambiguous: llama.cpp and a LiteLLM
proxy serve the API at the root, Ollama, LM Studio and vLLM only under
``/v1``, and a path-prefixed deployment can be either. No string rule
resolves that, so :func:`resolve_base_url` asks the server, and the answer
is persisted. Call time only ever reads the stored value.

Two things make the probe trustworthy:

- It is the call execution makes. ``AsyncOpenAI.models.list()`` is exactly
  what ``OpenAIProvider.fetch_models`` sends, through the same client and
  the same URL concatenation, so a URL that resolves here is a URL the
  runtime can use.
- A status code proves nothing on its own. LM Studio answers HTTP 200 to
  routes it does not serve, so a candidate counts only when the body is an
  OpenAI list (a ``data`` array). A status-only probe would accept a
  ``/v1``-less LM Studio URL, which is the failure RFC-0003 §2.1 records.

This is the one module allowed to append ``/v1`` (RFC-0003 AG1/AG2). The
openai SDK is imported inside the probe, so importing this module for its
pure helpers costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List, Optional
from urllib.parse import urlsplit, urlunsplit

from core.logging import get_logger
from services.llm.config import ENDPOINT_PROVIDER, split_provider_ref

if TYPE_CHECKING:
    from services.auth import AuthService

logger = get_logger(__name__)

#: Suffix of the credential row that holds a provider's base URL. The row
#: shape (``{provider}`` for the key, ``{provider}_proxy`` for the URL) is
#: shared by Ollama, LM Studio and every named endpoint.
BASE_URL_SUFFIX = "_proxy"

#: Reserved ``model_params`` entry on a saved server's key row, holding its
#: display metadata (label, redacted base URL, kind). Underscore-prefixed
#: like ``_default`` in llm_defaults.json: it is not a model.
SERVER_META_KEY = "_endpoint"

_OPENAI_PREFIX = "/v1"

#: Why a URL with no scheme or host is refused. Also why an endpoint with no
#: label cannot be named: its name comes from the URL's host.
FULL_URL_REQUIRED = "Enter the full base URL, including http:// or https:// (for example http://localhost:8080/v1)."


def base_url_key(provider_ref: str) -> str:
    """Credential-row key holding the resolved base URL of ``provider_ref``."""
    return f"{provider_ref}{BASE_URL_SUFFIX}"


def unconfigured_endpoint_message(provider_ref: str) -> Optional[str]:
    """What to tell the user when ``provider_ref`` names a named endpoint with no rows.

    ``None`` for every other provider. A named endpoint exists only as its
    credential rows, so a missing row means the endpoint was never saved or
    was removed, not that a key is missing. A bare ``openai_compatible``
    means no endpoint was chosen at all.
    """
    name, slug = split_provider_ref(provider_ref)
    if name != ENDPOINT_PROVIDER:
        return None
    if not slug:
        return "Choose an OpenAI-compatible endpoint. Save one under Credentials first if there is none."
    return f"The OpenAI-compatible endpoint '{slug}' is not configured. Add it under Credentials."


def redact_url(url: Any) -> str:
    """Render ``url`` for a log line or a user-facing message.

    Drops userinfo, the query string and the fragment, the parts of a URL
    that can carry a credential. Returns ``""`` for an empty value.
    """
    if not url:
        return ""
    parts = urlsplit(str(url))
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal: hostname drops the brackets
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:  # malformed port: render the host alone
        port = None
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True)
class ResolvedBaseUrl:
    """Outcome of rooting a user-supplied URL.

    ``base_url`` is ``None`` when nothing was adopted, and ``reason`` then
    says why in words safe to show the user. ``models`` carries the entries
    of the adopted ``/models`` page (SDK ``Model`` objects, whose extra
    fields such as vLLM's ``max_model_len`` survive in ``model_extra``).
    """

    base_url: Optional[str]
    rewritten: bool = False
    models: List[Any] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.base_url is not None


def _normalize_candidate(candidate: str) -> Optional[str]:
    """Trim the input and require an absolute http(s) URL.

    A scheme-less value such as ``localhost:8080`` is rejected rather than
    guessed at: the user sees what to type instead of a URL we invented.
    """
    value = (candidate or "").strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return value


async def resolve_base_url(candidate: str, *, api_key: str, timeout: float = 10.0) -> ResolvedBaseUrl:
    """Find the prefix at which ``candidate`` serves the OpenAI API.

    Tries ``candidate``, then ``candidate + "/v1"`` unless the input already
    ends there. Per candidate:

    - the ``/models`` page is an OpenAI list: adopt it;
    - 401/403: a server is there but refused the key; try the next one;
    - a connection-level failure: stop, the next candidate is the same host;
    - anything else (404, a 200 whose body is not a list): try the next one.

    ``api_key`` must already be resolved (``resolve_credential``): the SDK
    reads ``OPENAI_API_KEY`` when handed ``None``.
    """
    import openai

    base = _normalize_candidate(candidate)
    if base is None:
        return ResolvedBaseUrl(None, reason=FULL_URL_REQUIRED)

    candidates = [base] if base.endswith(_OPENAI_PREFIX) else [base, base + _OPENAI_PREFIX]
    key_rejected_at: Optional[str] = None

    for url in candidates:
        try:
            async with openai.AsyncOpenAI(base_url=url, api_key=api_key, max_retries=0, timeout=timeout) as client:
                page = await client.models.list()
        except (openai.AuthenticationError, openai.PermissionDeniedError):
            key_rejected_at = key_rejected_at or url
            continue
        except openai.APIConnectionError as exc:
            # APITimeoutError subclasses APIConnectionError. Either way the
            # host did not answer, and the other candidate is the same host.
            logger.info("endpoint unreachable", url=redact_url(url), error=type(exc).__name__)
            return ResolvedBaseUrl(None, reason=f"Could not reach {redact_url(base)}. Is the server running?")
        except Exception as exc:  # noqa: BLE001 — any other answer means "not rooted here"
            logger.debug("no OpenAI models list at candidate", url=redact_url(url), error=type(exc).__name__)
            continue

        entries = getattr(page, "data", None)
        if not isinstance(entries, list):
            logger.debug("candidate answered without an OpenAI list", url=redact_url(url))
            continue

        rewritten = url != base
        if rewritten:
            logger.info("endpoint base URL rewritten", entered=redact_url(base), resolved=redact_url(url))
        return ResolvedBaseUrl(url, rewritten=rewritten, models=list(entries))

    if key_rejected_at is not None:
        return ResolvedBaseUrl(
            None,
            reason=f"Found an OpenAI-compatible server at {redact_url(key_rejected_at)}, but it rejected the API key.",
        )
    tried = " or ".join(redact_url(url) for url in candidates)
    return ResolvedBaseUrl(
        None,
        reason=f"No OpenAI-compatible API answered at {tried}. Check the base URL (for example http://localhost:8080/v1).",
    )


async def list_endpoint_refs(auth: "AuthService", *, principal: Optional[str] = None) -> List[str]:
    """Every saved named endpoint, as provider references, sorted.

    Enumerated from the credential rows themselves rather than a separate
    index, so there is nothing that can disagree with them.
    """
    prefix = f"{ENDPOINT_PROVIDER}:"
    return sorted(
        provider
        for provider in await auth.list_api_key_providers(**({"principal": principal} if principal is not None else {}))
        if provider.startswith(prefix) and not provider.endswith(BASE_URL_SUFFIX)
    )


@dataclass(frozen=True)
class SavedEndpoint:
    """A saved named endpoint, as the credentials panel and dropdowns show it.

    ``base_url`` is the redacted form stored for display; the full URL
    stays in the encrypted ``{ref}_proxy`` row.
    """

    ref: str
    label: str
    base_url: str
    kind: str
    models: List[str]


async def list_endpoints(auth: "AuthService", *, principal: Optional[str] = None) -> List[SavedEndpoint]:
    """Every saved named endpoint with its display metadata and models."""
    endpoints: List[SavedEndpoint] = []
    scope = {"principal": principal} if principal is not None else {}
    for ref in await list_endpoint_refs(auth, **scope):
        meta = (await auth.get_model_params(ref, **scope)).get(SERVER_META_KEY) or {}
        endpoints.append(
            SavedEndpoint(
                ref=ref,
                label=meta.get("label") or split_provider_ref(ref)[1],
                base_url=meta.get("base_url", ""),
                kind=meta.get("kind", "generic"),
                models=list(await auth.get_stored_models(ref, **scope)),
            )
        )
    return endpoints


__all__ = [
    "BASE_URL_SUFFIX",
    "FULL_URL_REQUIRED",
    "SERVER_META_KEY",
    "ResolvedBaseUrl",
    "SavedEndpoint",
    "base_url_key",
    "list_endpoint_refs",
    "list_endpoints",
    "redact_url",
    "resolve_base_url",
    "unconfigured_endpoint_message",
]
