from typing import Literal, Optional

from pydantic import Field

from .._base import ChatModelBase, ChatModelParams

from .._credentials import GeminiCredential


class GeminiChatModelParams(ChatModelParams):
    top_k: Optional[int] = Field(default=40, ge=1, le=100)
    safety_settings: Literal["default", "strict", "permissive"] = Field(default="default")
    thinking_enabled: bool = Field(default=False)
    thinking_budget: Optional[int] = Field(
        default=2048, ge=1024, le=16000,
        json_schema_extra={"displayOptions": {"show": {"thinking_enabled": [True]}}},
    )


class GeminiChatModelNode(ChatModelBase):
    type = "geminiChatModel"
    display_name = "Gemini"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Google Gemini models for multimodal AI capabilities"

    credentials = (GeminiCredential,)
    Params = GeminiChatModelParams
