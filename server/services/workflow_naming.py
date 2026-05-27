"""Workflow naming — human-readable slugs derived from display names.

Separates two concerns the system used to conflate:

* ``Workflow.id`` (UUID) — stable system identity. Never changes on
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

import uuid
from typing import Set

from slugify import slugify

from core.logging import get_logger

logger = get_logger(__name__)


_SLUG_SEPARATOR = "_"
_SLUG_MAX_LEN = 50
_FALLBACK_SLUG = "Workflow"


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
    slug = slugify(
        name or "",
        separator=_SLUG_SEPARATOR,
        lowercase=False,
        max_length=_SLUG_MAX_LEN,
        word_boundary=False,
    )
    return slug or _FALLBACK_SLUG


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


def new_workflow_id() -> str:
    """Canonical workflow UUID: 32 lowercase hex chars, no prefix.

    Replaces the three pre-Wave-14 generation conventions
    (``wf_<12hex>`` from agent_builder, ``workflow-<ms>-<8hex>`` from
    workflow_import, ``example_<original_id>`` from example_loader)
    with a single stable form. The id never appears in human-readable
    surfaces — it's pure system identity — so a bare UUID is the right
    shape. Stays on stdlib to avoid adding a dep for marginal benefit
    (ULID/UUIDv7 would gain sortability but every consumer treats the
    id as opaque).
    """
    return uuid.uuid4().hex


__all__ = [
    "slugify_name",
    "next_available_slug",
    "new_workflow_id",
]
