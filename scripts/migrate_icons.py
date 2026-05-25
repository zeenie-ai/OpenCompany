"""One-shot migration: replace emoji `icon = "..."` literals in
backend node plugins with `icon = "lucide:<Name>"` references.

Idempotent — running again on already-migrated files is a no-op.
Skips icons that are already `asset:`, `lobehub:`, or `lucide:` prefixed.
Decodes source-form unicode escapes (e.g. ``"\\U0001F9E0"`` for 🧠)
before mapping so escape-encoded files migrate the same as literal-emoji
files.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODES_DIR = ROOT / "server" / "nodes"

# emoji literal -> lucide-react PascalCase icon name
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
}

ICON_PATTERN = re.compile(r'^(?P<indent>\s*)icon\s*=\s*"(?P<value>[^"]+)"\s*$')


def lucide_name_for(literal_value: str) -> str | None:
    """Resolve a python-source string literal value (already decoded
    by ast.literal_eval) to a lucide name, or None when it is already
    a library/asset ref or unmappable."""
    if not literal_value:
        return None
    if literal_value.startswith(("asset:", "lobehub:", "lucide:")):
        return None
    return EMOJI_TO_LUCIDE.get(literal_value)


def migrate_file(path: Path) -> tuple[bool, str | None]:
    """Returns (changed, lucide_name_used_or_skip_reason)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    changed = False
    used: str | None = None

    for line in lines:
        m = ICON_PATTERN.match(line)
        if not m:
            new_lines.append(line)
            continue
        # Decode source-form escapes (e.g. \U0001F9E0 -> 🧠)
        try:
            decoded = ast.literal_eval(f'"{m.group("value")}"')
        except (SyntaxError, ValueError):
            decoded = m.group("value")
        lucide = lucide_name_for(decoded)
        if lucide is None:
            new_lines.append(line)
            continue
        new_lines.append(f'{m.group("indent")}icon = "lucide:{lucide}"\n')
        changed = True
        used = lucide

    if changed:
        path.write_text("".join(new_lines), encoding="utf-8")
    return changed, used


def main() -> int:
    py_files = sorted(NODES_DIR.rglob("*.py"))
    migrated = 0
    skipped = 0
    for f in py_files:
        if f.name == "__init__.py":
            continue
        changed, used = migrate_file(f)
        rel = f.relative_to(ROOT)
        if changed:
            migrated += 1
            print(f"  migrated {rel} -> lucide:{used}")
        else:
            skipped += 1
    print(
        f"\n  {migrated} files migrated, {skipped} skipped (no emoji icon to migrate)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
