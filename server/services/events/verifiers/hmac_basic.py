"""Generic HMAC-SHA256 verifier — fallback for providers whose scheme
is "single header carrying hex HMAC of the raw body".

Subclasses customise :attr:`header_name` and (optionally)
:attr:`signature_prefix`. The default reads ``X-Signature-256`` and
expects a bare hex digest.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import ClassVar, Mapping

from .base import WebhookVerifier


class HmacVerifier(WebhookVerifier):
    header_name: ClassVar[str] = "X-Signature-256"
    signature_prefix: ClassVar[str] = ""  # e.g. "sha256=" for some providers

    @classmethod
    def verify(cls, headers: Mapping[str, str], body: bytes, secret: str) -> None:
        sig = cls._header(headers, cls.header_name)
        if not sig:
            raise ValueError(f"{cls.header_name} header missing")
        if cls.signature_prefix and not sig.startswith(cls.signature_prefix):
            raise ValueError(f"{cls.header_name} missing prefix {cls.signature_prefix!r}")
        provided = sig[len(cls.signature_prefix) :]
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise ValueError(f"{cls.header_name} mismatch")
