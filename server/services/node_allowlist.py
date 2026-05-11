"""Node allowlist service.

Reads server/config/node_allowlist.json and decides whether the frontend
Component Palette should show all nodes or filter by an explicit list.

Default-on: if the file is missing, malformed, or has an empty list, all
nodes are shown.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from core.logging import get_logger

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "node_allowlist.json"


class NodeAllowlistService:
    """Resolves the palette visibility config from node_allowlist.json."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path

    def get_config(self) -> Dict[str, Any]:
        """Return the effective allowlist config.

        Response shape:
            show_all: bool
                true  -> do not filter the palette; every node is visible
                         (still subject to disabled_groups + disabled_nodes).
                false -> show only node types listed in enabled_nodes.
            enabled_nodes: list[str]
                Only meaningful when show_all is false.
            disabled_groups: list[str]
                Absolute blocklist. A node whose first group matches any
                entry here is hidden in BOTH normal and dev mode, even
                if listed in enabled_nodes. Use to disable an entire
                backend group (e.g. 'android' hides all 16 Android
                service nodes + androidTool).
            disabled_nodes: list[str]
                Absolute blocklist by exact node-type identifier. Same
                mode-independent enforcement as disabled_groups; use
                for one-off types whose group label doesn't match
                (e.g. 'android_agent' belongs to the 'agent' group).
            disabled_credential_categories: list[str]
                Absolute blocklist for the Credentials Modal — every
                provider whose `category` matches an entry here is
                hidden from the modal AND its category header is
                stripped. Use to disable an entire credential category
                (e.g. 'android' hides the Android relay panel + the
                Android category header). Mirrors disabled_groups
                semantically — the same conceptual entity (android
                feature surface) but the credential catalogue uses its
                own category taxonomy independent of node groups.
            disabled_skill_folders: list[str]
                Absolute blocklist for the Master Skill folder
                dropdown — every entry hides the matching subfolder
                under server/skills/. Use when disabling a feature
                that also ships its own skill folder (e.g.
                'android_agent' so users can't see the 12 android-
                tied skills when android nodes are disabled).
                Email has no dedicated skill folder (email-tied skills
                live under productivity_agent mixed with Google
                Workspace) so no email entry is needed.
        """
        defaults = {
            "show_all": True,
            "enabled_nodes": [],
            "disabled_groups": [],
            "disabled_nodes": [],
            "disabled_credential_categories": [],
            "disabled_skill_folders": [],
        }

        if not self._config_path.exists():
            return defaults

        try:
            with self._config_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            logger.warning("Failed to parse node_allowlist.json, falling back to show_all: %s", e)
            return defaults

        def _str_list(key: str) -> List[str]:
            value = raw.get(key, [])
            if not isinstance(value, list):
                logger.warning(
                    "node_allowlist.json '%s' is not a list, treating as empty",
                    key,
                )
                return []
            return [n for n in value if isinstance(n, str)]

        enabled_nodes = _str_list("enabled_nodes")
        disabled_groups = _str_list("disabled_groups")
        disabled_nodes = _str_list("disabled_nodes")
        disabled_credential_categories = _str_list("disabled_credential_categories")
        disabled_skill_folders = _str_list("disabled_skill_folders")

        show_all = len(enabled_nodes) == 0

        return {
            "show_all": show_all,
            "enabled_nodes": enabled_nodes,
            "disabled_groups": disabled_groups,
            "disabled_nodes": disabled_nodes,
            "disabled_credential_categories": disabled_credential_categories,
            "disabled_skill_folders": disabled_skill_folders,
        }


_instance: NodeAllowlistService | None = None


def get_node_allowlist_service() -> NodeAllowlistService:
    """Return the singleton NodeAllowlistService."""
    global _instance
    if _instance is None:
        _instance = NodeAllowlistService()
    return _instance
