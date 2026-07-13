"""ProxyForge - Universal proxy management service for OpenCompany.

Routes HTTP requests through residential proxy providers with smart rotation,
scoring, geo-targeting, sticky sessions, failover, and cost tracking.
"""

from services.proxy.service import ProxyService, get_proxy_service

__all__ = ["ProxyService", "get_proxy_service"]
