"""The frontend must not keep its own copy of what the backend declares.

This suite exists because two hardcoded node-type arrays in
``client/src/contexts/WebSocketContext.tsx`` silently rotted until they were
wrong in production: 5 of 10 triggers and 16 of 21 agents. The consequence
was a phantom "Request timeout: execute_node" 30 s after pressing Run on
eight real node types, and a third stale copy in ``Dashboard.tsx`` refused to
deploy any workflow whose only entry point was ``stripeReceive``.

Deleting those copies fixes today. These tests are what stop tomorrow.

Cross-tree by design — the backend registry is the authority, so the
assertion belongs where the authority lives. Same idiom as
``test_node_spec.py``'s ``asset:<key>`` invariant, which already walks
``client/src/assets/icons``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_SRC = REPO_ROOT / "client" / "src"

# Types too generic to distinguish from ordinary English or CSS in a regex.
# Excluded deliberately: a guard that cries wolf gets disabled, and these are
# the ones that would. "browser" and "canvas" are also the names of Workspace
# surfaces (the Browser and Canvas tabs, a step's surface). Every excluded
# type is still covered by the executionTimeoutMs invariant below, which needs
# no source scanning.
_TOO_GENERIC = frozenset(
    {"start", "console", "shell", "browser", "canvas", "timer", "location", "code", "gallery"}
)


def _client_sources() -> list[Path]:
    return [
        p
        for p in CLIENT_SRC.rglob("*.ts*")
        if p.suffix in {".ts", ".tsx"}
        and "__tests__" not in p.parts
        and not p.name.endswith(".d.ts")
    ]


class TestExecutionBudgetIsServed:
    """The client sizes its request budget from the node's own declaration."""

    def test_every_node_serves_an_execution_timeout(self):
        import nodes  # noqa: F401  -- registers every plugin
        from services.node_spec import get_node_spec, list_node_types_with_spec

        missing = [
            node_type
            for node_type in list_node_types_with_spec()
            if not isinstance(
                (get_node_spec(node_type).get("uiHints") or {}).get("executionTimeoutMs"),
                int,
            )
        ]
        assert not missing, (
            "these node types serve no uiHints.executionTimeoutMs, so the client "
            f"would fall back to a generic budget for them: {missing}"
        )

    def test_triggers_declare_a_long_budget(self):
        """A trigger waits for an external event, so its budget must be big.

        This is the assertion that would have caught the original bug: the
        client's 30 s default was cutting off nodes that legitimately wait
        for hours.
        """
        import nodes  # noqa: F401
        from services.node_spec import get_node_spec, list_node_types_with_spec

        one_hour_ms = int(timedelta(hours=1).total_seconds() * 1000)
        too_short = {
            node_type: (get_node_spec(node_type).get("uiHints") or {}).get(
                "executionTimeoutMs"
            )
            for node_type in list_node_types_with_spec()
            if get_node_spec(node_type).get("componentKind") == "trigger"
        }
        offenders = {t: ms for t, ms in too_short.items() if (ms or 0) < one_hour_ms}
        assert too_short, "no trigger nodes found — the query itself is wrong"
        assert not offenders, (
            f"triggers must declare a budget of at least 1 h; got {offenders}"
        )

    def test_the_signal_separates_look_alike_nodes(self):
        """The regression that motivated using the timeout, not componentKind.

        ``socialSend`` / ``socialReceive`` declare ``component_kind="agent"``
        purely to borrow the multi-handle canvas layout — they are plain
        ActionNodes. A componentKind-based rule would hand them an infinite
        client budget. The declared timeout tells them apart for free.
        """
        import nodes  # noqa: F401
        from services.node_spec import get_node_spec

        def budget(node_type: str) -> int:
            return (get_node_spec(node_type).get("uiHints") or {})["executionTimeoutMs"]

        assert get_node_spec("socialSend").get("componentKind") == "agent", (
            "premise changed: socialSend no longer borrows the agent layout, so "
            "this test no longer guards anything — re-derive it"
        )
        assert budget("socialSend") == budget("httpRequest"), (
            "socialSend should carry an ordinary action budget despite rendering "
            "as an agent; if this drifts, a componentKind-based rule looks correct again"
        )
        assert budget("webhookTrigger") > budget("socialSend")


