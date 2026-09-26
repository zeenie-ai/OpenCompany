"""Typed access to ``server/config/browser_runtime.json``.

The JSON is the single source of truth for what the Browser node downloads:
the opt-in Chrome for Testing build (version, per-platform checksum, size and
executable path), the Linux libraries that build needs, and the pinned
browser-use CLI. Installed browsers are selected by default; their minimum
supported major is also recorded here. ``_pin.py`` rewrites the download pins.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "browser_runtime.json"


@dataclass(frozen=True)
class ChromePlatform:
    key: str
    md5: str
    size_bytes: int
    exe: str


@dataclass(frozen=True)
class ChromePin:
    version: str
    min_major: int
    keep_versions: int
    url_template: str
    platforms: Dict[str, ChromePlatform]
    hosts: Dict[str, str]
    linux_packages: Tuple[str, ...]

    @property
    def major(self) -> int:
        return int(self.version.split(".", 1)[0])

    def url(self, platform_key: str) -> str:
        return self.url_template.format(version=self.version, platform=platform_key)


@dataclass(frozen=True)
class BrowserUsePin:
    spec: str
    python: str

    @property
    def version(self) -> str:
        return self.spec.split("==", 1)[1] if "==" in self.spec else ""


@dataclass(frozen=True)
class RuntimeConfig:
    chrome: ChromePin
    browser_use: BrowserUsePin


def load_config(path: Path = CONFIG_PATH) -> RuntimeConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cft = raw["chrome_for_testing"]
    platforms = {
        key: ChromePlatform(key=key, md5=str(entry.get("md5") or ""), size_bytes=int(entry.get("bytes") or 0), exe=str(entry["exe"]))
        for key, entry in cft["platforms"].items()
    }
    chrome = ChromePin(
        version=str(cft["version"]),
        min_major=int(cft["min_major"]),
        keep_versions=max(1, int(cft.get("keep_versions") or 2)),
        url_template=str(cft["url_template"]),
        platforms=platforms,
        hosts={str(k): str(v) for k, v in cft["hosts"].items()},
        linux_packages=tuple(str(p) for p in cft.get("linux_packages") or ()),
    )
    bu = raw["browser_use"]
    return RuntimeConfig(chrome=chrome, browser_use=BrowserUsePin(spec=str(bu["spec"]), python=str(bu.get("python") or "3.12")))


@lru_cache(maxsize=1)
def get_config() -> RuntimeConfig:
    return load_config()


def host_key(system: Optional[str] = None, machine: Optional[str] = None) -> str:
    """``<sys.platform>/<machine>`` in the shape the ``hosts`` map uses."""
    system = system or sys.platform
    if system.startswith("linux"):
        system = "linux"
    machine = (machine or platform.machine() or "").lower()
    return f"{system}/{machine}"


def chrome_platform_for_host(pin: ChromePin, system: Optional[str] = None, machine: Optional[str] = None) -> Optional[ChromePlatform]:
    """The Chrome for Testing build for this host, or ``None`` when there is none."""
    key = pin.hosts.get(host_key(system, machine))
    return pin.platforms.get(key) if key else None


__all__ = [
    "BrowserUsePin",
    "CONFIG_PATH",
    "ChromePin",
    "ChromePlatform",
    "RuntimeConfig",
    "chrome_platform_for_host",
    "get_config",
    "host_key",
    "load_config",
]
