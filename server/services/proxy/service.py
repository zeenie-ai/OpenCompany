"""ProxyService facade - singleton service for proxy management.

Routes HTTP requests through residential proxy providers with smart rotation,
scoring, geo-targeting, sticky sessions, failover, and cost tracking.
Follows the CompactionService singleton pattern.
"""

import json
import time
from collections import deque
from fnmatch import fnmatch
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from core.logging import get_logger
from services.proxy.exceptions import (
    BudgetExceededError,
    NoHealthyProviderError,
    ProviderError,
)
from services.proxy.models import (
    GeoTarget,
    ProviderConfig,
    ProviderStats,
    ProxyResult,
    RoutingRule,
    SessionType,
)
from services.proxy.providers import TemplateProxyProvider

logger = get_logger(__name__)

# Scoring weights
_WEIGHT_SUCCESS = 0.4
_WEIGHT_LATENCY = 0.2
_WEIGHT_COST = 0.3
_WEIGHT_FRESHNESS = 0.1

# Max results to keep for scoring per provider
_MAX_HISTORY = 100


class _ProviderRuntime:
    """In-memory runtime state for a single provider."""

    def __init__(self, config: ProviderConfig, provider: TemplateProxyProvider):
        self.config = config
        self.provider = provider
        self.username: str = ""
        self.password: str = ""
        self.history: Deque[ProxyResult] = deque(maxlen=_MAX_HISTORY)
        self.total_bytes: int = 0
        self.total_requests: int = 0

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    def compute_score(self) -> float:
        """Compute composite health score (0.0 - 1.0)."""
        if not self.history:
            return 1.0  # No data = assume healthy

        results = list(self.history)

        # Success rate (0-1)
        successes = sum(1 for r in results if r.success)
        success_rate = successes / len(results)

        # Latency score (0-1, lower latency = higher score)
        latencies = [r.latency_ms for r in results if r.latency_ms > 0]
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            latency_score = max(0.0, 1.0 - (avg_latency / 10000))  # 10s = 0 score
        else:
            latency_score = 1.0

        # Cost score (0-1, lower cost = higher score)
        cost = self.config.cost_per_gb
        cost_score = max(0.0, 1.0 - (cost / 10.0))  # $10/GB = 0 score

        # Freshness score (0-1, more recent data = higher score)
        freshness_score = min(1.0, len(results) / 10)

        score = (
            _WEIGHT_SUCCESS * success_rate
            + _WEIGHT_LATENCY * latency_score
            + _WEIGHT_COST * cost_score
            + _WEIGHT_FRESHNESS * freshness_score
        )
        return round(max(0.0, min(1.0, score)), 4)

    def success_rate(self) -> float:
        if not self.history:
            return 1.0
        return sum(1 for r in self.history if r.success) / len(self.history)

    def avg_latency_ms(self) -> float:
        latencies = [r.latency_ms for r in self.history if r.latency_ms > 0]
        if not latencies:
            return 0.0
        return sum(latencies) / len(latencies)

    def is_healthy(self, min_success_rate: float = 0.3) -> bool:
        if not self.history:
            return True
        return self.success_rate() >= min_success_rate

    def to_stats(self) -> ProviderStats:
        return ProviderStats(
            name=self.config.name,
            score=self.compute_score(),
            success_rate=self.success_rate(),
            avg_latency_ms=self.avg_latency_ms(),
            total_requests=self.total_requests,
            total_bytes=self.total_bytes,
            healthy=self.is_healthy(),
        )


