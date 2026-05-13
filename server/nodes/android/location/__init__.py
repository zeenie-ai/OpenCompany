from .._base import AndroidServiceBase


class LocationNode(AndroidServiceBase):
    type = "location"
    display_name = "Location"
    description = "GPS location tracking with latitude, longitude, accuracy, and provider"
