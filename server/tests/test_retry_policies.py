"""Wave 12 D1: shared activity RetryPolicy constants — contract +
no-duplication invariants.

Locks three layers:

1. **Policy contents**: ``DEFAULT_ACTIVITY_RETRY`` declares
   ``non_retryable_error_types`` including ``"NodeUserError"``. Without
   this declaration the workflow-side retry policy silently overrides
   the plugin's class-level non-retryable contract.

2. **No re-typing of the policy in workflow code**: regression catch
   for someone re-introducing
   ``RetryPolicy(initial_interval=..., maximum_interval=..., maximum_attempts=...)``
   inline in workflow files. The shared constant exists so the
   non-retryable list is set in exactly one place.

3. **All three Temporal workflows that execute activities import the
   shared constant**: MachinaWorkflow, AgentWorkflow,
   PollingTriggerWorkflow. (TriggerListenerWorkflow + CronTriggerWorkflow
   don't execute activities — they only spawn child workflows — so
   they don't need a retry policy.)
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from unittest.mock import MagicMock

import pytest


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


class TestRetryPolicyConstants:
    def test_default_activity_retry_has_node_user_error_non_retryable(self):
        from services.temporal._retry_policies import DEFAULT_ACTIVITY_RETRY

        non_retryable = DEFAULT_ACTIVITY_RETRY.non_retryable_error_types or ()
        assert "NodeUserError" in non_retryable, (
            "DEFAULT_ACTIVITY_RETRY must list NodeUserError as "
            "non-retryable. Without it, plugins that raise NodeUserError "
            "for user-correctable failures (missing required field, bad "
            "regex, unknown enum value) get retried 3 times instead of "
            "failing fast back to the operator."
        )

    def test_default_activity_retry_max_attempts(self):
        from services.temporal._retry_policies import DEFAULT_ACTIVITY_RETRY

        # 3 attempts = original + 2 retries. Matches the pre-Wave-12
        # behaviour of the inline-typed RetryPolicy this module replaces.
        assert DEFAULT_ACTIVITY_RETRY.maximum_attempts == 3

    def test_quick_activity_retry_is_fast(self):
        from services.temporal._retry_policies import QUICK_ACTIVITY_RETRY

        # Fast-fail policy for cheap activities (emit_event,
        # broadcast_progress). Fewer attempts + shorter intervals than
        # the default so wiring bugs surface in tests instead of
        # retry-buffering for 90+ seconds.
        assert QUICK_ACTIVITY_RETRY.maximum_attempts <= 3
        non_retryable = QUICK_ACTIVITY_RETRY.non_retryable_error_types or ()
        assert "NodeUserError" in non_retryable

    def test_browser_task_policies_preserve_existing_temporal_commands(self):
        from temporalio.common import RetryPolicy
        from services.temporal._retry_policies import BROWSER_TASK_CLAIM_RETRY, BROWSER_TASK_CLEANUP_RETRY

        assert BROWSER_TASK_CLAIM_RETRY == RetryPolicy(maximum_attempts=1)
        assert BROWSER_TASK_CLEANUP_RETRY == RetryPolicy(maximum_attempts=0)


class TestReviewedDeliveryRetry:
    """1b.9: reviewed delivery retries without limit, re-attaching to its lease."""

    def test_the_policy_is_unlimited_and_keeps_the_non_retryable_list(self):
        from datetime import timedelta

        from services.temporal._retry_policies import NON_RETRYABLE_ERROR_TYPES, REVIEWED_DELIVERY_RETRY

        assert REVIEWED_DELIVERY_RETRY.maximum_attempts == 0
        assert REVIEWED_DELIVERY_RETRY.initial_interval == timedelta(seconds=2)
        assert REVIEWED_DELIVERY_RETRY.maximum_interval == timedelta(minutes=1)
        assert list(REVIEWED_DELIVERY_RETRY.non_retryable_error_types) == list(NON_RETRYABLE_ERROR_TYPES)

    @pytest.mark.parametrize(
        ("rel_path", "count"),
        [("services/temporal/employee_job_workflow.py", 3), ("services/temporal/agent_workflow.py", 1)],
    )
    def test_every_delivery_call_uses_it(self, rel_path, count):
        import ast
        import pathlib

        tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent / rel_path).read_text(encoding="utf-8"))
        policies = [
            keyword.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute_activity"
            and node.args and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in {"employee.job.deliver", "employee.job.resolve_delivery"}
            for keyword in node.keywords
            if keyword.arg == "retry_policy" and isinstance(keyword.value, ast.Name)
        ]
        assert policies == ["REVIEWED_DELIVERY_RETRY"] * count


class TestQueuePagingRetry:
    """1b.16: controller queue paging retries without limit."""

    def test_the_policy_is_unlimited_and_keeps_the_non_retryable_list(self):
        from datetime import timedelta

        from services.temporal._retry_policies import NON_RETRYABLE_ERROR_TYPES, QUEUE_PAGING_RETRY

        assert QUEUE_PAGING_RETRY.maximum_attempts == 0
        assert QUEUE_PAGING_RETRY.initial_interval == timedelta(seconds=1)
        assert QUEUE_PAGING_RETRY.maximum_interval == timedelta(seconds=30)
        assert list(QUEUE_PAGING_RETRY.non_retryable_error_types) == list(NON_RETRYABLE_ERROR_TYPES)

    def test_every_paging_call_uses_it(self):
        import ast
        import pathlib

        path = pathlib.Path(__file__).resolve().parent.parent / "services/temporal/workflow_control_workflow.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        policies = [
            keyword.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute_activity"
            and node.args and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in {"controller.queue.read", "controller.queue.spill"}
            for keyword in node.keywords
            if keyword.arg == "retry_policy" and isinstance(keyword.value, ast.Name)
        ]
        # The read, the batch and chunk spill, and the legacy spill.
        assert policies == ["QUEUE_PAGING_RETRY"] * 3


class TestCompensationRetry:
    """1b.20: compensations and failure records retry until they land."""

    def test_cleanup_and_bookkeeping_policies_are_unlimited(self):
        from datetime import timedelta

        from temporalio.common import RetryPolicy
        from services.temporal._retry_policies import BOOKKEEPING_RETRY, DELEGATION_CLEANUP_RETRY

        unlimited = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30),
                                maximum_attempts=0)
        # No non-retryable list: a cleanup that fails is still retried.
        assert DELEGATION_CLEANUP_RETRY == unlimited
        assert BOOKKEEPING_RETRY == unlimited

    def test_bookkeeping_policy_has_one_definition(self):
        from services.temporal import _retry_policies, approval_workflow, approved_tool_call_workflow

        assert approval_workflow.BOOKKEEPING_RETRY is _retry_policies.BOOKKEEPING_RETRY
        assert approved_tool_call_workflow.BOOKKEEPING_RETRY is _retry_policies.BOOKKEEPING_RETRY
        assert "BOOKKEEPING_RETRY" in _retry_policies.__all__

    @pytest.mark.parametrize(
        ("rel_path", "expected"),
        [("services/temporal/employee_job_workflow.py", {None: ["BOOKKEEPING_RETRY"] * 2}),
         # The runtime closure's record. The no-assignments decision (a computed
         # activity id) keeps 3 attempts: its activity can raise errors that would loop.
         ("services/temporal/agent_workflow.py",
          {"employee-runtime-failed": ["BOOKKEEPING_RETRY"], "computed": ["AGENT_ACTIVITY_RETRY"]})],
    )
    def test_job_failure_records_use_it(self, rel_path, expected):
        import ast
        import pathlib

        tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent / rel_path).read_text(encoding="utf-8"))
        policies: dict = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute_activity"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "employee.job.failed"):
                keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                activity_id = keywords.get("activity_id")
                name = activity_id.value if isinstance(activity_id, ast.Constant) else (activity_id and "computed")
                policies.setdefault(name, []).append(keywords["retry_policy"].id)
        assert policies == expected

    def test_every_compensation_activity_is_registered(self):
        # An unregistered cleanup activity would now retry for ever.
        from services.temporal.agent_activities import collect_agent_activities
        from services.temporal.pi_inline_delegation import collect_pi_inline_activities

        registered = {getattr(fn, "__temporal_activity_definition").name
                      for fn in [*collect_agent_activities(), *collect_pi_inline_activities()]}
        assert {"agent.cancel_delegation", "agent.release_subagent_permit", "employee.job.failed",
                "coding.inline_delegation_status"} <= registered


class TestNoDuplicatedInlineRetryPolicy:
    """Workflow files must import the shared constant — not re-type
    ``RetryPolicy(initial_interval=..., maximum_interval=..., maximum_attempts=N)``
    inline. The shared constant is the single source of truth for the
    non-retryable contract.
    """

    _WORKFLOW_FILES = (
        "services/temporal/workflow.py",
        "services/temporal/agent_workflow.py",
        "services/temporal/employee_job_workflow.py",
        "services/temporal/polling_trigger_workflow.py",
        "services/temporal/trigger_listener_workflow.py",
        "nodes/scheduler/cron_scheduler/_workflow.py",
    )

    # Matches RetryPolicy(...) with at least one of the three knobs as
    # a kwarg — catches inline construction without flagging
    # ``RetryPolicy`` mentioned in docstrings or imports.
    _INLINE_PATTERN = re.compile(
        r"RetryPolicy\s*\(\s*(initial_interval|maximum_interval|maximum_attempts)\s*=",
        re.MULTILINE,
    )

    def _read(self, rel_path: str) -> str:
        import pathlib

        path = pathlib.Path(__file__).resolve().parent.parent / rel_path
        if not path.exists():
            pytest.skip(f"{rel_path} not present in this checkout")
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("rel_path", _WORKFLOW_FILES)
    def test_no_inline_retry_policy_construction(self, rel_path):
        src = self._read(rel_path)
        match = self._INLINE_PATTERN.search(src)
        assert match is None, (
            f"{rel_path} contains an inline RetryPolicy(...) "
            f"construction at offset {match.start()}: "
            f"{src[match.start():match.end()].strip()}\n"
            "Use services.temporal._retry_policies.DEFAULT_ACTIVITY_RETRY "
            "(or QUICK_ACTIVITY_RETRY for cheap activities) so the "
            "non_retryable_error_types contract stays in one place."
        )


class TestWorkflowsImportSharedConstant:
    """The three workflow files that execute activities must import the
    shared constant. Source-introspection only — no Temporal runtime."""

    @pytest.mark.parametrize(
        "module_path,expected_constant",
        [
            ("services.temporal.workflow", "DEFAULT_ACTIVITY_RETRY"),
            ("services.temporal.agent_workflow", "DEFAULT_ACTIVITY_RETRY"),
            ("services.temporal.polling_trigger_workflow", "DEFAULT_ACTIVITY_RETRY"),
        ],
    )
    def test_imports_shared_constant(self, module_path, expected_constant):
        try:
            mod = __import__(module_path, fromlist=[expected_constant])
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"{module_path} not importable in test env: {exc}")

        src = inspect.getsource(mod)
        # Either an explicit import OR a `from ._retry_policies import ...`
        # line containing the constant name.
        assert expected_constant in src, (
            f"{module_path} must reference "
            f"services.temporal._retry_policies.{expected_constant} "
            "instead of re-typing the policy inline."
        )
