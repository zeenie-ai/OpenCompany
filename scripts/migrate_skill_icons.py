"""Migrate SKILL.md frontmatter `icon: "..."` emoji literals to
`lucide:<Name>` (or `asset:<key>` for branded language skills).

YAML uses the same double-quoted escape syntax Python does, so we can
decode `"\\U0001F4BB"` to `💻` via ast.literal_eval before resolving the
mapping. Idempotent — re-running on already-migrated files is a no-op.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "server" / "skills"

# Branded language skills get the same asset:* SVGs the corresponding
# code executor nodes already use, so palette and parameter panel
# render the same brand glyph for both.
ASSET_OVERRIDES: dict[str, str] = {
    "python-skill": "asset:python",
    "javascript-skill": "asset:javascript",
    "typescript-skill": "asset:typescript",
}

EMOJI_TO_LUCIDE: dict[str, str] = {
    "🧠": "Brain",
    "🔍": "Search",
    "📱": "Smartphone",
    "📋": "ClipboardList",
    "🔧": "Wrench",
    "📝": "FileText",
    "📊": "BarChart3",
    "📄": "FileType",
    "💻": "Laptop",
    "🎯": "Target",
    "🌐": "Globe",
    "✈️": "Plane",
    "⚙️": "Settings",
    "⏰": "AlarmClock",
    "🪝": "Anchor",
    "🧞": "Sparkles",
    "🤖": "Bot",
    "🛡": "Shield",
    "🛡️": "Shield",
    "🛒": "ShoppingCart",
    "🚀": "Rocket",
    "🗺️": "Map",
    "🗄️": "Database",
    "🖥️": "Monitor",
    "🕷": "Bug",
    "🕷️": "Bug",
    "🕐": "Clock",
    "🔵": "Circle",
    "🔢": "Calculator",
    "🔋": "Battery",
    "🔊": "Volume2",
    "📷": "Camera",
    "📶": "Signal",
    "📳": "Vibrate",
    "📨": "Mailbox",
    "📡": "Antenna",
    "📍": "MapPin",
    "📁": "Folder",
    "💳": "CreditCard",
    "💡": "Lightbulb",
    "👥": "Users",
    "🎼": "Music2",
    "🎵": "Music",
    "🌡️": "Thermometer",
    "🌡": "Thermometer",
    "🌍": "Earth",
    "⬇️": "Download",
    "✏️": "Edit",
    "✂️": "Scissors",
    "▶": "Play",
    "▶️": "Play",
    "⏱️": "Timer",
    "⏱": "Timer",
    "↩️": "CornerDownLeft",
    # Skill-only additions
    "📈": "TrendingUp",
    "✨": "Sparkles",
    "📦": "Package",
    "🔄": "RotateCw",
    "🔀": "Shuffle",
    "📜": "Scroll",
    "🐧": "Terminal",  # bash skill (Linux penguin -> terminal)
    "🐍": "Code2",  # python skill default; ASSET_OVERRIDES wins
    "📅": "Calendar",
    # User-typed library identifier without the prefix:
    "brain": "Brain",
}

# YAML icon line: two-space indent inside metadata block,
# value in double quotes.
ICON_PATTERN = re.compile(r'^(?P<indent>\s+)icon:\s*"(?P<value>[^"]*)"\s*$')


def resolve_target(skill_dir_name: str, decoded_value: str) -> str | None:
    if not decoded_value:
        return None
    if decoded_value.startswith(("asset:", "lobehub:", "lucide:")):
        return None
    asset = ASSET_OVERRIDES.get(skill_dir_name)
    if asset:
        return asset
    name = EMOJI_TO_LUCIDE.get(decoded_value)
    return f"lucide:{name}" if name else None


def migrate_file(path: Path) -> tuple[bool, str | None]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    changed = False
    used: str | None = None
    skill_dir_name = path.parent.name

    for line in lines:
        m = ICON_PATTERN.match(line)
        if not m:
            new_lines.append(line)
            continue
        try:
            decoded = ast.literal_eval(f'"{m.group("value")}"')
        except (SyntaxError, ValueError):
            decoded = m.group("value")
        target = resolve_target(skill_dir_name, decoded)
        if target is None:
            new_lines.append(line)
            continue
        new_lines.append(f'{m.group("indent")}icon: "{target}"\n')
        changed = True
        used = target

    if changed:
        path.write_text("".join(new_lines), encoding="utf-8")
    return changed, used


def main() -> int:
    files = sorted(SKILLS_DIR.rglob("SKILL.md"))
    migrated = 0
    skipped = 0
    unmapped: list[tuple[Path, str]] = []
    for f in files:
        # Detect any unmapped emoji icons before we ignore them.
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = ICON_PATTERN.match(line)
            if not m:
                continue
            try:
                decoded = ast.literal_eval(f'"{m.group("value")}"')
            except (SyntaxError, ValueError):
                decoded = m.group("value")
            if decoded.startswith(("asset:", "lobehub:", "lucide:")):
                continue
            if f.parent.name not in ASSET_OVERRIDES and decoded not in EMOJI_TO_LUCIDE:
                unmapped.append((f, decoded))

        changed, used = migrate_file(f)
        rel = f.relative_to(ROOT)
        if changed:
            migrated += 1
            print(f"  migrated {rel} -> {used}")
        else:
            skipped += 1

    print(
        f"\n  {migrated} files migrated, {skipped} skipped (no emoji icon to migrate)."
    )
    if unmapped:
        print("\nUNMAPPED (please add to mapping):")
        for f, val in unmapped:
            print(f"  {f.relative_to(ROOT)} -> {val!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