class ProxyService:
    """Singleton proxy management service.

    Loads provider configs from DB, reads credentials from AuthService,
    scores providers by health, matches domains to routing rules, and
    formats proxy URLs for httpx.
    """

    def __init__(self, auth_service, database, settings):
        self._auth_service = auth_service
        self._database = database
        self._settings = settings
        self._providers: Dict[str, _ProviderRuntime] = {}
        self._routing_rules: List[RoutingRule] = []
        self._daily_spend_usd: float = 0.0
        self._initialized = False

    async def startup(self) -> None:
        """Load provider configs from DB and credentials from AuthService.

        Always initializes -- providers are managed dynamically by the LLM
        via proxy_config tool. PROXY_ENABLED is not a gate.
        """
        try:
            # Load provider configs from database
            db_providers = await self._database.get_proxy_providers()
            await self._load_providers(db_providers)

            # Load routing rules from database
            await self._reload_routing_rules()

            self._initialized = True
            provider_names = [p.config.name for p in self._providers.values() if p.config.enabled]
            logger.info("Proxy service started", providers=provider_names, rules=len(self._routing_rules))

        except Exception as e:
            logger.error("Failed to start proxy service", error=str(e))
            self._initialized = True  # Mark as initialized to avoid retry loops

    async def shutdown(self) -> None:
        """Clean up resources."""
        self._providers.clear()
        self._routing_rules.clear()
        self._initialized = False
        logger.info("Proxy service stopped")

    def is_enabled(self) -> bool:
        return self._initialized

    async def get_proxy_url(
        self,
        url: str,
        parameters: Dict[str, Any],
    ) -> Optional[str]:
        """Get a proxy URL for the given target URL and node parameters.

        This is the main entry point. Returns None if proxy is not applicable.

        Args:
            url: Target URL to be proxied
            parameters: Node parameters (may contain proxyProvider, proxyCountry, etc.)

        Returns:
            Proxy URL string for httpx, or None if no proxy should be used
        """
        if not self.is_enabled():
            return None

        # Check daily budget
        budget = self._settings.proxy_budget_daily_usd
        if budget is not None and self._daily_spend_usd >= budget:
            raise BudgetExceededError(budget, self._daily_spend_usd)

        # Extract target domain
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or ""
        except Exception:
            domain = ""

        # Match routing rule
        rule = self._match_routing_rule(domain)

        # Determine provider
        explicit_provider = parameters.get("proxy_provider")
        if explicit_provider and explicit_provider in self._providers:
            runtime = self._providers[explicit_provider]
        elif rule and rule.preferred_providers:
            runtime = self._pick_from_preferred(rule.preferred_providers, rule.min_success_rate)
        else:
            runtime = self._pick_best_provider()

        if runtime is None:
            raise NoHealthyProviderError("No healthy proxy providers available")

        if not runtime.has_credentials:
            raise ProviderError(runtime.config.name, "No credentials configured")

        if not runtime.config.enabled:
            raise ProviderError(runtime.config.name, "Provider is disabled")

        # Build geo target. proxy_city / proxy_state / proxy_session_id
        # are not declared Pydantic fields today; reading them returns
        # None unless a caller hand-passes them via the same
        # snake_case convention as the rest of the schema.
        geo = GeoTarget(
            country=parameters.get("proxy_country") or (rule.required_country if rule else None) or self._settings.proxy_default_country,
            city=parameters.get("proxy_city"),
            state=parameters.get("proxy_state"),
        )

        # Determine session type
        session_type_str = parameters.get("session_type", "rotating")
        session_type = SessionType(session_type_str) if session_type_str else SessionType.ROTATING
        if rule:
            session_type = rule.session_type

        session_id = parameters.get("proxy_session_id")

        # Format proxy URL
        proxy_url = runtime.provider.format_proxy_url(
            username=runtime.username,
            password=runtime.password,
            host=runtime.config.gateway_host,
            port=runtime.config.gateway_port,
            geo=geo if (geo.country or geo.city or geo.state) else None,
            session_type=session_type,
            session_id=session_id,
        )

        logger.debug("Proxy URL generated", provider=runtime.config.name, domain=domain, country=geo.country)

        return proxy_url

    def report_result(self, provider_name: str, result: ProxyResult) -> None:
        """Report the result of a proxied request for health scoring.

        Args:
            provider_name: Name of the provider used
            result: Result of the request
        """
        runtime = self._providers.get(provider_name)
        if runtime is None:
            return

        runtime.history.append(result)
        runtime.total_requests += 1
        runtime.total_bytes += result.bytes_transferred

        # Track daily spend
        if result.bytes_transferred > 0 and runtime.config.cost_per_gb > 0:
            gb = result.bytes_transferred / (1024**3)
            self._daily_spend_usd += gb * runtime.config.cost_per_gb

    def get_providers(self) -> List[ProviderStats]:
        """Get health stats for all configured providers."""
        return [rt.to_stats() for rt in self._providers.values()]

    def get_routing_rules(self) -> List[RoutingRule]:
        """Get all routing rules sorted by priority."""
        return list(self._routing_rules)

    async def save_routing_rule(self, rule_data: Dict[str, Any]) -> bool:
        """Save a routing rule to database and refresh in-memory list."""
        success = await self._database.save_proxy_routing_rule(rule_data)
        if success:
            await self._reload_routing_rules()
        return success

    async def delete_routing_rule(self, rule_id: int) -> bool:
        """Delete a routing rule and refresh in-memory list."""
        success = await self._database.delete_proxy_routing_rule(rule_id)
        if success:
            await self._reload_routing_rules()
        return success

    async def test_provider(self, provider_name: str) -> Dict[str, Any]:
        """Health check a provider by making a request through it.

        Returns dict with success, ip, latency_ms, error.
        """
        runtime = self._providers.get(provider_name)
        if runtime is None:
            return {"success": False, "error": f"Provider '{provider_name}' not found"}

        if not runtime.has_credentials:
            return {"success": False, "error": "No credentials configured"}

        try:
            proxy_url = runtime.provider.format_proxy_url(
                username=runtime.username,
                password=runtime.password,
                host=runtime.config.gateway_host,
                port=runtime.config.gateway_port,
            )

            start = time.monotonic()
            async with httpx.AsyncClient(proxy=proxy_url, timeout=15.0) as client:
                resp = await client.get("https://httpbin.org/ip")
                latency_ms = (time.monotonic() - start) * 1000

            data = resp.json()
            result = ProxyResult(success=True, latency_ms=latency_ms, status_code=resp.status_code)
            self.report_result(provider_name, result)

            return {
                "success": True,
                "ip": data.get("origin", "unknown"),
                "latency_ms": round(latency_ms, 1),
                "status_code": resp.status_code,
            }

        except Exception as e:
            result = ProxyResult(success=False, error=str(e))
            self.report_result(provider_name, result)
            return {"success": False, "error": str(e)}

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregated proxy usage statistics."""
        return {
            "enabled": self.is_enabled(),
            "provider_count": len(self._providers),
            "active_providers": sum(1 for p in self._providers.values() if p.config.enabled and p.has_credentials),
            "routing_rules": len(self._routing_rules),
            "daily_spend_usd": round(self._daily_spend_usd, 4),
            "daily_budget_usd": self._settings.proxy_budget_daily_usd,
            "total_requests": sum(p.total_requests for p in self._providers.values()),
            "total_bytes": sum(p.total_bytes for p in self._providers.values()),
            "providers": {name: rt.to_stats().model_dump() for name, rt in self._providers.items()},
        }

    def reset_daily_spend(self) -> None:
        """Reset daily spend counter (called by scheduler at midnight)."""
        self._daily_spend_usd = 0.0

    async def reload_providers(self) -> None:
        """Reload provider configs from database (called after tool/UI changes)."""
        db_providers = await self._database.get_proxy_providers()
        await self._load_providers(db_providers)
        logger.info("Proxy providers reloaded", count=len(self._providers))

    async def _load_providers(self, db_providers: list) -> None:
        """Build _ProviderRuntime instances from database rows."""
        self._providers.clear()
        for row in db_providers:
            template = json.loads(row.get("url_template", "{}"))
            provider = TemplateProxyProvider(name=row["name"], template=template)

            config = ProviderConfig(
                name=row["name"],
                enabled=row.get("enabled", True),
                priority=row.get("priority", 50),
                weight=row.get("weight", 1.0),
                cost_per_gb=row.get("cost_per_gb", 0.0),
                gateway_host=row.get("gateway_host", ""),
                gateway_port=row.get("gateway_port", 0),
                geo_coverage=json.loads(row.get("geo_coverage", "[]")),
                sticky_support=row.get("sticky_support", True),
                max_sticky_seconds=row.get("max_sticky_seconds", 600),
                max_concurrent=row.get("max_concurrent", 100),
                url_template=template,
            )

            runtime = _ProviderRuntime(config, provider)

            # Load credentials from AuthService
            cred_key = f"proxy_{config.name}"
            username = await self._auth_service.get_api_key(f"{cred_key}_username")
            password = await self._auth_service.get_api_key(f"{cred_key}_password")
            if username and password:
                runtime.username = username
                runtime.password = password
            else:
                logger.warning("No credentials for proxy provider", name=config.name)

            self._providers[config.name] = runtime

    # ---- Internal helpers ----

    def _match_routing_rule(self, domain: str) -> Optional[RoutingRule]:
        """Find the first matching routing rule for a domain."""
        for rule in self._routing_rules:
            if fnmatch(domain, rule.domain_pattern):
                return rule
        return None

    def _pick_from_preferred(
        self,
        preferred: List[str],
        min_success_rate: float = 0.3,
    ) -> Optional[_ProviderRuntime]:
        """Pick the best provider from a preferred list."""
        candidates = []
        for name in preferred:
            rt = self._providers.get(name)
            if rt and rt.config.enabled and rt.has_credentials and rt.is_healthy(min_success_rate):
                candidates.append(rt)

        if not candidates:
            # Fallback to any healthy provider
            return self._pick_best_provider()

        # Sort by score descending
        candidates.sort(key=lambda r: r.compute_score(), reverse=True)
        return candidates[0]

    def _pick_best_provider(self) -> Optional[_ProviderRuntime]:
        """Pick the best healthy provider by composite score."""
        candidates = [rt for rt in self._providers.values() if rt.config.enabled and rt.has_credentials and rt.is_healthy()]

        if not candidates:
            return None

        # Sort by score descending, then priority ascending (lower = better)
        candidates.sort(key=lambda r: (-r.compute_score(), r.config.priority))
        return candidates[0]

    async def _reload_routing_rules(self) -> None:
        """Reload routing rules from database."""
        db_rules = await self._database.get_proxy_routing_rules()
        self._routing_rules = []
        for row in db_rules:
            self._routing_rules.append(
                RoutingRule(
                    id=row.get("id"),
                    domain_pattern=row["domain_pattern"],
                    preferred_providers=json.loads(row.get("preferred_providers", "[]")),
                    required_country=row.get("required_country"),
                    session_type=SessionType(row.get("session_type", "rotating")),
                    sticky_duration_seconds=row.get("sticky_duration_seconds", 300),
                    max_retries=row.get("max_retries", 3),
                    failover=row.get("failover", True),
                    min_success_rate=row.get("min_success_rate", 0.7),
                    priority=row.get("priority", 0),
                )
            )
        self._routing_rules.sort(key=lambda r: r.priority)


# ---- Singleton accessor (CompactionService pattern) ----

_proxy_service: Optional[ProxyService] = None


def init_proxy_service(auth_service, database, settings) -> ProxyService:
    """Create the singleton ProxyService instance."""
    global _proxy_service
    _proxy_service = ProxyService(auth_service, database, settings)
    return _proxy_service


def get_proxy_service() -> Optional[ProxyService]:
    """Get the singleton ProxyService instance, or None if not initialized."""
    return _proxy_service
