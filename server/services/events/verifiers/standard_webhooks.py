"""Standard Webhooks (Svix) signature verifier.

Spec: https://www.standardwebhooks.com/
Headers: ``webhook-id``, ``webhook-timestamp``, ``webhook-signature``
Signed payload: ``f"{webhook-id}.{webhook-timestamp}.{raw_body}"``
Algorithm: HMAC-SHA256 of the secret (b64-decoded after the ``whsec_`` prefix
is stripped) over the signed payload, base64-encoded.
``webhook-signature`` may carry multiple space-separated ``v1,<sig>`` entries
to support secret rotation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping

from .base import WebhookVerifier


class StandardWebhooksVerifier(WebhookVerifier):
    @classmethod
    def verify(cls, headers: Mapping[str, str], body: bytes, secret: str) -> None:
        msg_id = cls._header(headers, "webhook-id")
        timestamp = cls._header(headers, "webhook-timestamp")
        sig_header = cls._header(headers, "webhook-signature")
        if not (msg_id and timestamp and sig_header):
            raise ValueError("Standard Webhooks headers missing")

        if secret.startswith("whsec_"):
            key = base64.b64decode(secret[len("whsec_") :])
        else:
            key = secret.encode()

        signed = f"{msg_id}.{timestamp}.".encode() + body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

        candidates: list[str] = []
        for part in sig_header.split():
            if "," in part:
                _, value = part.split(",", 1)
                candidates.append(value)
        if not any(hmac.compare_digest(expected, c) for c in candidates):
            raise ValueError("Standard Webhooks signature mismatch")
