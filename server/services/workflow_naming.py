"""Workflow naming — human-readable slugs derived from display names.

Separates two concerns the system used to conflate:

* ``Workflow.id`` (positive decimal string) — stable system identity. Never changes on
  rename. Used for FK references (``Execution.workflow_id``), Temporal
  Search Attributes, CloudEvents extensions, log context, cache keys,
  in-memory ``DeploymentManager._deployments`` dict keys, frontend
  ``useAppStore.currentWorkflow.id``.
* ``Workflow.slug`` (e.g. ``AI_Assistant_1``) — human-readable derived
  identifier. Mutable on rename. Used for workspace dirs on disk,
  Temporal workflow IDs (visible in Temporal Web UI), cron Schedule IDs,
  export filenames — anywhere a human reads the workflow's name.

Slug format: ``<Sanitized_Name>_<N>`` where ``N`` starts at 1 and fills
gaps. Always-suffix — first creation of "AI Assistant" gets slug
``AI_Assistant_1``, never bare ``AI_Assistant``.

Slugification is delegated to ``python-slugify`` (declared in
``pyproject.toml``). It transliterates Unicode via ``text-unidecode``
("日本語" -> "Ri_Ben_Yu"), strips emoji, collapses runs of
non-alphanumerics, and truncates safely. We pass ``lowercase=False`` so
"AI Assistant" stays as ``AI_Assistant`` and ``separator="_"`` so the
slug is a valid Python identifier prefix (filesystem-safe on every OS,
Temporal-safe up to 1000 chars).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Set

from core.logging import get_logger

logger = get_logger(__name__)


_SLUG_SEPARATOR = "_"
_SLUG_MAX_LEN = 50
_FALLBACK_SLUG = "Workflow"

# Stdlib-only fallback regex for ``node_label_slug``. Plain ``re`` is on
# Temporal's default sandbox allow-list, so Temporal workflows
# (``MachinaWorkflow``, ``TriggerListenerWorkflow``,
# ``PollingTriggerWorkflow``) can call ``node_label_slug`` directly to
# build human-readable child workflow IDs from the user's F2-renamed
# node labels. ``slugify_name`` (which uses ``python-slugify`` for full
# Unicode transliteration) is deferred-imported so the workflow-side
# import doesn't drag the heavier dep into the sandbox.
_NODE_LABEL_PATTERN = re.compile(r"[^A-Za-z0-9]+")
_FALLBACK_NODE_LABEL = "Node"


def slugify_name(name: str) -> str:
    """Sanitize a display name into a filesystem-safe slug base.

    Examples::

        slugify_name("AI Assistant")        -> "AI_Assistant"
        slugify_name("Test/Workflow:Beta!") -> "Test_Workflow_Beta"
        slugify_name("日本語")              -> "Ri_Ben_Yu" (transliterated)
        slugify_name("Hello World 🚀")       -> "Hello_World" (emoji stripped)
        slugify_name("!!!")                 -> "Workflow"  (fallback)
        slugify_name("")                    -> "Workflow"  (fallback)

    Always returns a non-empty ASCII string suitable as a slug BASE.
    Callers append ``_<N>`` to dedupe via :func:`next_available_slug`.
    """
    # Deferred import — ``python-slugify`` is heavy and Temporal
    # workflows that only need :func:`node_label_slug` shouldn't pay
    # the cost (or risk the sandbox tripping on transitive imports).
    from slugify import slugify

    slug = slugify(
        name or "",
        separator=_SLUG_SEPARATOR,
        lowercase=False,
        max_length=_SLUG_MAX_LEN,
        word_boundary=False,
    )
    return slug or _FALLBACK_SLUG


def node_label_slug(node: Dict[str, Any]) -> str:
    """Sandbox-safe slug for a node's user-assigned label.

    Returns the canvas label (set via F2 rename) sanitized to ASCII
    alphanumerics + underscores, suitable for Temporal workflow IDs.
    Falls back to the node type, then to ``"Node"`` for ill-formed
    inputs. Stdlib-only — safe to call inside ``@workflow.defn``
    sandboxes (no ``python-slugify`` / ``text-unidecode`` imports).
    """
    label = (node.get("data") or {}).get("label") or node.get("type") or _FALLBACK_NODE_LABEL
    return _NODE_LABEL_PATTERN.sub(_SLUG_SEPARATOR, label).strip(_SLUG_SEPARATOR) or _FALLBACK_NODE_LABEL


async def next_available_slug(
    name: str,
    database,
    *,
    exclude_id: str | None = None,
) -> str:
    """Return the next free ``<Slug>_<N>`` for the given display name.

    Fill-gap: if ``AI_Assistant_2`` was deleted, the next creation of
    "AI Assistant" reuses ``_2``. Always-suffix: first creation gets ``_1``.

    ``exclude_id`` lets a rename ignore its own current slug — important
    so renaming "AI Assistant" -> "AI Assistant!" (which slugifies to
    the same base) doesn't bump itself to ``_2``.

    Args:
        name: User display name (e.g. ``"AI Assistant"``).
        database: ``Database`` instance — must expose
            :meth:`list_workflow_slugs` returning ``(id, slug)`` pairs.
        exclude_id: Workflow id to skip (rename path, not creation).
    """
    base = slugify_name(name)
    prefix = f"{base}_"
    rows = await database.list_workflow_slugs()
    taken: Set[int] = set()
    for row_id, slug in rows:
        if exclude_id is not None and row_id == exclude_id:
            continue
        if slug and slug.startswith(prefix):
            tail = slug[len(prefix):]
            if tail.isdigit():
                taken.add(int(tail))
    n = 1
    while n in taken:
        n += 1
    return f"{base}_{n}"


def canonicalize_node_ids(
    workflow_id: str, nodes: list[dict], edges: list[dict]
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Give node instances stable IDs derived from plugin metadata.

    Plugin ``type`` is the fixed identity while the per-type ordinal permits
    repeatable plugins such as ``aiAgent``. List order is the stable migration
    order used for legacy graphs. The function is pure and idempotent.
    """
    prefix = f"{workflow_id}:"
    counts: dict[str, int] = {}
    mapping: dict[str, str] = {}
    normalized_nodes: list[dict] = []
    for node in nodes:
        item = dict(node)
        plugin_type = str(item.get("type") or (item.get("data") or {}).get("type") or "node")
        plugin_type = re.sub(r"[^A-Za-z0-9_-]+", "-", plugin_type).strip("-") or "node"
        counts[plugin_type] = counts.get(plugin_type, 0) + 1
        old_id = str(item.get("id") or "")
        expected = f"{prefix}{plugin_type}:{counts[plugin_type]}"
        # Already canonical graphs retain their ordinals even if nodes were
        # reordered by a later layout/save operation.
        match = re.fullmatch(re.escape(prefix + plugin_type) + r":([1-9]\d*)", old_id)
        if match:
            counts[plugin_type] = max(counts[plugin_type], int(match.group(1)))
            expected = old_id
        if old_id and old_id != expected:
            mapping[old_id] = expected
        item["id"] = expected
        normalized_nodes.append(item)

    normalized_edges: list[dict] = []
    for edge in edges:
        item = dict(edge)
        for key in ("source", "target", "sourceNode", "targetNode"):
            value = item.get(key)
            if isinstance(value, str) and value in mapping:
                item[key] = mapping[value]
        normalized_edges.append(item)
    return normalized_nodes, normalized_edges, mapping


__all__ = [
    "slugify_name",
    "node_label_slug",
    "next_available_slug",
    "canonicalize_node_ids",
]
