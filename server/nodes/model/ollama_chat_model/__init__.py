"""Ollama chat-model plugin.

Auto-registers via BaseNode.__init_subclass__. Same shape as
``mistral_chat_model.py`` — a single ``ChatModelBase`` subclass plus a
credential class. Routing happens through the existing OpenAI-compatible
fallback in ``services/llm/factory.py``: Ollama serves an OpenAI-shaped
``/v1`` endpoint, so the factory hands it to ``OpenAIProvider`` with
``base_url`` from ``llm_defaults.json``. The user's custom server URL
(if not localhost) is stored as the ``ollama_proxy`` credential and
flows through the same ``proxy_url`` parameter cloud providers already
use for Ollama-style auth delegation.
"""

from .._base import ChatModelBase

from .._credentials import OllamaCredential


class OllamaChatModelNode(ChatModelBase):
    type = "ollamaChatModel"
    display_name = "Ollama"
    subtitle = "Chat Model"
    group = ("model",)
    description = "Run local LLMs via Ollama (llama, mistral, qwen, deepseek-r1, ...)"
    credentials = (OllamaCredential,)
