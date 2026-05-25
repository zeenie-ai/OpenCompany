from typing import Optional

from pydantic import Field

from .._base import ChatModelBase, ChatModelParams

from .._credentials import DeepSeekCredential


class DeepseekChatModelParams(ChatModelParams):
    frequency_penalty: Optional[float] = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
    )
    presence_penalty: Optional[float] = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
    )


class DeepseekChatModelNode(ChatModelBase):
    type = "deepseekChatModel"
    display_name = "DeepSeek"
    subtitle = "Chat Model"
    group = ("model",)
    description = "DeepSeek V3 models (deepseek-chat, deepseek-reasoner with always-on CoT)"

    credentials = (DeepSeekCredential,)
    Params = DeepseekChatModelParams
