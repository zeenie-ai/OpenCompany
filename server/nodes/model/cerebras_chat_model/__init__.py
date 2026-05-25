from typing import Optional

from pydantic import Field

from .._base import ChatModelBase, ChatModelParams

from .._credentials import CerebrasCredential


class CerebrasChatModelParams(ChatModelParams):
    thinking_enabled: bool = Field(default=False)
    thinking_budget: Optional[int] = Field(
        default=2048,
        ge=1024,
        le=16000,
        json_schema_extra={"displayOptions": {"show": {"thinking_enabled": [True]}}},
    )


class CerebrasChatModelNode(ChatModelBase):
    type = "cerebrasChatModel"
    display_name = "Cerebras"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Cerebras ultra-fast inference on custom AI hardware"

    credentials = (CerebrasCredential,)
    Params = CerebrasChatModelParams
