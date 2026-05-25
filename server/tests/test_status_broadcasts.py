"""Pytest invariants for the status-broadcast contract (Wave 11.I, milestone U).

Mirrors :mod:`tests.credentials.test_credential_broadcasts` for the
plugin-status / refresh / send_custom_event surface. Locks the
post-Wave-11.I contract:

* The credential-mutation path uses :class:`WorkflowEvent`
  (CloudEvents v1.0 envelope) -- already enforced by
  :mod:`tests.credentials.test_credential_broadcasts`. Re-asserted
  here for completeness.
* :func:`services.event_waiter.dispatch` /
  :func:`dispatch_async` accept either a ``WorkflowEvent`` or a
  ``(str, dict)`` pair via :func:`_unpack_event` (post-Q signature).
* ``send_custom_event`` callsites: enforced as a ratchet -- the
  ``_LEGACY_RAW_DICT_CALLSITES`` allowlist enumerates the existing
  raw-dict callers with documented WHY. New callsites must use
  ``WorkflowEvent`` unless they're added to the allowlist with a
  rationale.

Two carve-outs:

* ``_TELEMETRY_CARVE_OUT`` -- permanent exempts. High-frequency
  / paired-wire / log streams that won't ever move to typed
  envelopes (per-node-status broadcasts fire hundreds of times per
  workflow run; envelope wrapping is pure overhead there).
* ``_LEGACY_RAW_DICT_BROADCASTS`` / ``_LEGACY_RAW_DICT_CALLSITES`` --
  grandfathered until per-plugin migration. Each entry documents
  WHY it's still raw and what would unlock it.

Companion: :mod:`tests.credentials.test_credential_broadcasts` (the
older sibling that locks the credential-broadcast contract via the
same ``inspect.getsource`` introspection style).
"""

from __future__ import annotations

import inspect
import re
from typing import FrozenSet

import pytest


# ============================================================================
# CARVE-OUTS
# ============================================================================
#
# Telemetry: permanent exempts. These methods broadcast on every
# user-action / log-line / lifecycle-tick. Wrapping each emission in a
# CloudEvents envelope adds bytes + JSON encoding overhead at sites that
# fire hundreds of times per workflow run -- not worth it. The wire
# frames are paired with their dual (e.g. ``api_key_status`` is paired
# with ``credential_catalogue_updated`` in the credential mutation
# path) so frontend listeners always have a typed channel available
# for state changes.
_TELEMETRY_CARVE_OUT: FrozenSet[str] = frozenset(
    {
        "update_api_key_status",  # paired-wire with credential_catalogue_updated
        "update_node_status",  # ~hundreds per workflow run
        "update_node_output",  # paired with update_node_status
        "update_variable",  # per-write
        "update_variables",  # batch per-write
        "update_workflow_status",  # lifecycle ticks
        "update_deployment_status",  # lifecycle ticks
        "broadcast_console_log",  # log stream
        "broadcast_terminal_log",  # log stream
        # Pure dispatcher -- iterates plugin-registered callbacks via
        # TaskGroup, does not emit any broadcast itself. Per-plugin
        # refresh callbacks live in nodes/<plugin>/_refresh.py and are
        # subject to their own typed-envelope migration tracked outside
        # the StatusBroadcaster class.
        "_refresh_all_services",
    }
)

# Plugin status updates that still emit raw ``{type: 'X_status', data:
# {...}}`` frames. The frontend's per-plugin status panels listen for
# these wire-frames directly today; switching to a CloudEvents-typed
# envelope is a frontend change too. Migration is per-plugin, not
# included in milestone U scope.
_LEGACY_RAW_DICT_BROADCASTS: FrozenSet[str] = frozenset(
    {
        # Wave 12 B1: ``update_android_status`` retired; moved to
        # ``nodes/android/_events.py:broadcast_android_status``.
        # Wave 12 B2: ``update_whatsapp_status`` retired; moved to
        # ``nodes/whatsapp/_events.py:broadcast_whatsapp_status``.
        # Wave 12 B3: ``update_telegram_status`` retired; moved to
        # ``nodes/telegram/_events.py:broadcast_telegram_status``. The
        # shared ``_emit_connection_typed`` helper retired alongside (no
        # remaining callers). Allowlist now empty — all three plugin-named
        # broadcast methods live in their plugin folders.
    }
)

