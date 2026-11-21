"""Skill loader service for discovering and loading Agent Skills.

Implements the Agent Skills specification (https://agentskills.io/specification).
Skills are modular capabilities defined in Markdown files that Zeenie
can discover and use on demand.
"""

import asyncio
import json
import re
import threading
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from core.logging import get_logger

logger = get_logger(__name__)


def _default_skill_dirs() -> List[Path]:
    """Built-ins plus legacy and canonical project skill directories."""

    server_dir = Path(__file__).parent.parent
    cwd = Path.cwd()
    # Later directories win in scan_skills(), so canonical project skills
    # override a same-named legacy skill during the transition.
    return [
        server_dir / "skills",
        cwd / ".machina" / "skills",
        cwd / ".opencompany" / "skills",
    ]


@dataclass
class SkillMetadata:
    """Metadata from SKILL.md frontmatter (loaded at startup for all skills)."""

    name: str
    description: str
    allowed_tools: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None  # Path to skill directory


@dataclass
class Skill:
    """Full skill content (loaded on-demand when activated)."""

    metadata: SkillMetadata
    instructions: str  # Markdown body after frontmatter
    scripts: Dict[str, str] = field(default_factory=dict)  # filename -> content
    references: Dict[str, str] = field(default_factory=dict)  # filename -> content
    assets: Dict[str, bytes] = field(default_factory=dict)  # filename -> binary content


def _user_skill_metadata(row: Dict[str, Any], name: str) -> SkillMetadata:
    """Read the public row returned by the database's user-skill methods."""
    allowed_tools = row.get("allowed_tools") or []
    if isinstance(allowed_tools, str):
        allowed_tools = [tool.strip() for tool in allowed_tools.split(",") if tool.strip()]
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return SkillMetadata(name=name, description=row.get("description", ""), allowed_tools=allowed_tools, metadata=metadata)


