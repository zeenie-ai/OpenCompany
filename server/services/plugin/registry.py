"""Idempotent plugin registries.

Single generic base for all the ``register_*`` plumbing that plugin
``__init__.py`` files call into. Replaces five hand-rolled
implementations with the same shape.

Two flavours:

- :class:`IdempotentRegistry` -- key->value mapping with collision
  detection. Used for WS handlers, FastAPI routers, filter builders,
  trigger prechecks, output schemas. Re-registering the same value for
  a key is a no-op (re-import safe); a different value raises
  ``ValueError`` so plugin namespace clashes fail at import time.

- :class:`IdempotentList` -- fanout list with identity dedup. Used for
  per-cycle callbacks like ``register_service_refresh`` where every
  registered callable runs once per cycle. Re-registering the same
  callable is a no-op; different callables coexist (fanout semantics).

Each module that exposes a ``register_*`` public API constructs one of
these and provides a thin wrapper. Module-level dicts that other code
reads directly (e.g. ``FILTER_BUILDERS``, ``NODE_OUTPUT_SCHEMAS``) can
be passed in as the backing store via the ``items`` kwarg, so existing
readers keep working.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import (
    Callable,
    Dict,
    Generic,
    Iterator,
    List,
    Mapping,
    Optional,
    TypeVar,
)

K = TypeVar("K")
V = TypeVar("V")


def _qual(value: object) -> str:
    """Best-effort qualified name for callable / class -- used in error messages."""
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return repr(value)


class IdempotentRegistry(Generic[K, V]):
    """Key->value registry with idempotent register + collision raise.

    Args:
        name: Human-readable label used in error messages
            (``f"{name}: '{key}' already registered..."``).
        items: Optional pre-existing dict to use as the backing store.
            Lets a module expose its registry-backed dict as a
            module-level constant for readers that grew up reading the
            dict directly.
        on_register: Optional callback fired after a successful
            register. Used e.g. by ``register_output_schema`` to bust a
            JSON-schema cache.
    """

    def __init__(
        self,
        name: str,
        *,
        items: Optional[Dict[K, V]] = None,
        on_register: Optional[Callable[[K, V], None]] = None,
    ) -> None:
        self._name = name
        self._items: Dict[K, V] = items if items is not None else {}
        self._on_register = on_register

    def register(self, key: K, value: V) -> None:
        """Add ``key -> value``. Idempotent on equality; raises on conflict.

        Three equivalence checks (any match = idempotent re-register):

        1. ``existing == value`` — covers strings / ints / dataclasses /
           dicts (content-equal even on reload) and singleton instances
           cached across imports.
        2. **Reload tolerance for callables**: same fully-qualified
           name (``__module__`` + ``__qualname__``) means the same
           source-level function reloaded under fresh identity.
           ``importlib.reload(module)`` constructs new function objects
           that compare unequal under Python's identity-based
           ``function.__eq__``; without this branch the
           self-containment reload tests would break for every plugin
           that registers a wrapper closure.
        3. **Reload tolerance for classes**: same ``__module__`` +
           ``__qualname__`` for class objects (same reason).

        Genuinely conflicting registrations (different values, different
        names) still raise ``ValueError`` at import time.
        """
        existing = self._items.get(key)
        if existing is not None and not self._values_equivalent(existing, value):
            raise ValueError(
                f"{self._name}: {key!r} is already registered by " f"{_qual(existing)}; refusing to overwrite with {_qual(value)}"
            )
        self._items[key] = value
        if self._on_register is not None:
            self._on_register(key, value)

    @staticmethod
    def _values_equivalent(existing: object, new: object) -> bool:
        """True iff ``existing`` and ``new`` are the same registration
        for idempotency purposes. See :meth:`register` docstring."""
        # Content equality first (strings, dicts, dataclasses, singletons
        # cached across imports). Catches the bulk of cases.
        try:
            if existing == new:
                return True
        except Exception:  # noqa: BLE001 — exotic types with broken __eq__
            pass

        # Reload tolerance: callable / class objects re-defined under a
        # fresh module-level identity still count as the "same"
        # registration when their fully-qualified name matches.
        existing_module = getattr(existing, "__module__", None)
        new_module = getattr(new, "__module__", None)
        existing_qualname = getattr(existing, "__qualname__", None)
        new_qualname = getattr(new, "__qualname__", None)
        if (
            existing_qualname is not None
            and new_qualname is not None
            and existing_module == new_module
            and existing_qualname == new_qualname
        ):
            return True

        return False

    def get(self, key: K) -> Optional[V]:
        return self._items.get(key)

    def items(self) -> Mapping[K, V]:
        """Read-only view. Use ``dict(reg.items())`` for a mutable copy."""
        return MappingProxyType(self._items)

    def keys(self) -> List[K]:
        return list(self._items.keys())

    def values(self) -> List[V]:
        return list(self._items.values())

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)


class IdempotentList(Generic[V]):
    """Fanout list with identity dedup.

    Args:
        name: Human-readable label (used in repr / future logging).
        items: Optional pre-existing list to use as the backing store.
    """

    def __init__(
        self,
        name: str,
        *,
        items: Optional[List[V]] = None,
    ) -> None:
        self._name = name
        self._items: List[V] = items if items is not None else []

    def register(self, value: V) -> None:
        """Append if not already present (identity dedup)."""
        if value in self._items:
            return
        self._items.append(value)

    def __iter__(self) -> Iterator[V]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, value: object) -> bool:
        return value in self._items
