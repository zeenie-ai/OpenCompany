from .._base import AndroidServiceBase


class MediaControlNode(AndroidServiceBase):
    type = "mediaControl"
    display_name = "Media Control"
    description = "Media playback - volume, playback, play files"
