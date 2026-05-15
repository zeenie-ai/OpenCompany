"""``machina version sync`` -- replaces ``scripts/sync-version.js``.

Reads the latest git tag (or one supplied on the command line), strips
the ``v`` prefix, and writes the resulting semver into both
``package.json`` files (root + client).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer

from cli.colors import console
from cli.platform_ import project_root
from cli.run import capture


app = typer.Typer(
    name="version",
    help="Version-management subcommands.",
    no_args_is_help=True,
    add_completion=False,
)


_VALID_VERSION = re.compile(r"^\d+\.\d+\.\d+")


def _git_describe(root: Path) -> str | None:
    """``git describe --tags --abbrev=0``; falls back to sorted ``git tag -l``."""
    described = capture(["git", "describe", "--tags", "--abbrev=0"], cwd=root)
    if described:
        return described
    listed = capture(["git", "tag", "-l", "--sort=-version:refname"], cwd=root)
    if listed:
        return next((line.strip() for line in listed.splitlines() if line.strip()), None)
    return None


def _tag_to_version(tag: str) -> str:
    return tag.lstrip("v")


def _update_package_json(path: Path, new_version: str) -> bool:
    pkg = json.loads(path.read_text(encoding="utf-8"))
    old = pkg.get("version")
    if old == new_version:
        console.print(f"  {path}: already at {new_version}")
        return False
    pkg["version"] = new_version
    # Match the existing 2-space indent + trailing newline of the JS writer.
    path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    console.print(f"  {path}: {old} -> {new_version}")
    return True


@app.command("sync", help="Sync package.json versions from latest git tag.")
def sync(tag: str | None = typer.Argument(None, help="Git tag to use (defaults to latest).")) -> None:
    root = project_root()
    resolved_tag = tag or _git_describe(root)
    if not resolved_tag:
        console.print("[red]Error: No git tags found and no tag provided.[/]")
        raise typer.Exit(code=1)

    version = _tag_to_version(resolved_tag)
    if not _VALID_VERSION.match(version):
        console.print(f"[red]Error: Invalid version from tag {resolved_tag!r}: {version!r}[/]")
        console.print("Expected semver format (e.g. v0.0.11 or 0.0.11).")
        raise typer.Exit(code=1)

    console.print(f"Syncing version from tag: {resolved_tag} -> {version}\n")

    updated = 0
    for pkg_path in (root / "package.json", root / "client" / "package.json"):
        try:
            if _update_package_json(pkg_path, version):
                updated += 1
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]  Error updating {pkg_path}: {exc}[/]")

    console.print()
    if updated:
        console.print(f"Updated {updated} file(s). Don't forget to commit the changes.")
    else:
        console.print("All package.json files already at correct version.")