# ``send_custom_event`` callers that still pass raw dicts. Each entry
# documents WHY. Per-plugin migration unlocks each one.
#
# Wave 12 B2: ``nodes/whatsapp/_service.py`` retired — all 7
# send_custom_event callsites (message_sent/received + 4 newsletter
# events + history_sync_complete) moved to typed CloudEvents wrappers
# in ``nodes/whatsapp/_events.py``.
# Wave 12 B9: ``routers/webhook.py`` retired — webhook_received dispatch
# moved to ``nodes/trigger/webhook_trigger/_events.py``.
# Wave 12 B8: ``services/handlers/tools.py`` retired — 3 task_completed
# callsites moved to ``nodes/agent/_events.py``.
# Wave 12 B10: ``nodes/tool/agent_builder/__init__.py`` retired —
# workflow_ops_apply dispatch moved to
# ``nodes/tool/agent_builder/_events.py``.
#
# Allowlist now empty: every send_custom_event callsite lives in a
# plugin folder's _events.py wrapper.
_LEGACY_RAW_DICT_CALLSITES: FrozenSet[str] = frozenset()


# ============================================================================
# REGEXES
# ============================================================================

# Inside a ``_refresh_*`` / ``update_*_status`` method body, the
# ``broadcast({...})`` call MUST consume a WorkflowEvent (looking for
# ``WorkflowEvent`` token in a 200-char window before the ``broadcast(``
# call) OR call ``broadcast_credential_event`` (which wraps WorkflowEvent
# internally).
_REFRESH_OR_STATUS_NAME = re.compile(r"^_refresh_|^update_\w+_status$")

# Function-defining line in source. Captures method name.
_METHOD_DEF = re.compile(r"^\s+(?:async )?def (\w+)\b")


# ============================================================================
# Helpers
# ============================================================================


def _is_typed_broadcast(method_source: str) -> bool:
    """Heuristic: method source either references ``WorkflowEvent``
    OR calls a typed-broadcast helper (``broadcast_credential_event``).
    """
    if "WorkflowEvent" in method_source:
        return True
    if "broadcast_credential_event" in method_source:
        return True
    return False


def _split_methods(class_source: str) -> dict[str, str]:
    """Split a class body's source into ``{method_name: method_source}``.

    Anchors on ``\\n    (?:async )?def `` which marks method starts at
    class-level indentation. Module-level functions are ignored.
    """
    methods: dict[str, str] = {}
    parts = re.split(r"(\n    (?:async )?def \w+)", class_source)
    # Re-stitch: parts looks like [preamble, "    def foo", body0, "    def bar", body1, ...]
    for i in range(1, len(parts), 2):
        header = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        match = _METHOD_DEF.match(header.lstrip("\n"))
        if match:
            methods[match.group(1)] = header + body
    return methods


# ============================================================================
# 1) Credential event uses WorkflowEvent (already locked elsewhere; re-pinned)
# ============================================================================


class TestCredentialBroadcastUsesWorkflowEvent:
    """``broadcast_credential_event`` is the canonical typed-builder
    helper. Pinned here too so a regression in either invariant file
    catches the break.
    """

    def test_uses_workflow_event(self):
        from services.status_broadcaster import StatusBroadcaster

        src = inspect.getsource(StatusBroadcaster.broadcast_credential_event)
        assert "WorkflowEvent" in src, (
            "broadcast_credential_event must wrap WorkflowEvent (CloudEvents "
            "v1.0). Removing the typed envelope breaks the cross-tab "
            "catalogue refresh contract."
        )


# ============================================================================
# 2) event_waiter.dispatch accepts WorkflowEvent (post-Q signature)
# ============================================================================


