from .._base import AndroidServiceBase


class BluetoothAutomationNode(AndroidServiceBase):
    type = "bluetoothAutomation"
    display_name = "Bluetooth Automation"
    description = "Bluetooth control - enable, disable, get status, paired devices"
