"""Wave 16.1 pre-flight invariants for per-queue task routing.

Before ``TemporalWorkerPool`` starts one worker per declared queue
(Wave 16.2) and ``_resolve_activity`` starts returning
``cls.task_queue`` (Wave 16.3), these invariants lock the three
assumptions that make that activation safe:

1. Every ``TaskQueue`` constant is either populated by >=1 plugin or
   explicitly documented as reserved — a queue nobody declares would
   spin an idle worker (harmless) but more importantly signals a stale
   constant.
2. Every plugin's ``task_queue`` value is a declared ``TaskQueue``
   member — an off-registry string would route activities to a queue
   NO pool worker polls, and the workflow would hang at
   schedule-to-start.
3. ``TemporalWorkerPool.DEFAULT_CONCURRENCY`` covers every declared
   queue — a missing entry silently falls back to ``default_pool_size``
   which defeats per-queue sizing.

See docs-internal/TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md §5.
"""

import pytest

from services.plugin.scaling import TaskQueue


@pytest.fixture(scope="module")
def registered_classes():
    import nodes  # noqa: F401  (populate registry via __init_subclass__)
    from services.node_registry import registered_node_classes

    return dict(registered_node_classes())


@pytest.fixture(scope="module")
def declared_queues(registered_classes):
    """Every distinct task_queue string declared by any plugin."""
    queues = {}
    for node_type, cls in registered_classes.items():
        q = getattr(cls, "task_queue", None)
        if q:
            queues.setdefault(q, []).append(node_type)
    return queues


class TestTaskQueueCoverage:
    # TRIGGERS_EVENT has explicit opt-ins (webhook/chat/task triggers);
    # nothing is reserved-but-unpopulated today. If you add a TaskQueue
    # constant ahead of its first plugin, list it here with a comment.
    RESERVED_QUEUES: frozenset = frozenset()

    def test_every_task_queue_has_at_least_one_plugin(self, declared_queues):
        unpopulated = TaskQueue.ALL - set(declared_queues) - self.RESERVED_QUEUES - {TaskQueue.DEFAULT}
        assert not unpopulated, (
            f"TaskQueue constants with zero plugin opt-ins: {sorted(unpopulated)}. "
            "Either a plugin should declare them, or add them to "
            "RESERVED_QUEUES with a comment explaining the reservation."
        )

    def test_no_plugin_uses_undeclared_queue(self, declared_queues):
        rogue = set(declared_queues) - TaskQueue.ALL
        assert not rogue, (
            f"Plugins declare task_queue values outside TaskQueue.ALL: "
            f"{ {q: declared_queues[q] for q in rogue} }. "
            "Once Wave 16.3 routes by cls.task_queue, an off-registry "
            "queue has no pool worker polling it and the activity hangs "
            "at schedule-to-start forever."
        )

    def test_pool_default_concurrency_covers_every_queue(self):
        from services.temporal.worker import TemporalWorkerPool

        missing = TaskQueue.ALL - set(TemporalWorkerPool.DEFAULT_CONCURRENCY)
        assert not missing, (
            f"TemporalWorkerPool.DEFAULT_CONCURRENCY is missing entries for: "
            f"{sorted(missing)}. Add explicit per-queue sizing — the "
            "default_pool_size fallback defeats queue specialisation."
        )

    def test_default_queue_is_populated_or_default_only(self, declared_queues):
        """Sanity: DEFAULT exists in TaskQueue.ALL and the frozenset is
        the single source the other invariants key off."""
        assert TaskQueue.DEFAULT in TaskQueue.ALL
        assert len(TaskQueue.ALL) >= 9, (
            "TaskQueue.ALL shrank below the 9 queues Wave 16 verified — "
            "update docs-internal/TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md "
            "if a queue was deliberately removed."
        )
