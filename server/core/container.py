"""Dependency injection container for the application."""


def _clog(msg):
    """Pre-logger import-boundary marker.

    Emits via ``print()``; the CLI wrapper prefixes the absolute
    timestamp, so this helper carries no inline elapsed-time anymore.
    Kept as a function rather than inlining ``print()`` so future
    routing changes (e.g. to a logger after init) hit one site.
    """
    print(f"           container: {msg}", flush=True)


from typing import Any

from dependency_injector import containers, providers

from core.config import Settings
from core.database import Database
from core.cache import CacheService
from core.encryption import EncryptionService
from core.credentials_database import CredentialsDatabase

_clog("core imports done")
from services.ai import AIService

_clog("AIService imported")
from services.workflow import WorkflowService

_clog("WorkflowService imported")
from services.auth import AuthService
from services.text import TextService
from services.user_auth import UserAuthService
from services.compaction import init_compaction_service

_clog("all service imports done")

from services.temporal import TemporalClientWrapper


def _create_temporal_client(server_address: str, namespace: str):
    """Factory function for temporal client."""
    return TemporalClientWrapper(server_address=server_address, namespace=namespace)


# Wave 12 C4 sub-piece C: plugin-owned service factories.
# ``MapsService`` (nodes/location) and ``AndroidService`` (nodes/android)
# used to be imported here at module scope so providers.Factory could
# reference the classes directly. That made the framework DI container
# know about specific plugins' implementation classes — direct
# violation of the "framework knows no plugin names" rule.
# Now the plugins self-register their service classes via
# ``services.plugin.service_factories.register_service_factory``;
# the container's provider declarations reference the helper functions
# below, which look up the registered factory at instantiation time
# (first ``container.foo()`` call). Plugin auto-discovery on startup
# populates the registry before any service is first resolved, so
# import-ordering is not a constraint.


def _build_registered_service(name: str, **kwargs: Any) -> Any:
    """Look up a plugin-registered service factory and instantiate it.

    Wrapped behind a thin function so the DI container can declare
    providers without importing the concrete service classes.
    """
    from services.plugin.service_factories import get_service_factory

    factory = get_service_factory(name)
    if factory is None:
        raise RuntimeError(
            f"DI container: service factory {name!r} is not registered. "
            "The owning plugin failed to import (or hasn't shipped a "
            "register_service_factory call from its __init__.py). Check "
            "the plugin folder's __init__.py for the registration."
        )
    return factory(**kwargs)


def _build_maps_service(auth_service, settings):
    return _build_registered_service("maps", auth_service=auth_service, settings=settings)


def _build_android_service():
    return _build_registered_service("android")


class Container(containers.DeclarativeContainer):
    """Application dependency injection container."""

    # Configuration
    config = providers.Configuration()

    # Settings
    settings = providers.Singleton(
        Settings,
    )

    # Database (needed by CacheService for SQLite fallback)
    database = providers.Singleton(Database, settings=settings)

    # Cache service (uses Redis when available, SQLite otherwise)
    cache = providers.Singleton(CacheService, settings=settings, database=database)

    # Encryption service for credentials (initialized on user login)
    encryption_service = providers.Singleton(EncryptionService)

    # Credentials database (separate encrypted database for API keys and OAuth tokens)
    credentials_database = providers.Singleton(
        CredentialsDatabase, db_path=settings.provided.credentials_db_resolved, encryption=encryption_service
    )

    # Temporal client
    temporal_client = providers.Singleton(
        _create_temporal_client,
        server_address=settings.provided.temporal_server_address,
        namespace=settings.provided.temporal_namespace,
    )

    # Services
    auth_service = providers.Singleton(AuthService, credentials_db=credentials_database, cache=cache, database=database, settings=settings)

    user_auth_service = providers.Factory(
        UserAuthService, database=database, settings=settings, encryption=encryption_service, credentials_db=credentials_database
    )

    ai_service = providers.Singleton(AIService, auth_service=auth_service, database=database, cache=cache, settings=settings)

    maps_service = providers.Factory(_build_maps_service, auth_service=auth_service, settings=settings)

    text_service = providers.Factory(TextService)

    android_service = providers.Factory(_build_android_service)

    compaction_service = providers.Singleton(init_compaction_service, database=database, settings=settings)

    workflow_service = providers.Singleton(
        WorkflowService,
        database=database,
        ai_service=ai_service,
        maps_service=maps_service,
        text_service=text_service,
        android_service=android_service,
        cache=cache,
        settings=settings,
    )


# Global container instance
container = Container()
