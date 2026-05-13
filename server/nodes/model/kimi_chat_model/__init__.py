from .._base import ChatModelBase

from .._credentials import KimiCredential


class KimiChatModelNode(ChatModelBase):
    type = "kimiChatModel"
    display_name = "Kimi"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Kimi K2 models by Moonshot AI with 256K context (thinking on by default)"
    credentials = (KimiCredential,)
