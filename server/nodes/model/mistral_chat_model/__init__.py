from .._base import ChatModelBase

from .._credentials import MistralCredential


class MistralChatModelNode(ChatModelBase):
    type = "mistralChatModel"
    display_name = "Mistral"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Mistral AI models for reasoning, coding, and multilingual tasks"
    credentials = (MistralCredential,)
