from typing import Optional

from pydantic import Field

from .._base import ChatModelBase, ChatModelParams

from .._credentials import AnthropicCredential


class AnthropicChatModelParams(ChatModelParams):
    top_k: Optional[int] = Field(default=40, ge=1, le=100)
    thinking_enabled: bool = Field(default=False)
    thinking_budget: Optional[int] = Field(
        default=2048,
        ge=1024,
        le=16000,
        json_schema_extra={"displayOptions": {"show": {"thinking_enabled": [True]}}},
    )


class AnthropicChatModelNode(ChatModelBase):
    type = "anthropicChatModel"
    display_name = "Claude"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Anthropic Claude models for conversation and analysis"

    credentials = (AnthropicCredential,)
    Params = AnthropicChatModelParams
