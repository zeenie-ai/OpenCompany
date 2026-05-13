from .._base import AndroidServiceBase


class EnvironmentalSensorsNode(AndroidServiceBase):
    type = "environmentalSensors"
    display_name = "Environmental Sensors"
    description = "Temperature, humidity, pressure, light level"
