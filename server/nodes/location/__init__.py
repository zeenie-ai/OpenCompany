"""Plugins for the 'location' palette group. See ../__init__.py for the package layout.

Self-registration on import:
  - Wave 12 C4 sub-piece C: ``MapsService`` is published as the
    ``"maps"`` service factory via
    ``services.plugin.service_factories.register_service_factory``.
    The DI container's ``maps_service`` provider looks up the factory
    at instantiation time, so the framework no longer carries a
    top-level ``from nodes.location._service import MapsService``.
  - Eager-import the three gmaps subpackages so their ``BaseNode``
    subclasses auto-register regardless of ``pkgutil.walk_packages``
    recursion behaviour. The conftest plugin-discovery path was
    occasionally skipping nested subpackages on CI Linux; this
    belt-and-braces approach makes the registration deterministic.
"""

from services.plugin.service_factories import register_service_factory

from ._service import MapsService

# Eager-import the three gmaps subpackages so their ``BaseNode``
# subclasses auto-register regardless of ``pkgutil.walk_packages``
# recursion quirks on CI Linux. The factory call below runs
# unconditionally after these — if any eager import raises, the
# exception propagates and ``nodes/__init__.py:_discover`` catches +
# logs it (and the factory test will catch the missing registration).
from . import gmaps_create as _gmaps_create  # noqa: F401 — registers GmapsCreateNode
from . import gmaps_locations as _gmaps_locations  # noqa: F401 — registers GmapsLocationsNode
from . import gmaps_nearby_places as _gmaps_nearby_places  # noqa: F401 — registers GmapsNearbyPlacesNode

register_service_factory("maps", MapsService)
