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
_TELEMETRY_CARVE_OUT: FrozenSet[str] = frozenset({
    "update_api_key_status",     # paired-wire with credential_catalogue_updated
    "update_node_status",        # ~hundreds per workflow run
    "update_node_output",        # paired with update_node_status
    "update_variable",           # per-write
    "update_variables",          # batch per-write
    "update_workflow_status",    # lifecycle ticks
    "update_deployment_status",  # lifecycle ticks
    "broadcast_console_log",     # log stream
    "broadcast_terminal_log",    # log stream
    # Pure dispatcher -- iterates plugin-registered callbacks via
    # TaskGroup, does not emit any broadcast itself. Per-plugin
    # refresh callbacks live in nodes/<plugin>/_refresh.py and are
    # subject to their own typed-envelope migration tracked outside
    # the StatusBroadcaster class.
    "_refresh_all_services",
})

# Plugin status updates that still emit raw ``{type: 'X_status', data:
# {...}}`` frames. The frontend's per-plugin status panels listen for
# these wire-frames directly today; switching to a CloudEvents-typed
# envelope is a frontend change too. Migration is per-plugin, not
# included in milestone U scope.
_LEGACY_RAW_DICT_BROADCASTS: FrozenSet[str] = frozenset({
    "update_android_status",   # FE: AndroidStatusPanel reads `android_status` wire-frame
    "update_whatsapp_status",  # FE: WhatsAppStatusPanel reads `whatsapp_status` wire-frame
    "update_telegram_status",  # FE: TelegramStatusPanel reads `telegram_status` wire-frame
})

# ``send_custom_event`` callers that still pass raw dicts. Each entry
# documents WHY. Per-plugin migration unlocks each one.
_LEGACY_RAW_DICT_CALLSITES: FrozenSet[str] = frozenset({
    # WhatsApp event router emits 7 distinct wire types. Migration would
    # also rename ``whatsapp_message_received`` etc. to
    # ``whatsapp.message.received`` namespaced types, requiring FE
    # listener updates.
    "nodes/whatsapp/_service.py",
    # Webhook router emits ``webhook_received``. Webhook payload is
    # arbitrary JSON; the consumer (webhookTrigger node) shape is
    # already abstract, so a typed envelope adds little value.
    "routers/webhook.py",
    # Task-delegation completion. Migration would namespace as
    # ``agent.task.{succeeded,failed}``; matched by taskTrigger.
    "services/handlers/tools.py",
    # Agent Builder canvas-mutation broadcast. Emits
    # ``workflow_ops_apply`` carrying a flat
    # ``{workflow_id, caller_node_id, operations}`` shape consumed by
    # ``client/src/hooks/useWorkflowOpsListener.ts``. Migration to
    # WorkflowEvent would change the wire format (envelope vs flat),
    # requiring a parallel rewrite of the frontend listener +
    # invariant test fixtures. Deferred — typed-envelope migration
    # tracked alongside the credentials.* / agent.task.* migrations.
    "nodes/tool/agent_builder.py",
})


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
            "event_waiter must expose _unpack_event -- the normalisation "
            "helper that lets dispatch() accept a WorkflowEvent directly."
        )

    def test_dispatch_calls_unpack_event(self, event_waiter_module):
        src = inspect.getsource(event_waiter_module.dispatch)
        assert "_unpack_event" in src, (
            "dispatch() must route through _unpack_event() so a "
            "WorkflowEvent argument is normalised before the dispatch loop."
        )

    def test_dispatch_async_calls_unpack_event(self, event_waiter_module):
        src = inspect.getsource(event_waiter_module.dispatch_async)
        assert "_unpack_event" in src, (
            "dispatch_async() must route through _unpack_event() for the "
            "same reason as dispatch()."
        )

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
            "legacy.event.type", {"k": "v"},
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
        assert not overlap, (
            f"Methods in both _TELEMETRY_CARVE_OUT and "
            f"_LEGACY_RAW_DICT_BROADCASTS: {sorted(overlap)}. Pick one."
        )

    def test_status_methods_use_typed_builder_or_listed(
        self, broadcaster_methods,
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
    """Wave 11.I, X4: every legacy ``update_<plugin>_status`` method
    listed in :data:`_LEGACY_RAW_DICT_BROADCASTS` MUST also call
    :meth:`StatusBroadcaster._emit_connection_typed` (or otherwise
    construct ``WorkflowEvent.connection_status(...)``) so a typed
    sibling broadcast goes out alongside the legacy raw frame.

    Phase 4 Y3 retires the raw frame after frontend migrates to read
    the typed channel. Until then, both frames coexist: existing FE
    consumers stay compatible; typed listeners can opt in.
    """

    @pytest.fixture
    def broadcaster_methods(self) -> dict[str, str]:
        from services import status_broadcaster as sb

        cls_src = inspect.getsource(sb.StatusBroadcaster)
        return _split_methods(cls_src)

    @pytest.mark.parametrize("method_name", sorted(_LEGACY_RAW_DICT_BROADCASTS))
    def test_legacy_status_method_emits_typed_sibling(
        self, broadcaster_methods, method_name: str,
    ):
        body = broadcaster_methods.get(method_name)
        assert body is not None, (
            f"StatusBroadcaster.{method_name} missing -- update "
            f"_LEGACY_RAW_DICT_BROADCASTS or restore the method."
        )
        assert "_emit_connection_typed" in body or "WorkflowEvent.connection_status" in body, (
            f"StatusBroadcaster.{method_name} emits only the legacy raw "
            f"{{type: 'X_status', data: {{...}}}} frame. It must ALSO emit a "
            f"CloudEvents-typed sibling via "
            f"``await self._emit_connection_typed(plugin=..., connected=..., "
            f"subject=..., data=...)`` so future typed-channel listeners "
            f"can subscribe. Wave 11.I, X4 contract."
        )

    def test_emit_connection_typed_helper_exists(self):
        """The shared helper must exist on the broadcaster -- locks
        the canonical extension point so per-method migrations don't
        re-invent the typed-envelope construction."""
        from services.status_broadcaster import StatusBroadcaster

        assert hasattr(StatusBroadcaster, "_emit_connection_typed"), (
            "StatusBroadcaster._emit_connection_typed is the canonical "
            "helper for typed plugin-connection broadcasts. It must "
            "exist; per-method update_<plugin>_status implementations "
            "call it after the legacy raw broadcast."
        )

    def test_emit_connection_typed_uses_workflow_event_factory(self):
        """The helper itself must construct via the typed factory, not
        a hand-rolled WorkflowEvent literal."""
        from services.status_broadcaster import StatusBroadcaster

        src = inspect.getsource(StatusBroadcaster._emit_connection_typed)
        assert "WorkflowEvent.connection_status" in src, (
            "_emit_connection_typed must call WorkflowEvent.connection_status(...) "
            "(the typed factory) rather than hand-rolling the envelope. "
            "Wave 11.I CloudEvents contract."
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
        callers = {
            f for f in callsite_files
            if not f.endswith("status_broadcaster.py")
        }
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
