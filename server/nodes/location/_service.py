"""Google Maps service for location operations.

Wave 11.I, milestone N: relocated from ``services/maps.py`` into the
location plugin folder. Three nodes consume it through the DI container
(``container.maps_service()``): ``gmaps_create``, ``gmaps_locations``,
``gmaps_nearby_places``. The container provider in ``core/container.py``
imports ``MapsService`` from this module.

API-key validation lives in :mod:`._credentials`'s
:class:`GoogleMapsCredential._probe` (Wave 11.I scaffold) -- the legacy
REST endpoint at ``/python/maps/validate-key`` was retired with this
move. Workflow nodes still call ``MapsService.create_map`` /
``geocode_location`` / ``find_nearby_places`` directly via the DI
container; the dead ``/python/<name>/execute`` REST endpoints (also
retired) duplicated the same paths.
"""

import time
import googlemaps
from datetime import datetime
from typing import Dict, Any

from core.config import Settings
from core.logging import get_logger, log_execution_time
from services.auth import AuthService
from services.pricing import get_pricing_service

logger = get_logger(__name__)


async def _track_maps_usage(
    node_id: str,
    action: str,
    resource_count: int = 1,
    workflow_id: str = None,
    session_id: str = "default"
) -> Dict[str, float]:
    """Track Google Maps API usage for cost calculation.

    Args:
        node_id: The node executing the Maps action
        action: Action name (geocode, nearby_search, etc.)
        resource_count: Number of resources (usually 1 per request)
        workflow_id: Optional workflow context
        session_id: Session for aggregation

    Returns:
        Cost breakdown dict with operation, unit_cost, resource_count, total_cost
    """
    from services.plugin.deps import get_database

    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost('google_maps', action, resource_count)

    # Save to database
    db = get_database()
    await db.save_api_usage_metric({
        'session_id': session_id,
        'node_id': node_id,
        'workflow_id': workflow_id,
        'service': 'google_maps',
        'operation': cost_data.get('operation', action),
        'endpoint': action,
        'resource_count': resource_count,
        'cost': cost_data.get('total_cost', 0.0)
    })

    logger.debug(f"[Maps] Tracked usage: {action} x{resource_count} = ${cost_data.get('total_cost', 0):.6f}")
    return cost_data


