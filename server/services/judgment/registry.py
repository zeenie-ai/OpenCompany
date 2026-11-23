"""The judgment provider registry.

One :class:`services.provider_registry.ProviderRegistry`, as
``nodes/translate/_registry.py`` keeps one per capability. A plugin
registers its providers when it loads. The service never imports a plugin,
so a provider that is not registered is simply not tried.
"""

from __future__ import annotations

from typing import List

from services.provider_registry import ProviderRegistry, ProviderSpec

from .protocol import JudgmentProvider

JudgmentProviderSpec = ProviderSpec[JudgmentProvider]

_JUDGMENT: ProviderRegistry[JudgmentProvider] = ProviderRegistry("judgment")


def register_judgment_provider(spec: JudgmentProviderSpec) -> None:
    _JUDGMENT.register(spec)


def get_judgment_provider(name: str) -> JudgmentProviderSpec:
    return _JUDGMENT.get(name)


def has_judgment_provider(name: str) -> bool:
    return _JUDGMENT.has(name)


def judgment_providers() -> List[str]:
    return _JUDGMENT.all()


def _reset_for_tests() -> None:
    _JUDGMENT.reset_for_tests()


__all__ = [
    "JudgmentProviderSpec",
    "get_judgment_provider",
    "has_judgment_provider",
    "judgment_providers",
    "register_judgment_provider",
]