# Sites that already hardcoded a node type when this guard was introduced.
# Keyed by (path, node_type) rather than line number so ordinary edits don't
# churn it.
#
# This is a DEBT LEDGER, not an allowlist: the test below fails if an entry
# stops being needed, so the set can only shrink. Each of these should
# eventually read the type off the NodeSpec — several are genuinely
# UI-specific (which canvas component to render) and may end up expressed as
# a uiHint instead.
_BASELINE_VIOLATIONS = frozenset(
    {
        ("client/src/Dashboard.tsx", "aiAgent"),
        ("client/src/Dashboard.tsx", "chatAgent"),
        ("client/src/components/LocationParameterPanel.tsx", "gmaps_create"),
        ("client/src/components/TriggerNode.tsx", "whatsappReceive"),
        ("client/src/hooks/useDragAndDrop.ts", "aiAgent"),
        ("client/src/hooks/useDragAndDrop.ts", "chatAgent"),
        ("client/src/hooks/useReactFlowNodes.ts", "taskManager"),
        ("client/src/utils/locationUtils.ts", "gmaps_create"),
        ("client/src/components/parameterPanel/TaskManagerPanel.tsx", "orchestrator_agent"),
        ("client/src/components/parameterPanel/TaskManagerPanel.tsx", "ai_employee"),
        ("client/src/components/parameterPanel/TeamMonitorPanel.tsx", "orchestrator_agent"),
        ("client/src/components/parameterPanel/TeamMonitorPanel.tsx", "ai_employee"),
    }
)


class TestClientKeepsNoNodeTypeLists:
    """No NEW client file may enumerate backend node types.

    Matching is restricted to the shapes that actually encode a *decision* —
    an array literal, an equality comparison, or an `includes(...)` — rather
    than any occurrence of the string. An unrestricted match produces false
    positives on ordinary prose and CSS, and a noisy guard is one somebody
    turns off.
    """

    def test_no_client_file_enumerates_backend_node_types(self):
        import nodes  # noqa: F401
        from services.node_spec import list_node_types_with_spec

        candidates = [
            t for t in list_node_types_with_spec() if t not in _TOO_GENERIC and len(t) > 4
        ]
        pattern = re.compile(
            r"""(?:===\s*|!==\s*|\[\s*|,\s*|includes\(\s*)['"](%s)['"]"""
            % "|".join(re.escape(t) for t in candidates)
        )

        found: set[tuple[str, str]] = set()
        located: dict[tuple[str, str], str] = {}
        for path in _client_sources():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith(("//", "*", "/*")):
                    continue  # a comment naming a type is documentation
                for match in pattern.finditer(line):
                    key = (rel, match.group(1))
                    found.add(key)
                    located.setdefault(key, f"{rel}:{lineno}")

        new = sorted(found - _BASELINE_VIOLATIONS)
        assert not new, (
            "client code is deciding behaviour from a hardcoded backend node type. "
            "Read it off the NodeSpec instead (getCachedNodeSpec -> componentKind / "
            "group / uiHints), which cannot go stale.\n  "
            + "\n  ".join(f"{located[k]} -> {k[1]!r}" for k in new)
        )

    def test_the_debt_ledger_only_shrinks(self):
        """A baseline entry that no longer applies must be deleted.

        Otherwise the ledger silently becomes a graveyard and stops meaning
        anything — and worse, it would keep permitting a re-introduction at a
        site that had already been cleaned up.
        """
        import nodes  # noqa: F401
        from services.node_spec import list_node_types_with_spec

        known = set(list_node_types_with_spec())
        candidates = [t for t in known if t not in _TOO_GENERIC and len(t) > 4]
        pattern = re.compile(
            r"""(?:===\s*|!==\s*|\[\s*|,\s*|includes\(\s*)['"](%s)['"]"""
            % "|".join(re.escape(t) for t in candidates)
        )

        found: set[tuple[str, str]] = set()
        for path in _client_sources():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            for line in text.splitlines():
                if line.lstrip().startswith(("//", "*", "/*")):
                    continue
                for match in pattern.finditer(line):
                    found.add((rel, match.group(1)))

        stale = sorted(_BASELINE_VIOLATIONS - found)
        assert not stale, (
            "these baseline entries no longer match anything — delete them from "
            f"_BASELINE_VIOLATIONS: {stale}"
        )

        unknown = sorted(t for _, t in _BASELINE_VIOLATIONS if t not in known)
        assert not unknown, (
            "the baseline names node types that are not in the backend registry; "
            f"those sites are dead code, not debt: {unknown}"
        )