class TestEventWaiterDispatchAcceptsEnvelope:
    """Wave 11.I, milestone Q. ``dispatch`` and ``dispatch_async``
    accept either a ``WorkflowEvent`` or a ``(str, dict)`` pair via
    ``_unpack_event`` -- the (str, dict) shape stays supported via
    ``WorkflowEvent.from_legacy`` upstream of the dispatcher.

    Locks the contract so future refactors don't drop the envelope
    path silently.
    """

    @pytest.fixture
    def event_waiter_module(self):
        from services import event_waiter

        return event_waiter

    def test_unpack_event_present(self, event_waiter_module):
        assert hasattr(event_waiter_module, "_unpack_event"), (
            "event_waiter must expose _unpack_event -- the normalisation " "helper that lets dispatch() accept a WorkflowEvent directly."
        )

    def test_dispatch_calls_unpack_event(self, event_waiter_module):
        src = inspect.getsource(event_waiter_module.dispatch)
        assert "_unpack_event" in src, (
            "dispatch() must route through _unpack_event() so a " "WorkflowEvent argument is normalised before the dispatch loop."
        )

    def test_dispatch_async_calls_unpack_event(self, event_waiter_module):
        src = inspect.getsource(event_waiter_module.dispatch_async)
        assert "_unpack_event" in src, "dispatch_async() must route through _unpack_event() for the " "same reason as dispatch()."

    def test_unpack_event_handles_workflow_event(self, event_waiter_module):
        """Functional check: a WorkflowEvent input round-trips to
        ``(event_type, data)``. The legacy tuple-form path is exercised
        by the rest of the test suite."""
        from services.events.envelope import WorkflowEvent

        event = WorkflowEvent(
            source="machinaos://test",
            type="test.something.happened",
            subject="test-subject",
            data={"k": "v"},
        )
        event_type, data = event_waiter_module._unpack_event(event)
        assert event_type == "test.something.happened"
        assert data == {"k": "v"}

    def test_unpack_event_handles_legacy_tuple_form(self, event_waiter_module):
        event_type, data = event_waiter_module._unpack_event(
            "legacy.event.type",
            {"k": "v"},
        )
        assert event_type == "legacy.event.type"
        assert data == {"k": "v"}

    def test_unpack_event_rejects_unsupported_form(self, event_waiter_module):
        with pytest.raises(TypeError):
            event_waiter_module._unpack_event(42)


# ============================================================================
# 3) Plugin status broadcasts: typed builder enforced (with carve-outs)
# ============================================================================