class SkillLoader:
    """Loads and manages Agent Skills from filesystem and database.

    Skills are loaded from multiple directories with priority:
    1. Built-in skills: server/skills/
    2. Project skills: .opencompany/skills/ (legacy .machina/skills is
       scanned at lower priority)
    3. User skills from database (created via UI)

    Follows progressive disclosure pattern:
    - scan_skills(): Load only metadata (~100 tokens per skill)
    - load_skill(): Load full content when activated (~5000 tokens max)
    """

    def __init__(self, skill_dirs: List[Path] = None, database=None):
        """Initialize skill loader.

        Args:
            skill_dirs: List of directories to scan for skills
            database: Database instance for loading user-created skills
        """
        self._skill_dirs = [folder.resolve() for folder in (skill_dirs or [])]
        self._database = database
        self._registry: Dict[str, SkillMetadata] = {}
        self._files_by_path: Dict[Path, SkillMetadata] = {}
        self._database_overrides: Dict[str, SkillMetadata] = {}
        self._cache: Dict[str, Skill] = {}  # Cache loaded skills
        self._refresh_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cache_generation = 0

    def get_registry(self) -> Dict[str, SkillMetadata]:
        """Return cached metadata; call ``scan_skills`` explicitly to refresh.

        Refreshes replace the registry rather than modifying it, so readers
        can keep using the last complete snapshot while a scan is in flight.
        """
        return dict(self._registry)

    def scan_skills(self) -> Dict[str, SkillMetadata]:
        """Scan all skill directories and load metadata.

        Returns:
            Dict mapping skill name to SkillMetadata
        """
        return self._scan_and_publish({})

    def _scan_and_publish(self, overrides: Dict[str, SkillMetadata]) -> Dict[str, SkillMetadata]:
        # Serialize filesystem refreshes without holding the state lock during
        # I/O. Metadata readers continue to see the previous complete registry.
        with self._refresh_lock:
            files = self._scan_filesystem()
            self._database_overrides = overrides
            return self._publish_files(files)

    def _directory_priority(self, path: Path) -> int:
        # Explicit imports outside the configured directories overlay them.
        return max((i for i, root in enumerate(self._skill_dirs) if path.is_relative_to(root)),
                   default=len(self._skill_dirs))

    def _publish_files(self, files: Dict[Path, SkillMetadata]) -> Dict[str, SkillMetadata]:
        # Keep shadowed sources so a folder refresh can reveal a lower-priority
        # copy even when it only scans the folder that removed its override.
        registry = {}
        for path in sorted(files, key=self._directory_priority):
            metadata = files[path]
            registry[metadata.name] = metadata
        registry.update(self._database_overrides)
        self._files_by_path = files
        with self._state_lock:
            self._registry = registry
            self._cache = {}
            self._cache_generation += 1
        return dict(registry)

    def scan_skill_folder(self, folder: Path) -> Dict[str, SkillMetadata]:
        """Refresh an explicit folder while preserving unrelated cached metadata.

        This one-off import does not change the default refresh directories.
        Async callers should offload the method with ``asyncio.to_thread``.
        """
        with self._refresh_lock:
            folder = folder.resolve()
            discovered = self._scan_directories([folder])
            retained = {path: meta for path, meta in self._files_by_path.items() if not path.is_relative_to(folder)}
            self._publish_files({**retained, **discovered})
            return {meta.name: meta for meta in discovered.values()}

    def _scan_filesystem(self) -> Dict[Path, SkillMetadata]:
        return self._scan_directories(self._skill_dirs)

    def _scan_directories(self, skill_dirs: List[Path]) -> Dict[Path, SkillMetadata]:
        registry: Dict[Path, SkillMetadata] = {}

        # Scan filesystem directories (recursive - finds SKILL.md in any subdirectory)
        for skill_dir in skill_dirs:
            if not skill_dir.exists():
                logger.debug(f"[SkillLoader] Skill directory not found: {skill_dir}")
                continue

            for skill_md in skill_dir.rglob("SKILL.md"):
                try:
                    metadata = self._parse_skill_metadata(skill_md)
                    if metadata:
                        metadata.path = skill_md.parent
                        registry[skill_md.parent] = metadata
                        logger.debug(f"[SkillLoader] Loaded skill: {metadata.name}")
                except Exception as e:
                    logger.error(f"[SkillLoader] Failed to parse {skill_md}: {e}")

        logger.info(f"[SkillLoader] Loaded {len(registry)} skills from filesystem")
        return registry

    async def scan_skills_with_database(self) -> Dict[str, SkillMetadata]:
        """Scan skills from filesystem and database.

        Returns:
            Dict mapping skill name to SkillMetadata
        """
        # Build database overrides without mutating the published registry.
        # No threading lock is held while awaiting database I/O.
        overrides: Dict[str, SkillMetadata] = {}
        if self._database:
            try:
                user_skills = await self._database.get_all_user_skills()
                for row in user_skills:
                    overrides[row["name"]] = _user_skill_metadata(row, row["name"])
                logger.info(f"[SkillLoader] Loaded {len(user_skills)} skills from database")
            except Exception as e:
                logger.error(f"[SkillLoader] Failed to load skills from database: {e}")

        return await asyncio.to_thread(self._scan_and_publish, overrides)

    def _parse_skill_metadata(self, skill_md_path: Path) -> Optional[SkillMetadata]:
        """Parse SKILL.md frontmatter to extract metadata.

        Icon and color are resolved from the target node through
        :mod:`nodes._visuals` (see the chain in the body below); the
        first entry in ``allowed-tools`` that resolves to a known node
        becomes the visual source, and its values override any
        frontmatter ``metadata.icon`` / ``metadata.color``. Only skills
        with no node target keep their inline metadata. Unlike
        ``BaseNode._metadata_dict``, this chain does not consult
        ``meta.json`` icon references (``get_plugin_icon_ref``).
        """
        content = skill_md_path.read_text(encoding="utf-8")

        # Parse YAML frontmatter (between --- markers)
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not frontmatter_match:
            logger.warning(f"[SkillLoader] No frontmatter in {skill_md_path}")
            return None

        try:
            frontmatter = yaml.safe_load(frontmatter_match.group(1))
        except yaml.YAMLError as e:
            logger.error(f"[SkillLoader] Invalid YAML in {skill_md_path}: {e}")
            return None

        # Validate required fields
        name = frontmatter.get("name")
        description = frontmatter.get("description")

        if not name or not description:
            logger.warning(f"[SkillLoader] Missing name or description in {skill_md_path}")
            return None

        # Validate name format (lowercase, hyphens, no consecutive hyphens)
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
            logger.warning(f"[SkillLoader] Invalid skill name format: {name}")
            return None

        # Parse allowed-tools (space-delimited)
        allowed_tools = []
        if "allowed-tools" in frontmatter:
            allowed_tools = frontmatter["allowed-tools"].split()

        # Resolve icon + color from the target node, mirroring
        # ``BaseNode._metadata_dict``'s chain so skills inherit whichever
        # icon source the teaching node uses:
        #   1. ``<plugin>/icon.svg`` or ``<plugin>/icon_<type>.svg`` →
        #      ``/api/schemas/nodes/<type>/icon`` URL (wire format
        #      consumed by frontend ``resolveIcon``)
        #   2. ``visuals.json`` entry (emoji or ``lobehub:<brand>``)
        # Color: ``<plugin>/meta.json`` first, then ``visuals.json``.
        # ``allowed-tools`` uses snake_case; node ``type`` is camelCase.
        from nodes._visuals import (
            get_color,
            get_icon,
            get_plugin_icon_path,
            get_plugin_meta,
        )

        meta_in = dict(frontmatter.get("metadata") or {})
        for tok in allowed_tools:
            cam = tok if "_" not in tok else (tok.split("_")[0] + "".join(p.title() for p in tok.split("_")[1:]))
            for candidate in (cam, tok):
                if get_plugin_icon_path(candidate) is not None:
                    icon = f"/api/schemas/nodes/{candidate}/icon"
                else:
                    icon = get_icon(candidate)
                color = get_plugin_meta(candidate, "color") or get_color(candidate)
                if icon or color:
                    if icon:
                        meta_in["icon"] = icon
                    if color:
                        meta_in["color"] = color
                    break
            else:
                continue
            break

        return SkillMetadata(
            name=name,
            description=description,
            allowed_tools=allowed_tools,
            metadata=meta_in,
        )

    def load_skill(self, name: str) -> Optional[Skill]:
        """Load full skill content by name.

        Args:
            name: Skill name to load

        Returns:
            Skill with full content or None if not found
        """
        with self._state_lock:
            if name in self._cache:
                return self._cache[name]
            metadata = self._registry.get(name)
            generation = self._cache_generation

        # Check registry (filesystem skills only - use load_skill_async for user skills)
        if metadata is None:
            logger.debug(f"[SkillLoader] Skill not in registry: {name}")
            return None

        # Database skills have no path - load from database
        if metadata.path is None:
            return self._load_skill_from_database(name, metadata)

        # Filesystem skills
        skill_path = metadata.path
        skill_md = skill_path / "SKILL.md"

        if not skill_md.exists():
            logger.error(f"[SkillLoader] SKILL.md not found for {name}")
            return None

        # Parse full content
        content = skill_md.read_text(encoding="utf-8")

        # Extract body (after frontmatter)
        frontmatter_match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
        if frontmatter_match:
            instructions = content[frontmatter_match.end() :]
        else:
            instructions = content

        # Load scripts directory
        scripts = {}
        scripts_dir = skill_path / "scripts"
        if scripts_dir.exists():
            for script_file in scripts_dir.iterdir():
                if script_file.is_file():
                    try:
                        scripts[script_file.name] = script_file.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"[SkillLoader] Failed to read script {script_file}: {e}")

        # Load references directory
        references = {}
        refs_dir = skill_path / "references"
        if refs_dir.exists():
            for ref_file in refs_dir.iterdir():
                if ref_file.is_file() and ref_file.suffix in [".md", ".txt", ".json"]:
                    try:
                        references[ref_file.name] = ref_file.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"[SkillLoader] Failed to read reference {ref_file}: {e}")

        skill = Skill(metadata=metadata, instructions=instructions, scripts=scripts, references=references)

        # Cache the loaded skill
        with self._state_lock:
            # A refresh or editor invalidation may have completed while the
            # files were being read. Never repopulate its cache with old data.
            if generation == self._cache_generation:
                self._cache[name] = skill
        logger.debug(f"[SkillLoader] Loaded full skill: {name} ({len(instructions)} chars)")

        return skill

    def _load_skill_from_database(self, name: str, metadata: SkillMetadata) -> Optional[Skill]:
        """Load skill from database (synchronous wrapper for cached data)."""
        # This should be called after scan_skills_with_database
        # The full instructions should be loaded via async method
        logger.warning(f"[SkillLoader] Database skill {name} requires async loading")
        return None

    async def load_skill_async(self, name: str) -> Optional[Skill]:
        """Load database or filesystem content without blocking the event loop.

        Args:
            name: Skill name to load

        Returns:
            Skill with full content or None if not found
        """
        with self._state_lock:
            if name in self._cache:
                return self._cache[name]
            metadata = self._registry.get(name)
            generation = self._cache_generation

        # Try filesystem first
        if metadata is not None and metadata.path is not None:
            return await asyncio.to_thread(self.load_skill, name)

        # Load from database
        if self._database:
            try:
                user_skill = await self._database.get_user_skill(name)
                if user_skill:
                    metadata = _user_skill_metadata(user_skill, user_skill.get("name", name))
                    skill = Skill(metadata=metadata, instructions=user_skill.get("instructions", ""), scripts={}, references={})

                    logger.info(f"[SkillLoader] Loaded user skill from database: {name}")
                    with self._state_lock:
                        if generation == self._cache_generation:
                            self._cache[name] = skill
                    return skill
            except Exception as e:
                logger.error(f"[SkillLoader] Failed to load skill {name} from database: {e}")

        return None

    def get_registry_prompt(self, skill_names: List[str] = None) -> str:
        """Generate skill registry for LLM system prompt.

        Args:
            skill_names: Optional list of skill names to include.
                        If None, includes all registered skills.

        Returns:
            Formatted string listing available skills
        """
        registry = self._registry
        skills_to_include = skill_names or list(registry.keys())

        if not skills_to_include:
            return ""

        lines = ["## Available Skills", ""]
        lines.append("You have access to the following skills. When a user's request matches a skill's purpose, activate it to help them.")
        lines.append("")

        for name in skills_to_include:
            if name in registry:
                metadata = registry[name]
                lines.append(f"- **{name}**: {metadata.description}")
                if metadata.allowed_tools:
                    tools_str = ", ".join(metadata.allowed_tools)
                    lines.append(f"  - Tools: {tools_str}")

        lines.append("")
        lines.append("To use a skill, identify when the user's request matches its purpose and apply the skill's instructions.")

        return "\n".join(lines)

    def get_skill_instructions(self, name: str) -> Optional[str]:
        """Get full instructions for a skill (loads if not cached).

        Args:
            name: Skill name

        Returns:
            Markdown instructions or None if not found
        """
        skill = self.load_skill(name)
        return skill.instructions if skill else None

    def get_available_skills(self) -> List[Dict[str, Any]]:
        """Get list of available skills for frontend display.

        Returns:
            List of skill info dicts
        """
        return [
            {
                "name": metadata.name,
                "description": metadata.description,
                "allowed_tools": metadata.allowed_tools,
                "metadata": metadata.metadata,
                "is_builtin": metadata.path is not None,
            }
            for metadata in self._registry.values()
        ]

    def clear_cache(self):
        """Clear the skill cache."""
        with self._state_lock:
            self._cache = {}
            self._cache_generation += 1
        logger.debug("[SkillLoader] Cache cleared")


