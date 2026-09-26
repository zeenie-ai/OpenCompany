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
    description = "DeepSeek V4 models (deepseek-v4.1-flash, deepseek-v4-pro) with 1M context"

    credentials = (DeepSeekCredential,)
    Params = DeepseekChatModelParams
