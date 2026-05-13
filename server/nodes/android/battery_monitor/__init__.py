from .._base import AndroidServiceBase


class BatteryMonitorNode(AndroidServiceBase):
    type = "batteryMonitor"
    display_name = "Battery Monitor"
    description = "Monitor battery status, level, charging state, temperature, and health"