# Global skill loader instance
_skill_loader: Optional[SkillLoader] = None
_skill_loader_lock = threading.Lock()


def _resolve_database():
    """Best-effort fetch of the DI database singleton (None if unavailable)."""
    try:
        from core.container import container

        return container.database()
    except Exception:  # container not wired yet (very early call)
        return None


def get_skill_loader() -> SkillLoader:
    """Get the global skill loader instance.

    The loader is wired with the database so ``load_skill_async`` can
    resolve user-created skills (stored in the DB, not on disk). The DB
    is late-bound if the loader happened to be created before the DI
    container was ready -- otherwise a skill created via the Master
    Skill editor stays invisible to ``get_skill_content`` and every
    agent path, and the editor reports "Skill '<name>' not found".
    """
    global _skill_loader
    with _skill_loader_lock:
        if _skill_loader is None:
            loader = SkillLoader(skill_dirs=_default_skill_dirs(), database=_resolve_database())
            loader.scan_skills()
            # Publish only after the initial metadata scan is complete.
            _skill_loader = loader
        elif _skill_loader._database is None:
            # Created before the container was wired -- bind the DB now so
            # user-skill lookups stop failing.
            _skill_loader._database = _resolve_database()
        return _skill_loader


async def get_skill_loader_async() -> SkillLoader:
    """Get the singleton without running its cold filesystem scan on the loop."""
    return await asyncio.to_thread(get_skill_loader)


def init_skill_loader(database=None) -> SkillLoader:
    """Initialize the global skill loader with database support.

    Args:
        database: Database instance for user skill storage

    Returns:
        Initialized SkillLoader
    """
    global _skill_loader
    with _skill_loader_lock:
        loader = SkillLoader(skill_dirs=_default_skill_dirs(), database=database)
        loader.scan_skills()
        _skill_loader = loader
        return loader
