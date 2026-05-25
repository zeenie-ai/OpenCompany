"""Pydantic v2 domain models for the proxy service."""

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SessionType(str, Enum):
    """Proxy session type controlling IP rotation behavior."""

    ROTATING = "rotating"  # New IP per request (default)
    STICKY = "sticky"  # Same IP for duration


class GeoTarget(BaseModel):
    """Geographic targeting for proxy requests."""

    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2 country code")
    city: Optional[str] = Field(default=None, description="City name")
    state: Optional[str] = Field(default=None, description="State/region name")


class ProviderConfig(BaseModel):
    """Configuration for a proxy provider loaded from database."""

    name: str
    enabled: bool = True
    priority: int = 50  # Lower = preferred
    weight: float = 1.0  # For weighted random selection
    cost_per_gb: float = 0.0  # USD per GB
    gateway_host: str = ""
    gateway_port: int = 0
    geo_coverage: List[str] = Field(default_factory=list)  # ISO country codes
    sticky_support: bool = True
    max_sticky_seconds: int = 600
    max_concurrent: int = 100
    url_template: Dict = Field(default_factory=dict)  # JSON template config


class ProviderStats(BaseModel):
    """Runtime health statistics for a proxy provider."""

    name: str
    score: float = 1.0  # 0.0 - 1.0 composite score
    success_rate: float = 1.0  # 0.0 - 1.0
    avg_latency_ms: float = 0.0
    total_requests: int = 0
    total_bytes: int = 0
    healthy: bool = True


class RoutingRule(BaseModel):
    """Domain-based routing rule for proxy selection."""

    id: Optional[int] = None
    domain_pattern: str  # fnmatch glob: "*.linkedin.com", "*"
    preferred_providers: List[str] = Field(default_factory=list)
    required_country: Optional[str] = None
    session_type: SessionType = SessionType.ROTATING
    sticky_duration_seconds: int = 300
    max_retries: int = 3
    failover: bool = True
    min_success_rate: float = 0.7
    priority: int = 0  # Lower = evaluated first


class ProxyResult(BaseModel):
    """Result of a proxied HTTP request, reported back for scoring."""

    success: bool
    latency_ms: float = 0.0
    bytes_transferred: int = 0
    status_code: Optional[int] = None
    error: Optional[str] = None
