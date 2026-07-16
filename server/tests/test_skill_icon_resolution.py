"""Every shipped skill must resolve a non-empty icon.

The Master Skill panel derives each skill row's icon from its SKILL.md
``allowed-tools`` token: ``SkillLoader._parse_skill_metadata`` runs the
token through snake -> camel and looks the result up in ``visuals.json``
(or the plugin folder's ``icon.svg`` via the node-type registry). When a
plugin's LLM ``tool_name`` differs from ``<snake_case_of_node_type>``
(github, vercel), that lookup silently misses unless a lowercase alias
entry keyed by the tool name exists in ``visuals.json`` — the Master
Skill row then renders a blank icon. This is the silent-miss case the
naming contract in docs-internal/plugin_system.md ("Tool / skill /
visuals naming contract") warns about.

This suite locks the invariant so a new skill/plugin pairing that breaks
the contract fails CI instead of shipping a blank Master Skill row.
Fix options when a test here fails:

1. Name the plugin's ``tool_name`` ``<snake_case_of_node_type>`` so the
   skill's ``allowed-tools`` token resolves through the node-type key.
2. Keep the short tool name and add a ``visuals.json`` alias entry keyed
   by it, carrying the same icon plus the plugin's ``meta.json`` color —
   precedent: ``"github": {"icon": "lobehub:Github", "color": "#8250df"}``.
3. Orphan skills (no backing node) declare inline ``metadata.icon`` /
   ``metadata.color`` in the SKILL.md frontmatter.
"""

from pathlib import Path

import pytest

from services.skill_loader import SkillLoader

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_MDS = sorted(SKILLS_DIR.glob("*/*/SKILL.md"))

_loader = SkillLoader()


def test_skill_corpus_discovered() -> None:
    """Guard the glob itself — a layout change must not silently skip skills."""
    assert len(SKILL_MDS) >= 60, (
        f"Only {len(SKILL_MDS)} SKILL.md files matched "
        f"{SKILLS_DIR}/*/*/SKILL.md — the skills layout moved and this "
        "suite's glob needs updating."
    )


@pytest.mark.parametrize(
    "skill_md",
    SKILL_MDS,
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}",
)
def test_skill_resolves_icon(skill_md: Path) -> None:
    meta = _loader._parse_skill_metadata(skill_md)
    resolved = meta.metadata or {}
    assert resolved.get("icon"), (
        f"{skill_md.relative_to(SKILLS_DIR.parent)} resolves an EMPTY icon "
        f"(allowed-tools={meta.allowed_tools!r}). The Master Skill row will "
        "render blank. Either name the plugin's tool_name "
        "<snake_case_of_node_type>, add a visuals.json alias entry keyed by "
        "the tool name (icon + color, like the \"github\"/\"vercel\" "
        "entries), or declare inline metadata.icon for orphan skills. See "
        "docs-internal/plugin_system.md -> Tool / skill / visuals naming "
        "contract."
    )
