"""Provider settings, from ``server/config/judgment_defaults.json``.

A thin layer over :class:`services.plugin.capabilities.CapabilityConfig`, as
``nodes/translate/_config.py`` is. Every setting sits on the provider's own
block; there are no sections. The service reads the order, ``keyless``,
``credential_id``, ``calibrated``, ``question_types``, ``timeout_seconds``
and whether a provider names an ``image_model``; the providers read the rest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from services.plugin.capabilities import CapabilityConfig

CONFIG = CapabilityConfig("judgment_defaults.json")

#: Used only for a provider whose block gives no usable time limit.
FALLBACK_TIMEOUT_SECONDS = 15.0


def reload_defaults() -> None:
    CONFIG.reload()


def order() -> List[str]:
    """The providers, in the order the service tries them."""
    return [str(name) for name in CONFIG.data.get("order") or []]


def _block(provider: str) -> Dict[str, Any]:
    return CONFIG.provider_block(provider)


def keyless(provider: str) -> bool:
    """The provider needs no key of its own, and raises ``NotConnected``
    itself when it has nothing to call."""
    return bool(_block(provider).get("keyless"))


def credential_id(provider: str) -> str:
    return CONFIG.credential_id(provider)


def calibrated(provider: str) -> bool:
    return bool(_block(provider).get("calibrated"))


def question_types(provider: str) -> List[str]:
    return [str(kind) for kind in _block(provider).get("question_types") or []]


def timeout_seconds(provider: str) -> float:
    value = _block(provider).get("timeout_seconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return FALLBACK_TIMEOUT_SECONDS


def base_url(provider: str) -> str:
    return CONFIG.base_url(provider)


def model(provider: str, *, images: bool = False) -> str:
    """The model to ask: the provider's ``image_model`` when there are images."""
    block = _block(provider)
    return str((block.get("image_model") if images else "") or block.get("model") or "")


def accepts_images(provider: str) -> bool:
    """A provider takes images when it names a model for them."""
    return bool(_block(provider).get("image_model"))


def image_headers(provider: str) -> Dict[str, str]:
    headers = _block(provider).get("image_headers")
    return {str(name): str(value) for name, value in headers.items()} if isinstance(headers, dict) else {}


def image_side_limits(provider: str) -> Tuple[int, int]:
    """The smallest and largest width or height, in pixels, the provider takes."""
    block = _block(provider)
    return int(block.get("image_min_side") or 0), int(block.get("image_max_side") or 0)


__all__ = [
    "CONFIG",
    "FALLBACK_TIMEOUT_SECONDS",
    "accepts_images",
    "base_url",
    "calibrated",
    "credential_id",
    "image_headers",
    "image_side_limits",
    "keyless",
    "model",
    "order",
    "question_types",
    "reload_defaults",
    "timeout_seconds",
]
