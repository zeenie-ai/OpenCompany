"""Plugin registry for Temporal SDK ``Worker(plugins=[...])``.

Plugins that ship workflow classes / activities / interceptors build a
:class:`temporalio.plugin.SimplePlugin` instance and register it from
their ``__init__.py``. The Temporal worker passes the snapshot to
``Worker(plugins=[...])`` once at startup; the Temporal SDK's
chain-of-responsibility ``configure_worker()`` hook merges each plugin's
contributions into the worker's effective workflow / activity list
internally.

Same mechanism as the Temporal SDK's own contrib plugins
(``temporalio.contrib.openai_agents`` ships ``OpenAIAgentsPlugin`` for
exactly this purpose). Documented at
https://python.temporal.io/temporalio.plugin.SimplePlugin.html and
https://docs.temporal.io/develop/plugins-guide .

Why a registry rather than direct framework imports
---------------------------------------------------

The framework worker stays plugin-agnostic — no
``from nodes.scheduler... import CronTriggerWorkflow`` at the framework
level. Plugins push their ``SimplePlugin`` instances in; the worker
reads the list and hands it to Temporal. Same direction-of-dependency
contract the eight other Wave-11.I self-registration registries enforce
(canary_trigger_type / poll_coroutine_factory / filter_builder /
ws_handler / router / oauth_callback_path / service_refresh /
output_schema / shutdown_hook / service_factory / social_send_handler).
"""

from __future__ import annotations

from typing import List

from temporalio.plugin import SimplePlugin

from services.plugin.registry import IdempotentRegistry


# Keyed by plugin name so duplicates raise at import time (e.g. two
# plugins both declaring ``name="cron-scheduler"`` would surface a
# namespace clash). SimplePlugin instances are content-comparable via
# the IdempotentRegistry's qualname check (reload safe).
_REGISTRY: IdempotentRegistry[str, SimplePlugin] = IdempotentRegistry("temporal_plugin")


def register_temporal_plugin(plugin: SimplePlugin) -> None:
    """Publish a Temporal ``SimplePlugin`` for the worker.

    Idempotent on re-import. Raises ``ValueError`` on a namespace
    conflict (same ``plugin.name`` registered by a different caller).

    Args:
        plugin: A :class:`temporalio.plugin.SimplePlugin` instance.
            The ``name`` field is used as the registry key; ``workflows``,
            ``activities``, ``interceptors`` etc. surface in the
            worker's effective configuration via Temporal's plugin chain.
    """
    # ``SimplePlugin.name`` is a method (per temporalio.plugin API) — call
    # it to get the actual identifier string the registry keys on.
    _REGISTRY.register(plugin.name(), plugin)


def temporal_plugins() -> List[SimplePlugin]:
    """Return every registered plugin (snapshot, in registration order).

    The Temporal worker reads this once at startup::

        worker = Worker(
            client,
            task_queue=...,
            plugins=temporal_plugins(),
            workflows=[MachinaWorkflow, ...],
            activities=[...],
        )
    """
    return list(_REGISTRY.values())


__all__ = [
    "register_temporal_plugin",
    "temporal_plugins",
]
