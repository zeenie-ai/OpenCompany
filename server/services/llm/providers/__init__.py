"""Native LLM provider implementations.

Importing this package triggers self-registration of every shipped
provider into ``services.llm.registry._REGISTRY`` via the
``register_provider(...)`` side-effect at the bottom of each provider
module. ``ChatUnifier`` reads the registry directly — no factory
dispatch lives in ``services/ai.py`` anymore.

Adding a new provider:
  1. Create the module under this package.
  2. Implement the provider class (matching ``LLMProvider`` Protocol).
  3. Call ``register_provider(ProviderSpec(...))`` at module bottom.
  4. Add the module name to the import list below.
  5. Add a ``providers.<name>`` block to ``server/config/llm_defaults.json``.
"""

from __future__ import annotations

# Side-effect imports — each module registers its ProviderSpec into the
# global registry at the bottom of the file. Order is documentation
# only; Python's module cache makes duplicate registration impossible.
from services.llm.providers import anthropic  # noqa: F401
from services.llm.providers import openai  # noqa: F401
from services.llm.providers import gemini  # noqa: F401
from services.llm.providers import openrouter  # noqa: F401
from services.llm.providers import _compat  # noqa: F401  (registers 8 OpenAI-compat providers)
