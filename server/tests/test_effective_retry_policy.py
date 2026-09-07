"""``BaseNode.effective_retry_policy``: which nodes Temporal may re-run.

Precedence: a policy declared on the class (or an intermediate base) wins;
triggers get one attempt; ``annotations.readonly is True`` without
``destructive`` keeps the three-attempt default; everything else gets one
attempt. ``tests/fixtures/effective_retry_attempts_snapshot.json`` pins the
resulting attempt count per registered node type so a changed annotation
shows up in a diff. Regenerate it with::

    cd server && uv run python -c "import json, nodes; \
        from services.node_registry import registered_node_classes as r; \
        print(json.dumps({t: c.effective_retry_policy().maximum_attempts \
            for t, c in sorted(r().items())}, indent=2))"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.plugin import ActionNode, TriggerNode
from services.plugin.scaling import DEFAULT_RETRY, SINGLE_ATTEMPT_RETRY, RetryPolicy
from services.plugin.tool import ToolNode

pytestmark = pytest.mark.unit

_SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "effective_retry_attempts_snapshot.json"


def _stub(base=ActionNode, **attrs):
    namespace = {"type": "_testEffectiveRetry", "display_name": "stub", **attrs}
    return type("_Stub", (base,), namespace, abstract=True)


class TestPrecedence:
    def test_unannotated_action_gets_one_attempt(self):
        assert _stub().effective_retry_policy() is SINGLE_ATTEMPT_RETRY

    def test_readonly_keeps_the_default(self):
        policy = _stub(annotations={"readonly": True}).effective_retry_policy()
        assert policy is DEFAULT_RETRY
        assert policy.maximum_attempts == 3

    def test_readonly_plus_destructive_gets_one_attempt(self):
        assert _stub(annotations={"readonly": True, "destructive": True}).effective_retry_policy() is SINGLE_ATTEMPT_RETRY

    @pytest.mark.parametrize(
        "annotations",
        [
            {"destructive": True},
            {"readonly": False},
            {"destructive": False, "readonly": False, "open_world": True},
        ],
    )
    def test_mutating_annotations_get_one_attempt(self, annotations):
        assert _stub(annotations=annotations).effective_retry_policy() is SINGLE_ATTEMPT_RETRY

    def test_trigger_gets_one_attempt_regardless_of_annotations(self):
        assert _stub(TriggerNode, annotations={"readonly": True}).effective_retry_policy() is SINGLE_ATTEMPT_RETRY

    def test_tool_node_inherits_read_only_default(self):
        """ToolNode declares ``readonly: True`` on the base; a tool that
        mutates must say so (simple_memory, writeTodos, taskManager do)."""
        assert _stub(ToolNode).effective_retry_policy() is DEFAULT_RETRY

    def test_declared_policy_wins_over_annotations(self):
        declared = RetryPolicy(maximum_attempts=5)
        policy = _stub(annotations={"destructive": True}, retry_policy=declared).effective_retry_policy()
        assert policy is declared

    def test_declaration_on_an_intermediate_base_wins(self):
        declared = RetryPolicy(maximum_attempts=7)
        base = type("_Base", (ActionNode,), {"retry_policy": declared, "annotations": {"destructive": True}}, abstract=True)
        leaf = type("_Leaf", (base,), {"type": "_testEffectiveRetryLeaf", "display_name": "leaf"}, abstract=True)
        assert leaf.effective_retry_policy() is declared

    def test_base_node_default_is_not_treated_as_a_declaration(self):
        """``BaseNode.retry_policy = DEFAULT_RETRY`` is the framework default,
        not a plugin decision, so a mutating node still drops to one attempt."""
        assert _stub(annotations={"destructive": True}).effective_retry_policy().maximum_attempts == 1

    def test_derived_policies_keep_the_non_retryable_names(self):
        names = set(SINGLE_ATTEMPT_RETRY.non_retryable_error_types)
        assert {"NodeUserError", "ValidationError", "PermissionDeniedError", "InvalidParametersError", "OutputValidationError"} <= names
        assert names == set(DEFAULT_RETRY.non_retryable_error_types)

    def test_to_temporal_roundtrip(self):
        temporal = _stub(annotations={"readonly": True}).effective_retry_policy().to_temporal()
        assert temporal.maximum_attempts == 3
        assert "OutputValidationError" in temporal.non_retryable_error_types


class TestRegistrySnapshot:
    @pytest.fixture(autouse=True)
    def _registry(self):
        import nodes  # noqa: F401 -- populate the plugin registry

    def _live(self) -> dict[str, int]:
        from services.node_registry import registered_node_classes

        return {t: cls.effective_retry_policy().maximum_attempts for t, cls in registered_node_classes().items()}

    def test_every_registered_node_resolves_a_policy(self):
        from services.node_registry import registered_node_classes

        for node_type, cls in registered_node_classes().items():
            policy = cls.effective_retry_policy()
            assert isinstance(policy, RetryPolicy), node_type
            assert policy.maximum_attempts >= 1, node_type

    def test_snapshot_matches_the_registry(self):
        expected = {k: v for k, v in json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8")).items() if not k.startswith("_")}
        live = self._live()
        missing = sorted(set(live) - set(expected))
        stale = sorted(set(expected) - set(live))
        changed = {t: (expected[t], live[t]) for t in set(live) & set(expected) if expected[t] != live[t]}
        assert not missing and not stale and not changed, (
            f"effective retry attempts drifted from the snapshot.\n"
            f"  new node types: {missing}\n  removed: {stale}\n  changed (snapshot -> live): {changed}\n"
            "If intentional, regenerate tests/fixtures/effective_retry_attempts_snapshot.json "
            "(command in this module's docstring) in the same commit."
        )

    @pytest.mark.parametrize(
        "node_type, attempts",
        [
            ("codex_agent", 1),
            ("claude_code_agent", 1),
            ("rlm_agent", 1),
            ("vertex_managed_agent", 1),
            ("httpScraper", 3),
            ("braveSearch", 3),
            ("pythonExecutor", 1),
            ("telegramSend", 1),
            ("gmaps_create", 1),
            ("apifyActor", 1),
            ("emailRead", 3),
            ("webhookTrigger", 1),
            ("telegramReceive", 1),
        ],
    )
    def test_representative_nodes(self, node_type, attempts):
        live = self._live()
        assert node_type in live, f"{node_type} is not a registered node type"
        assert live[node_type] == attempts
