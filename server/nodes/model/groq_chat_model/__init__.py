from typing import Literal, Optional

from pydantic import Field

from .._base import ChatModelBase, ChatModelParams

from .._credentials import GroqCredential


class GroqChatModelParams(ChatModelParams):
    thinking_enabled: bool = Field(default=False)
    reasoning_format: Optional[Literal["parsed", "hidden"]] = Field(
        default="parsed",
        json_schema_extra={"displayOptions": {"show": {"thinking_enabled": [True]}}},
    )


class GroqChatModelNode(ChatModelBase):
    type = "groqChatModel"
    display_name = "Groq"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Groq ultra-fast LLM inference (Llama, Qwen3, GPT-OSS)"

    credentials = (GroqCredential,)
    Params = GroqChatModelParams
