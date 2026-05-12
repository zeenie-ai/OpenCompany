"""Plugins for the 'skill' palette group.

Self-contained plugin folder. Owns:

- ``master_skill.py`` -- the :class:`MasterSkillNode` plugin.
- ``simple_memory.py`` -- the :class:`SimpleMemoryNode` plugin.
- ``_expander.py`` -- Master-Skill expansion callback registered with
  :func:`services.plugin.edge_walker.register_master_skill_expander`
  so the framework-side edge walker doesn't need to know about
  ``services.skill_loader``.
"""

from services.plugin.edge_walker import register_master_skill_expander

from ._expander import expand_master_skill

# Edge-walker calls back into the plugin to expand Master-Skill nodes
# into per-skill entries. Wave 11.I, X3 -- replaces the inline
# ``from services.skill_loader import get_skill_loader`` import that
# previously lived in ``edge_walker._append_skill_entries``.
register_master_skill_expander(expand_master_skill)
