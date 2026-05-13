"""LM Studio chat-model plugin.

Auto-registers via BaseNode.__init_subclass__. LM Studio exposes a pure
OpenAI-compatible endpoint at ``http://localhost:1234/v1`` by default,
so the existing OpenAI-compatible fallback in
``services/llm/factory.py`` routes it through ``OpenAIProvider`` with
``base_url`` from ``llm_defaults.json`` — same path as deepseek/kimi/
mistral. The user's custom server URL is stored as the
``lmstudio_proxy`` credential.
"""

from .._base import ChatModelBase

from .._credentials import LMStudioCredential


class LMStudioChatModelNode(ChatModelBase):
    type = "lmstudioChatModel"
    display_name = "LM Studio"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Run local LLMs via LM Studio's OpenAI-compatible server"
    credentials = (LMStudioCredential,)
