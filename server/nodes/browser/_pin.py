"""Bump the Browser node's runtime pins in ``server/config/browser_runtime.json``.

Run from ``server/``::

    uv run python -m nodes.browser._pin                  # latest Stable Chrome for Testing
    uv run python -m nodes.browser._pin --version 154.0.8037.57
    uv run python -m nodes.browser._pin --browser-use 0.13.10

Chrome for Testing publishes no checksums of its own, so the pin records the
object size and MD5 that Google Cloud Storage reports for each zip
(``Content-Length`` and ``x-goog-hash``) from a ``HEAD`` request: nothing is
downloaded. The installer then refuses a download whose MD5 differs, which
catches corruption and a replaced object alike (a second preimage for MD5 is
still out of reach). Review the diff this writes like any other pin bump.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from ._config import CONFIG_PATH

LAST_KNOWN_GOOD = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
KNOWN_GOOD = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"

_EXE = {
    "linux-arm64": "chrome-linux-arm64/chrome",
    "linux64": "chrome-linux64/chrome",
    "mac-arm64": "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "mac-x64": "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "win32": "chrome-win32/chrome.exe",
    "win64": "chrome-win64/chrome.exe",
}


def _downloads_for(client: httpx.Client, *, channel: str, version: Optional[str]) -> tuple[str, Dict[str, str]]:
    if version:
        data = client.get(KNOWN_GOOD).raise_for_status().json()
        entry = next((v for v in data["versions"] if v["version"] == version), None)
        if entry is None:
            raise SystemExit(f"Chrome for Testing has no build {version}")
    else:
        data = client.get(LAST_KNOWN_GOOD).raise_for_status().json()
        entry = data["channels"][channel]
    downloads = {d["platform"]: d["url"] for d in entry.get("downloads", {}).get("chrome", [])}
    if not downloads:
        raise SystemExit(f"Chrome for Testing {entry['version']} has no chrome downloads")
    return entry["version"], downloads


def md5_from_goog_hash(header: str) -> str:
    """Hex MD5 out of an ``x-goog-hash`` header (``crc32c=..,md5=<base64>``)."""
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "md5" and value:
            return base64.b64decode(value).hex()
    return ""


def _object_facts(client: httpx.Client, url: str) -> tuple[str, int]:
    response = client.head(url).raise_for_status()
    md5 = md5_from_goog_hash(response.headers.get("x-goog-hash", ""))
    size = int(response.headers.get("content-length") or 0)
    if not md5 or not size:
        raise SystemExit(f"{url} reported no MD5 or size; refusing to pin it")
    return md5, size


def bump(config: Dict[str, Any], *, channel: str, version: Optional[str], browser_use: Optional[str]) -> tuple[Dict[str, Any], list[str]]:
    """The updated config and a line per platform for the operator."""
    cft = config["chrome_for_testing"]
    report: list[str] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resolved, downloads = _downloads_for(client, channel=channel, version=version)
        major = int(resolved.split(".", 1)[0])
        if major < int(cft["min_major"]):
            raise SystemExit(f"{resolved} is older than min_major {cft['min_major']} (WebMCP needs it)")
        platforms: Dict[str, Any] = {}
        for key, exe in _EXE.items():
            url = downloads.get(key)
            if not url:
                report.append(f"  {key}: not published for {resolved}, dropped")
                continue
            md5, size = _object_facts(client, url)
            platforms[key] = {"md5": md5, "bytes": size, "exe": exe}
            report.append(f"  {key}: {size / 1e6:.1f} MB md5={md5}")
    cft["version"] = resolved
    cft["platforms"] = platforms
    if browser_use:
        config["browser_use"]["spec"] = f"browser-use=={browser_use}"
    return config, report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channel", default="Stable", choices=["Stable", "Beta", "Dev", "Canary"])
    parser.add_argument("--version", help="an exact Chrome for Testing version instead of the channel's latest")
    parser.add_argument("--browser-use", dest="browser_use", help="pin browser-use to this version")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    updated, report = bump(config, channel=args.channel, version=args.version, browser_use=args.browser_use)
    args.config.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    for line in report:
        print(line)
    print(f"Pinned Chrome for Testing {updated['chrome_for_testing']['version']} and {updated['browser_use']['spec']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
