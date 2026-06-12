"""Vertex / Agent-Platform key detection for the gemini provider.

Gemini accepts two kinds of API keys:

- AI Studio Developer-API keys (``AIza...``) — billed to the user's
  personal Gemini API credits via ``generativelanguage.googleapis.com``.
- Gemini Enterprise Agent Platform / Vertex AI Express keys (``AQ.``) —
  billed to the key's GCP project (eligible for Cloud credits) via
  ``aiplatform.googleapis.com``.

Both the native ``google-genai`` SDK and ``langchain-google-genai``
route to the Vertex backend with ``vertexai=True`` plus the same
``api_key`` — endpoint construction, auth headers, and backend parity
are entirely handled by the official libraries. The only application
concern is detecting which backend a stored key belongs to, which the
key prefix encodes.
"""

from __future__ import annotations

from typing import Optional

VERTEX_KEY_PREFIX = "AQ."


def is_vertex_express_key(api_key: Optional[str]) -> bool:
    """True when the key is an Agent Platform / Vertex AI Express key."""
    return bool(api_key) and api_key.startswith(VERTEX_KEY_PREFIX)
