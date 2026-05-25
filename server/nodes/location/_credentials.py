"""Google Maps credential (Wave 11.E.1 — per-domain).

Used by the three location plugins in this folder (gmaps_create,
gmaps_locations, gmaps_nearby_places). Separate from
:class:`GoogleCredential` because Maps uses a static API key
(billing-tied to a GCP project) whereas Workspace uses OAuth.
"""

from __future__ import annotations

import httpx

from services.plugin.credential import ApiKeyCredential, ProbeResult


# Google's documented sentinel address for connectivity tests on the
# Geocoding API. Returns ``status=OK`` for any working key with the
# Geocoding API enabled; ``REQUEST_DENIED`` for a bad key or disabled
# API. We never store the response — it's just a probe.
_GEOCODE_SENTINEL = "1600 Amphitheatre Parkway, Mountain View, CA"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GoogleMapsCredential(ApiKeyCredential):
    id = "google_maps"
    display_name = "Google Maps"
    category = "Location"
    key_name = "key"
    key_location = "query"
    docs_url = "https://developers.google.com/maps/documentation"

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Geocode a sentinel address to verify the key is live.

        Replaces ``handle_validate_maps_key`` in
        ``routers/websocket.py``; the storage + broadcast wiring is
        handled by the base :meth:`Credential.validate` so this method
        only needs to translate the geocode response into a
        :class:`ProbeResult`. ``REQUEST_DENIED`` → invalid; ``OK`` /
        ``ZERO_RESULTS`` / ``OVER_QUERY_LIMIT`` → valid (the key
        authenticated, even if the query didn't return rows).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                _GEOCODE_URL,
                params={"address": _GEOCODE_SENTINEL, "key": api_key},
            )
        payload = response.json()
        status = payload.get("status")

        if status == "REQUEST_DENIED":
            return ProbeResult(
                valid=False,
                message=payload.get("error_message", "Invalid API key"),
            )
        return ProbeResult(
            valid=True,
            message=("Google Maps API key is valid" if status == "OK" else f"API key is valid (status: {status})"),
        )
