"""Skill materialisation for claude code agent — per-workflow workspace.

Writes ``<workspace_dir>/.claude/skills/<name>/SKILL.md`` for each
connected-and-enabled skill, so claude's built-in skill loader (the
``Skill`` tool in ``--allowedTools``) can discover them. Claude live-
watches its skill directories — adding, editing, or removing a
``SKILL.md`` mid-session is picked up without restart per the
[skills reference](https://code.claude.com/docs/en/skills) — which
lets us toggle skills on a warm subprocess without respawning.

This is the canonical pattern per Anthropic's own SDK
(``code.claude.com/docs/en/agent-sdk/skills``): filesystem-first
discovery with `SKILL.md` files. There is no programmatic
skill-injection channel — neither MCP `resources`/`prompts`, hooks,
nor settings.json offer a per-session skill toggle equivalent to
this filesystem layer.

Why workspace_dir (not cwd)
---------------------------

Memory-bound pool runs spawn with ``cwd = repo_root`` so claude's
``project_key`` stays stable for ``--continue`` / ``--resume``.
Writing skills into ``<repo_root>/.claude/skills/`` would:

  - Pollute the user's repo (gitignored, but visible).
  - Accumulate stale SKILL.md trees across workflow runs that wired
    different skills.
  - Let workflow A's skills bleed into workflow B's subprocess
    (claude walks parent dirs up to the repo root for skill discovery).

The per-workflow workspace at ``data/workspaces/<workflow_id>/`` is
already passed via ``--add-dir <workspace>`` (see
``AICliService.run_batch``), and claude scans ``.claude/skills/``
inside every ``--add-dir`` path. Materialising there gives us
per-workflow isolation and no inter-workflow races.

Diff-based add/remove
---------------------

:func:`materialise_skills` takes the previous skill set + the new one
and applies just the delta to disk. Removed skills are ``rmtree``'d;
added skills are written. Skills present in both sets are left alone.
Claude's filesystem watcher picks up both edge events automatically.

For the cold-spawn path, ``previous_skill_names=None`` is equivalent
to ``previous_skill_names=set()`` — every skill in ``skill_names`` is
written.

Failures (skill not found, OSError on copy / write) log at WARNING
and are skipped — never fatal to the spawn.

Migration note
--------------

Pre-cutover, the pool path materialised under ``cwd = repo_root`` so
SKILL.md trees accumulated in the user's actual repo. The user can
safely run ``rm -rf .claude/skills/`` from the repo root once;
no future runs write there. (We deliberately do NOT auto-clean —
the repo's ``.claude/`` may contain user-authored skills that we
have no right to touch.)
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import FrozenSet, Iterable, Optional, Tuple

import yaml

from core.logging import get_logger

logger = get_logger(__name__)


async def materialise_skills(
    workspace_dir: Path,
    skill_names: Iterable[str],
    *,
    previous_skill_names: Optional[Iterable[str]] = None,
    log_label: str = "cli-agent",
) -> Tuple[int, int]:
    """Materialise the wired skill set under workspace_dir's
    ``.claude/skills/``, removing any prior skills not in the new set.

    Args:
        workspace_dir: ``data/workspaces/<workflow_id>/``. The helper
            writes under ``<workspace_dir>/.claude/skills/<name>/``.
            Claude picks these up via ``--add-dir <workspace_dir>``
            (emitted in argv by :class:`AICliService.run_batch`) and
            its live filesystem watcher.
        skill_names: Skills that should be present after this call —
            already filtered to "connected AND enabled" upstream
            (master-skill expansion in
            ``services.plugin.edge_walker._collect_agent_connections``
            drops disabled entries from the wired set).
        previous_skill_names: Skills present BEFORE this call. Pass
            ``None`` or an empty set for cold spawn (writes everything
            in ``skill_names``). For warm reuse, pass what was
            materialised last time so we only touch the delta.
        log_label: Free-form prefix for log lines (caller's identity).

    Returns:
        ``(added_count, removed_count)``. Caller uses these to log /
        broadcast and to update its stored ``materialised_skills``
        bookkeeping.
    """
    new_set: FrozenSet[str] = frozenset(s for s in skill_names if s)
    prev_set: FrozenSet[str] = frozenset(s for s in (previous_skill_names or ()) if s)

    added = new_set - prev_set
    removed = prev_set - new_set

    if not added and not removed:
        return (0, 0)

    skills_dir = Path(workspace_dir) / ".claude" / "skills"
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "[%s] cannot create skills dir %s: %s — skipping all changes",
            log_label,
            skills_dir,
            exc,
        )
        return (0, 0)

    # Remove first: claude's watcher fires deregistration events
    # before re-registrations, which keeps the agent's tool/skill
    # registry consistent across the diff.
    removed_count = 0
    for name in removed:
        dest = skills_dir / name
        if not dest.exists():
            continue
        try:
            await asyncio.to_thread(shutil.rmtree, dest, ignore_errors=False)
            removed_count += 1
            logger.info(
                "[%s] removed skill %r from %s",
                log_label,
                name,
                dest,
            )
        except OSError as exc:
            logger.warning(
                "[%s] failed to remove skill %r at %s: %s — leaving",
                log_label,
                name,
                dest,
                exc,
            )

    if not added:
        return (0, removed_count)

    # Add: parallelise with asyncio.gather since each skill write is
    # CPU- and disk-bound and independent of the others. Scales to
    # the 1000-skill masterSkill case without serial slow-down.
    from services.skill_loader import get_skill_loader

    loader = get_skill_loader()
    add_tasks = [
        asyncio.create_task(
            _materialise_one(loader, skills_dir, name, log_label),
            name=f"materialise_skill({name})",
        )
        for name in sorted(added)
    ]
    results = await asyncio.gather(*add_tasks, return_exceptions=False)
    added_count = sum(1 for ok in results if ok)
    return (added_count, removed_count)


async def _materialise_one(
    loader,
    skills_dir: Path,
    name: str,
    log_label: str,
) -> bool:
    """Write one skill. Returns True on success, False on skip / error.

    Filesystem-origin skills are copytree'd wholesale (preserves
    ``scripts/`` and ``references/`` subdirs verbatim); DB-origin
    user skills get a synthesised SKILL.md from frontmatter + body.
    """
    try:
        skill = await loader.load_skill_async(name)
    except Exception as exc:
        logger.warning(
            "[%s] load_skill_async(%r) failed: %s — skipping",
            log_label,
            name,
            exc,
        )
        return False
    if skill is None:
        logger.warning(
            "[%s] skill %r not found in registry — skipping materialisation",
            log_label,
            name,
        )
        return False

    dest = skills_dir / name
    try:
        if skill.metadata.path is not None:
            # Filesystem skill: copytree preserves SKILL.md +
            # scripts/ + references/ + templates/ atomically.
            await asyncio.to_thread(
                shutil.copytree,
                skill.metadata.path,
                dest,
                dirs_exist_ok=True,
            )
        else:
            # DB skill: reconstruct frontmatter from metadata
            # (allowed-tools is space-separated per Anthropic spec)
            # + write body.
            await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
            frontmatter = {
                "name": skill.metadata.name,
                "description": skill.metadata.description,
                "allowed-tools": " ".join(skill.metadata.allowed_tools),
                "metadata": skill.metadata.metadata,
            }
            body = f"---\n" f"{yaml.safe_dump(frontmatter, sort_keys=False)}" f"---\n\n" f"{skill.instructions}"
            await asyncio.to_thread(
                (dest / "SKILL.md").write_text,
                body,
                encoding="utf-8",
            )
        logger.info("[%s] materialised skill %r -> %s", log_label, name, dest)
        return True
    except OSError as exc:
        logger.warning(
            "[%s] failed to materialise skill %r at %s: %s",
            log_label,
            name,
            dest,
            exc,
        )
        return False
