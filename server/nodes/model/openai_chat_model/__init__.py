from typing import Literal, Optional

from pydantic import Field

from .._base import ChatModelBase, ChatModelParams

from .._credentials import OpenAICredential


class OpenAIChatModelParams(ChatModelParams):
    frequency_penalty: Optional[float] = Field(
        default=0.0, ge=-2.0, le=2.0,
        json_schema_extra={"numberStepSize": 0.1},
    )
    presence_penalty: Optional[float] = Field(
        default=0.0, ge=-2.0, le=2.0,
        json_schema_extra={"numberStepSize": 0.1},
    )
    response_format: Optional[Literal["text", "json_object"]] = Field(default="text")
    thinking_enabled: bool = Field(default=False)
    reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]] = Field(
        default="medium",
        json_schema_extra={"displayOptions": {"show": {"thinking_enabled": [True]}}},
    )


class OpenAIChatModelNode(ChatModelBase):
    type = "openaiChatModel"
    display_name = "OpenAI"
    subtitle = "Chat Model"
    group = ("model",)
    description = "OpenAI GPT models for chat completion and generation"

    credentials = (OpenAICredential,)
    Params = OpenAIChatModelParams
