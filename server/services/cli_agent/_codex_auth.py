"""Codex-owned authentication in OpenCompany's managed CLI directory.

The CLI owns credentials. Only a status exit code and short-lived login
instructions cross this boundary; authentication output is never logged.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from typing import Any, Callable, Dict
from urllib.parse import urlsplit

from core.paths import data_path
from services._supervisor.util import kill_tree
from services.events.cli import run_cli_command
from services.process_environment import without_onepassword_environment

OPENCOMPANY_CODEX_DIR = data_path("codex")
LOGIN_TIMEOUT_SECONDS = 600.0
ONESHOT_TIMEOUT_SECONDS = 30.0
_install_lock = threading.Lock()
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CLI_UNPREPARED_ERROR = "The managed Codex CLI is missing or needs an update. Click Login to prepare it, then try again."


def codex_installed_binary_path() -> str | None:
    """Return an existing pinned managed CLI without installing or waiting.

    Authentication probes must stay independent of the package install lock:
    preparing a missing or outdated CLI belongs to the owned login task.
    """
    from core.js_runtime import installed_version, shared_tree_bin
    from services.cli_agent.config import get_provider_config

    cfg = get_provider_config("codex")
    if cfg is None or not cfg.package_version:
        return None
    binary = shared_tree_bin(cfg.binary_name)
    if binary.is_file() and installed_version(cfg.package_name) == cfg.package_version:
        return str(binary)
    return None


def codex_binary_path() -> str:
    """Resolve the pinned shared-tree CLI, installing/updating it on demand."""
    from core.js_runtime import add_package, installed_version, shared_tree_bin
    from services.cli_agent.config import get_provider_config

    cfg = get_provider_config("codex")
    if cfg is None or not cfg.package_version:
        raise RuntimeError("Codex CLI package version is not configured.")
    binary = shared_tree_bin(cfg.binary_name)
    with _install_lock:
        if binary.is_file() and installed_version(cfg.package_name) == cfg.package_version:
            return str(binary)
        result = add_package(f"{cfg.package_name}@{cfg.package_version}")
        if result.returncode != 0 or not binary.is_file():
            raise RuntimeError("Could not install the managed Codex CLI. Check Bun and registry connectivity.")
    return str(binary)


def _codex_env() -> Dict[str, str]:
    """The same auth environment is used by login, status, logout and exec."""
    OPENCOMPANY_CODEX_DIR.mkdir(parents=True, exist_ok=True)
    return without_onepassword_environment({**os.environ, "CODEX_HOME": str(OPENCOMPANY_CODEX_DIR)})


async def _run_auth(argv: list[str]) -> Dict[str, Any]:
    try:
        binary = await asyncio.to_thread(codex_installed_binary_path)
        if binary is None:
            return {"success": False, "cli_missing": True, "error": _CLI_UNPREPARED_ERROR}
        OPENCOMPANY_CODEX_DIR.mkdir(parents=True, exist_ok=True)
        return await run_cli_command(binary=binary, argv=argv, env=_codex_env(), timeout=ONESHOT_TIMEOUT_SECONDS)
    except (OSError, RuntimeError):
        return {"success": False, "error": "The managed Codex CLI could not be started."}


async def codex_auth_status_info() -> Dict[str, Any]:
    envelope = await _run_auth(["login", "status"])
    if envelope.get("cli_missing"):
        return {"logged_in": False, "error": _CLI_UNPREPARED_ERROR}
    if envelope.get("success"):
        return {"logged_in": True}
    # Codex writes both successful status and signed-out status to stderr.
    # Do not infer success from an output string or expose credential details.
    output = f"{envelope.get('stdout', '')}\n{envelope.get('stderr', '')}".lower()
    if "not logged in" in output:
        return {"logged_in": False}
    return {"logged_in": False, "error": "Could not check Codex authentication. Check the managed CLI installation and try again."}


async def codex_auth_logout() -> bool:
    return bool((await _run_auth(["logout"])).get("success"))


def _login_instructions(text: str, *, device_auth: bool) -> Dict[str, str]:
    """Extract only official browser instructions, never arbitrary CLI output."""
    clean = _ANSI.sub("", text)
    result: Dict[str, str] = {}
    for value in re.findall(r"https://[^\s<>\"']+", clean):
        value = value.rstrip(".,)")
        parsed = urlsplit(value)
        if parsed.hostname == "auth.openai.com" and parsed.path.startswith(("/oauth/authorize", "/codex/device", "/device")):
            result["url"] = value
            break
    if device_auth:
        code = re.search(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,5}\b", clean)
        if code:
            result["verification_code"] = code.group(0)
    return result


async def run_codex_login(*, on_prompt: Callable[[Dict[str, str]], None], device_auth: bool = False) -> Dict[str, Any]:
    """Stream browser/device instructions while retaining ownership of the CLI."""
    proc = None
    readers: list[asyncio.Task[None]] = []
    try:
        binary = await asyncio.to_thread(codex_binary_path)
        OPENCOMPANY_CODEX_DIR.mkdir(parents=True, exist_ok=True)
        argv = [binary, "login", *(["--device-auth"] if device_auth else [])]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=_codex_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read(stream: asyncio.StreamReader) -> None:
            # Wait for a complete line (or EOF) before publishing a URL;
            # OS pipe chunks can split an authorization URL at any byte.
            pending = b""
            while chunk := await stream.read(4096):
                pending += chunk
                if len(pending) > 16384:
                    raise RuntimeError("Codex login instruction exceeded the output limit")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    prompt = _login_instructions(line.decode(errors="replace"), device_auth=device_auth)
                    if prompt:
                        on_prompt(prompt)
            if pending:
                prompt = _login_instructions(pending.decode(errors="replace"), device_auth=device_auth)
                if prompt:
                    on_prompt(prompt)

        readers = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr))]
        async with asyncio.timeout(LOGIN_TIMEOUT_SECONDS):
            await asyncio.gather(*readers)
            code = await proc.wait()
        return {
            "success": code == 0,
            "error": None if code == 0 else "Codex sign-in failed. Try again, or use a device code for a remote server.",
        }
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return {"success": False, "error": "Codex sign-in timed out. Start sign-in again."}
    except (OSError, RuntimeError):
        return {"success": False, "error": "Codex sign-in could not start. Check the managed CLI installation and try again."}
    finally:
        if proc is not None and proc.returncode is None:
            kill_tree(proc.pid)
            await proc.wait()
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