class TestStatusBroadcastsUseTypedBuilder:
    """Every ``_refresh_*`` / ``update_*_status`` method on
    :class:`StatusBroadcaster` MUST broadcast via a ``WorkflowEvent``
    OR be listed in :data:`_TELEMETRY_CARVE_OUT` (permanent exempt)
    OR :data:`_LEGACY_RAW_DICT_BROADCASTS` (per-plugin migration TODO).

    The carve-outs exist so the test stays green during incremental
    migration; new methods that don't fit either carve-out fail CI.
    """

    @pytest.fixture
    def broadcaster_methods(self) -> dict[str, str]:
        from services import status_broadcaster as sb

        cls_src = inspect.getsource(sb.StatusBroadcaster)
        return _split_methods(cls_src)

    def test_no_overlap_between_carve_outs(self):
        """A method can't be both telemetry-permanent-exempt and
        legacy-grandfathered. If it appears in both, one of the two
        lists is wrong."""
        overlap = _TELEMETRY_CARVE_OUT & _LEGACY_RAW_DICT_BROADCASTS
        assert not overlap, f"Methods in both _TELEMETRY_CARVE_OUT and " f"_LEGACY_RAW_DICT_BROADCASTS: {sorted(overlap)}. Pick one."

    def test_status_methods_use_typed_builder_or_listed(
        self,
        broadcaster_methods,
    ):
        offenders = []
        for name, body in broadcaster_methods.items():
            if not _REFRESH_OR_STATUS_NAME.match(name):
                continue
            if name in _TELEMETRY_CARVE_OUT:
                continue
            if name in _LEGACY_RAW_DICT_BROADCASTS:
                continue
            if not _is_typed_broadcast(body):
                offenders.append(name)
        assert not offenders, (
            f"StatusBroadcaster methods {offenders} broadcast via raw "
            f"{{type: ..., data: ...}} dicts. Either use WorkflowEvent / "
            f"broadcast_credential_event, or document the exception in "
            f"_TELEMETRY_CARVE_OUT (permanent exempt) or "
            f"_LEGACY_RAW_DICT_BROADCASTS (per-plugin migration TODO) "
            f"in tests/test_status_broadcasts.py."
        )

    def test_legacy_entries_actually_exist_on_class(self, broadcaster_methods):
        """If we drop a legacy method during migration, remove its
        allowlist entry too. Catches stale carve-outs."""
        missing = _LEGACY_RAW_DICT_BROADCASTS - set(broadcaster_methods.keys())
        assert not missing, (
            f"_LEGACY_RAW_DICT_BROADCASTS lists methods that don't exist "
            f"on StatusBroadcaster: {sorted(missing)}. Remove the stale "
            f"entries from tests/test_status_broadcasts.py."
        )

    def test_telemetry_entries_actually_exist_on_class(self, broadcaster_methods):
        """Same staleness check for the telemetry carve-out."""
        missing = _TELEMETRY_CARVE_OUT - set(broadcaster_methods.keys())
        assert not missing, (
            f"_TELEMETRY_CARVE_OUT lists methods that don't exist "
            f"on StatusBroadcaster: {sorted(missing)}. Remove the stale "
            f"entries from tests/test_status_broadcasts.py."
        )


# ============================================================================
# 3b) Plugin status broadcasts ALSO emit typed CloudEvents siblings
# ============================================================================


class TestStatusBroadcastsAlsoEmitTypedEnvelope:
    """Wave 12 D4: per-plugin ``broadcast_<plugin>_status`` wrappers in
    ``nodes/<plugin>/_events.py`` MUST emit ONLY the typed CloudEvents
    envelope on ``plugin_connection_status`` (legacy raw frame retired).

    Phase history:
      - B1-B3: dual-emit (legacy raw + typed sibling) so the FE could
        switch between channels without breaking back-compat.
      - B11: FE adds the ``case 'plugin_connection_status'`` handler
        that reads the typed envelope.
      - D4: drops the legacy raw frames now that FE consumes via the
        typed channel.

    Each plugin's wrapper file is parametrized below — every entry must
    reference ``plugin_connection_status`` AND must NOT reference the
    retired legacy wire key.
    """

    # (plugin_label, module_path, wrapper_function_name, retired_legacy_key)
    _PLUGIN_WRAPPERS = [
        ("android", "nodes.android._events", "broadcast_android_status", "android_status"),
        ("whatsapp", "nodes.whatsapp._events", "broadcast_whatsapp_status", "whatsapp_status"),
        ("telegram", "nodes.telegram._events", "broadcast_telegram_status", "telegram_status"),
    ]

    @pytest.mark.parametrize(
        "plugin,module_path,wrapper_name,retired_legacy_key",
        _PLUGIN_WRAPPERS,
        ids=[row[0] for row in _PLUGIN_WRAPPERS],
    )
    def test_plugin_wrapper_emits_typed_only(
        self,
        plugin: str,
        module_path: str,
        wrapper_name: str,
        retired_legacy_key: str,
    ):
        """Each plugin's broadcaster wrapper emits ONLY the typed
        ``plugin_connection_status`` envelope. The legacy raw wire key
        retired in Wave 12 D4 — reintroducing it would resurrect the
        dual-broadcast bug where the FE handler fired twice per status
        change.
        """
        import importlib

        mod = importlib.import_module(module_path)
        wrapper = getattr(mod, wrapper_name, None)
        assert wrapper is not None, (
            f"{module_path}.{wrapper_name} missing — Wave 12 B-phase "
            f"contract violated; plugin {plugin!r} must own its broadcast "
            f"wrapper in its plugin folder."
        )
        mod_src = inspect.getsource(mod)
        # Positive: typed envelope channel still emitted.
        assert "plugin_connection_status" in mod_src, (
            f"{module_path} must emit a typed CloudEvents envelope on " f"``plugin_connection_status`` (the cross-plugin typed channel)."
        )
        # Negative: legacy raw wire key retired in D4.
        legacy_double = f'"{retired_legacy_key}"'
        legacy_single = f"'{retired_legacy_key}'"
        # Allow mentions inside docstrings / comments — narrow the
        # negative assertion to lines that look like an actual emit
        # (``broadcaster.broadcast({"type": "<legacy_key>"`` pattern).
        emit_double = f'"type": "{retired_legacy_key}"'
        emit_single = f"'type': '{retired_legacy_key}'"
        assert emit_double not in mod_src and emit_single not in mod_src, (
            f"{module_path} must NOT emit on the retired legacy wire key "
            f"{retired_legacy_key!r}. Wave 12 D4 dropped the dual-emit; "
            f"FE now consumes via ``plugin_connection_status``."
        )

    def test_emit_connection_typed_helper_retired(self):
        """B3 retired the cross-plugin helper. Locks: nothing on the
        broadcaster should reintroduce a shared parametrised version —
        per RFC §6.4, connection_status is plugin-specific."""
        from services.status_broadcaster import StatusBroadcaster

        assert not hasattr(StatusBroadcaster, "_emit_connection_typed"), (
            "StatusBroadcaster._emit_connection_typed was retired in "
            "Wave 12 B3. Each plugin now owns its connection_status "
            "factory in nodes/<plugin>/_events.py (RFC §6.4). Don't "
            "reintroduce the shared helper."
        )