class MapsService:
    """Google Maps and Places API service."""

    def __init__(self, auth_service: AuthService, settings: Settings):
        self.auth = auth_service
        self.settings = settings

    def validate_coordinates(self, lat: float, lng: float) -> bool:
        """Validate latitude and longitude."""
        return -90 <= lat <= 90 and -180 <= lng <= 180

    def validate_zoom_level(self, zoom: int) -> bool:
        """Validate Google Maps zoom level."""
        return 0 <= zoom <= 21

    async def create_map(self, node_id: str, parameters: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Create Google Maps configuration."""
        start_time = time.time()
        context = context or {}

        try:
            api_key = parameters.get('api_key') or self.settings.google_maps_api_key
            if not api_key:
                raise ValueError("Google Maps API key is required")

            # Extract and validate parameters (snake_case)
            lat = float(parameters.get('lat', 40.7128))
            lng = float(parameters.get('lng', -74.0060))
            zoom = int(parameters.get('zoom', 13))
            map_type = parameters.get('map_type_id', 'ROADMAP')

            if not self.validate_coordinates(lat, lng):
                raise ValueError("Invalid coordinates")
            if not self.validate_zoom_level(zoom):
                raise ValueError("Invalid zoom level")
            if map_type not in ['ROADMAP', 'SATELLITE', 'HYBRID', 'TERRAIN']:
                raise ValueError("Invalid map type")

            result = {
                "map_config": {
                    "center": {"lat": lat, "lng": lng},
                    "zoom": zoom,
                    "mapTypeId": map_type
                },
                "static_map_url": f"https://maps.googleapis.com/maps/api/staticmap?center={lat},{lng}&zoom={zoom}&size=600x400&maptype={map_type.lower()}&key={api_key}",
                "status": "OK"
            }

            # Track: static_map $0.002
            await _track_maps_usage(
                node_id, 'static_map', 1,
                context.get('workflow_id'), context.get('session_id', 'default')
            )

            log_execution_time(logger, "create_map", start_time, time.time())

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "gmaps_create",
                "operation": "map_initialization",
                "result": result,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error("Create map failed", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "gmaps_create",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

    async def geocode_location(self, node_id: str, parameters: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Geocode addresses or reverse geocode coordinates."""
        start_time = time.time()
        context = context or {}

        try:
            api_key = parameters.get('api_key') or self.settings.google_maps_api_key
            if not api_key:
                raise ValueError("Google Maps API key is required")

            gmaps = googlemaps.Client(key=api_key)
            service_type = parameters.get('service_type', 'geocode')

            if service_type == 'geocode':
                address = parameters.get('address', '')
                if not address:
                    raise ValueError("Address is required for geocoding")

                geocode_result = gmaps.geocode(address=address)
                result = {
                    "service_type": "geocoding",
                    "input": {"address": address},
                    "results": geocode_result,
                    "status": "OK" if geocode_result else "ZERO_RESULTS"
                }
                # Track: geocode $0.005
                await _track_maps_usage(
                    node_id, 'geocode', 1,
                    context.get('workflow_id'), context.get('session_id', 'default')
                )

            elif service_type == 'reverse_geocode':
                lat = float(parameters.get('lat', 0))
                lng = float(parameters.get('lng', 0))

                if not self.validate_coordinates(lat, lng):
                    raise ValueError("Invalid coordinates")

                reverse_result = gmaps.reverse_geocode((lat, lng))
                result = {
                    "service_type": "reverse_geocoding",
                    "input": {"lat": lat, "lng": lng},
                    "results": reverse_result,
                    "status": "OK" if reverse_result else "ZERO_RESULTS"
                }
                # Track: reverse_geocode $0.005
                await _track_maps_usage(
                    node_id, 'reverse_geocode', 1,
                    context.get('workflow_id'), context.get('session_id', 'default')
                )

            else:
                raise ValueError(f"Unsupported service type: {service_type}")

            log_execution_time(logger, f"geocoding_{service_type}", start_time, time.time())

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "gmaps_locations",
                "operation": service_type,
                "result": result,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        except googlemaps.exceptions.ApiError as e:
            logger.error("Google Maps API error", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "gmaps_locations",
                "error": f"Google Maps API error: {str(e)}",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error("Geocoding failed", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "gmaps_locations",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

    async def find_nearby_places(self, node_id: str, parameters: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Find nearby places using Google Places API."""
        start_time = time.time()
        context = context or {}

        try:
            api_key = parameters.get('api_key') or self.settings.google_maps_api_key
            if not api_key:
                raise ValueError("Google Maps API key is required")

            gmaps = googlemaps.Client(key=api_key)

            # Extract and validate parameters
            lat = float(parameters.get('lat', 40.7484))
            lng = float(parameters.get('lng', -73.9857))
            radius = int(parameters.get('radius', 500))

            if not self.validate_coordinates(lat, lng):
                raise ValueError("Invalid coordinates")
            if not (1 <= radius <= 50000):
                raise ValueError("Radius must be between 1 and 50000 meters")

            # Optional parameters (snake_case)
            place_type = parameters.get('type', 'restaurant')
            page_size = min(int(parameters.get('page_size', 20)), 20)

            # Extract options (may contain keyword, name, min_price, max_price, open_now, language, rank_by)
            options = parameters.get('options', {})

            # Support both nested options and top-level parameters (snake_case)
            keyword = options.get('keyword', '') if isinstance(options, dict) else parameters.get('keyword', '')
            name_filter = options.get('name', '') if isinstance(options, dict) else parameters.get('name', '')
            min_price = options.get('min_price') if isinstance(options, dict) else parameters.get('min_price')
            max_price = options.get('max_price') if isinstance(options, dict) else parameters.get('max_price')
            open_now = options.get('open_now', False) if isinstance(options, dict) else parameters.get('open_now', False)
            language = options.get('language', 'en') if isinstance(options, dict) else parameters.get('language', 'en')
            rank_by = options.get('rank_by', 'prominence') if isinstance(options, dict) else parameters.get('rank_by', 'prominence')

            logger.debug("[Nearby Places] Extracted parameters",
                        keyword=keyword,
                        name_filter=name_filter,
                        min_price=min_price,
                        max_price=max_price,
                        open_now=open_now,
                        language=language,
                        rank_by=rank_by,
                        place_type=place_type)

            # Build search request
            search_params = {
                'location': (lat, lng),
                'type': place_type
            }

            # Add radius unless ranking by distance
            if rank_by != 'distance':
                search_params['radius'] = radius
            else:
                search_params['rank_by'] = rank_by

            # Add optional parameters
            if keyword:
                search_params['keyword'] = keyword
            if name_filter:
                search_params['name'] = name_filter
            if min_price is not None and min_price != '':
                search_params['min_price'] = int(min_price)
            if max_price is not None and max_price != '':
                search_params['max_price'] = int(max_price)
            if open_now:
                search_params['open_now'] = True
            if language:
                search_params['language'] = language

            nearby_result = gmaps.places_nearby(**search_params)
            results = nearby_result.get('results', [])[:page_size]

            # Track: nearby_search $0.032
            await _track_maps_usage(
                node_id, 'nearby_search', 1,
                context.get('workflow_id'), context.get('session_id', 'default')
            )

            result = {
                "search_parameters": {
                    "location": {"lat": lat, "lng": lng},
                    "radius": radius if rank_by != 'distance' else None,
                    "type": place_type,
                    "keyword": keyword or None,
                    "name_filter": name_filter or None,
                    "min_price": min_price if min_price not in [None, ''] else None,
                    "max_price": max_price if max_price not in [None, ''] else None,
                    "open_now": open_now,
                    "language": language,
                    "rank_by": rank_by,
                    "page_size": page_size
                },
                "results": results,
                "total_results": len(results),
                "status": nearby_result.get('status', 'OK')
            }

            log_execution_time(logger, "nearby_places", start_time, time.time())

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "gmaps_nearby_places",
                "operation": "nearby_search",
                "result": result,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        except googlemaps.exceptions.ApiError as e:
            logger.error("Google Places API error", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "gmaps_nearby_places",
                "error": f"Google Places API error: {str(e)}",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error("Nearby places search failed", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "gmaps_nearby_places",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }