"""Judgment providers. Importing this package registers them all with
``services.judgment``, which tries them in the order
``config/judgment_defaults.json`` gives.

- ``systemone``: TypeSafe's Jev, registered twice, as ``typesafe`` (the
  owner's TypeSafe key) and ``openrouter`` (the same API through the owner's
  OpenRouter key).
- ``llm``: the owner's chat model answering as JSON, when neither is
  connected.

No provider imports an SDK when it registers.
"""

from __future__ import annotations

from nodes.judgment._providers import llm  # noqa: F401
from nodes.judgment._providers import systemone  # noqa: F401