# ============================================================================
# 4) send_custom_event callsite ratchet
# ============================================================================


class TestSendCustomEventPayload:
    """``broadcaster.send_custom_event(event_type, data)`` accepts
    either a raw dict or a ``WorkflowEvent`` today. Wave 11.I,
    milestone U adds this ratchet so NEW callsites must pass a
    WorkflowEvent unless explicitly listed in
    :data:`_LEGACY_RAW_DICT_CALLSITES` with a documented WHY.
    """

    @pytest.fixture
    def callsite_files(self) -> list[str]:
        """All files under ``server/`` containing ``send_custom_event``."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent  # server/
        return [
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*.py")
            if "send_custom_event" in p.read_text(encoding="utf-8")
            and "tests/" not in str(p).replace("\\", "/")
            and p.name != "test_status_broadcasts.py"
        ]

    def test_definition_lives_on_status_broadcaster(self):
        """``send_custom_event`` is defined on the broadcaster --
        anchor the symbol so allowlist entries can point to caller
        files without colliding with the definition."""
        from services.status_broadcaster import StatusBroadcaster

        assert hasattr(StatusBroadcaster, "send_custom_event")

    def test_callsites_match_allowlist(self, callsite_files):
        """Every file calling ``send_custom_event`` MUST be in the
        legacy allowlist (with documented WHY) OR call it through a
        WorkflowEvent envelope (caught by the next test).

        New plugins that need to broadcast custom events should
        either use :class:`WorkflowEvent` directly (preferred) or add
        themselves to :data:`_LEGACY_RAW_DICT_CALLSITES` with a
        comment explaining why typed-envelope migration is deferred.
        """
        # The status broadcaster file owns the definition; not a "caller".
        callers = {f for f in callsite_files if not f.endswith("status_broadcaster.py")}
        unknown = callers - _LEGACY_RAW_DICT_CALLSITES
        # New callers must EITHER appear in the allowlist OR pass a
        # WorkflowEvent -- the next test verifies the envelope
        # path; here we only flag truly new files.
        offenders = []
        for path in unknown:
            full = Path_resolve(path)
            src = full.read_text(encoding="utf-8")
            if not _passes_workflow_event(src):
                offenders.append(path)
        assert not offenders, (
            f"send_custom_event callsites missing from typed-envelope "
            f"path AND not listed in _LEGACY_RAW_DICT_CALLSITES: "
            f"{sorted(offenders)}. Either pass a WorkflowEvent or "
            f"add the file path to the allowlist with documented WHY "
            f"in tests/test_status_broadcasts.py."
        )

    def test_legacy_callsites_actually_call_send_custom_event(self, callsite_files):
        """Stale-entry check: every path in
        ``_LEGACY_RAW_DICT_CALLSITES`` must still contain a
        ``send_custom_event`` call. Catches obsolete allowlist entries
        that should have been removed during a migration."""
        missing = _LEGACY_RAW_DICT_CALLSITES - set(callsite_files)
        assert not missing, (
            f"_LEGACY_RAW_DICT_CALLSITES lists files that no longer "
            f"call send_custom_event: {sorted(missing)}. Remove the "
            f"stale entries."
        )


# ============================================================================
# 5) Service-refresh callback signature contract
# ============================================================================


class TestServiceRefreshCallbackSignature:
    """``StatusBroadcaster._refresh_all_services`` invokes every callback
    registered via ``register_service_refresh`` as ``callback(self)`` — the
    broadcaster is passed as the sole positional argument.

    Regression: a callback declared ``async def refresh() -> None`` (no
    args) crashes at refresh-fan-out time with ``TypeError: takes 0
    positional arguments but 1 was given``. The TaskGroup swallows the
    exception and logs at WARNING, so the broken callback never throws
    loudly — observed in prod for ``refresh_temporal_status`` until the
    Wave 13 follow-up. This invariant catches the shape mismatch at
    test time so future refresh registrations can't reintroduce it.
    """

    def test_every_registered_callback_accepts_one_positional_arg(self):
        import inspect

        # Importing the broadcaster module triggers plugin-side
        # ``register_service_refresh(...)`` calls via the standard
        # plugin import side-effects.
        from services.status_broadcaster import _SERVICE_REFRESH_CALLBACKS

        offenders: list[str] = []
        for cb in list(_SERVICE_REFRESH_CALLBACKS):
            try:
                sig = inspect.signature(cb)
            except (TypeError, ValueError):  # pragma: no cover — defensive
                offenders.append(f"{cb!r} (couldn't introspect)")
                continue

            params = [
                p
                for p in sig.parameters.values()
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            required = [p for p in params if p.default is inspect.Parameter.empty]
            # Must accept at least one positional arg (the broadcaster);
            # additional defaults are fine, varargs (*args) is fine.
            accepts_one_positional = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()) or len(params) >= 1
            # Must NOT require MORE than one positional — the framework
            # only supplies the broadcaster, nothing else.
            too_many_required = len(required) > 1

            if not accepts_one_positional or too_many_required:
                offenders.append(f"{cb.__module__}.{cb.__qualname__}{sig}")

        assert not offenders, (
            f"register_service_refresh callbacks must accept the "
            f"StatusBroadcaster as their sole positional argument: "
            f"``async def refresh(broadcaster) -> None``. These "
            f"violate that contract: {offenders}. The framework calls "
            f"each callback as ``callback(self)`` inside "
            f"``StatusBroadcaster._refresh_all_services`` — a 0-arg "
            f"callback raises ``TypeError: takes 0 positional arguments "
            f"but 1 was given`` and the TaskGroup swallows it at WARNING."
        )


# ============================================================================
# Module-private helpers (used by the test bodies above)
# ============================================================================


def Path_resolve(rel: str):
    """Resolve a server-relative path to an absolute :class:`pathlib.Path`."""
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / rel


def _passes_workflow_event(src: str) -> bool:
    """Heuristic: file imports WorkflowEvent AND every send_custom_event
    call is made through a typed envelope. The simplest pattern is to
    import WorkflowEvent and pass a constructed instance. We accept any
    file that imports WorkflowEvent at all -- the test is a nudge, not
    an exhaustive contract."""
    return "WorkflowEvent" in src
