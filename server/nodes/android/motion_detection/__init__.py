from .._base import AndroidServiceBase


class MotionDetectionNode(AndroidServiceBase):
    type = "motionDetection"
    display_name = "Motion Detection"
    description = "Accelerometer + gyroscope - motion, shake, orientation"
