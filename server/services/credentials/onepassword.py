"""Private CLI secret reader. No shell, secret arguments, logs or value cache."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

CLI_VERSION = "2.40.0"
# Vault/item IDs must be unambiguous. Field names/IDs can include a section.
_REFERENCE = re.compile(r"^op://[a-z0-9]{26}/[a-z0-9]{26}/[A-Za-z0-9_. -]+(?:/[A-Za-z0-9_. -]+)?$")
_SAFE_ENV = frozenset({"PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG", "LC_ALL"})


class CredentialSourceError(PermissionError):
    """Only fixed, safe messages may cross an execution or UI boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.reason = code
        super().__init__(message)


def validate_reference(reference: str) -> str:
    if not isinstance(reference, str) or len(reference) > 512 or not _REFERENCE.fullmatch(reference):
        raise CredentialSourceError("invalid_reference", "Use an op:// reference with vault and item IDs and an approved field; query parameters are unsupported.")
    return reference


def resolver_environment(settings) -> dict[str, str]:
    """Explicit auth mode; Connect/session/ambient API tokens are never inherited."""
    env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV}
    mode = getattr(settings, "onepassword_auth_mode", "desktop")
    if getattr(settings, "distributed_mode", False) and mode != "service_account":
        raise CredentialSourceError("unsupported_auth", "Distributed credentials require 1Password service-account authentication.")
    if mode == "service_account":
        token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN")
        if not token:
            raise CredentialSourceError("authorization_required", "The 1Password service account is not configured on this runtime.")
        env["OP_SERVICE_ACCOUNT_TOKEN"] = token
        env["OP_BIOMETRIC_UNLOCK_ENABLED"] = "false"
    elif mode == "desktop":
        env["OP_BIOMETRIC_UNLOCK_ENABLED"] = "true"
        account = getattr(settings, "onepassword_account", None)
        if account:
            env["OP_ACCOUNT"] = str(account)
    else:
        raise CredentialSourceError("unsupported_auth", "Select desktop or service-account 1Password authentication.")
    return env


async def _terminate(process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def _private_command(executable: str, args: tuple[str, ...], env: dict, timeout: float) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(executable, *args, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=65536)
    except (OSError, ValueError):
        raise CredentialSourceError("cli_unavailable", "1Password CLI is unavailable. Provision the pinned CLI before using this binding.") from None
    async def bounded_read(stream) -> bytes:
        chunks, size = [], 0
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > 65536:
                raise CredentialSourceError("invalid_value", "The credential response exceeds the supported size.")
            chunks.append(chunk)
    async def collect():
        output, error = await asyncio.gather(bounded_read(process.stdout), bounded_read(process.stderr))
        await process.wait()
        return output, error
    try:
        output, discarded_stderr = await asyncio.wait_for(collect(), timeout)
    except asyncio.TimeoutError:
        await _terminate(process)
        raise CredentialSourceError("timeout", "1Password authorization or secret resolution timed out. Try again after authorizing the runtime.") from None
    except BaseException:
        await asyncio.shield(_terminate(process))
        raise
    if process.returncode != 0:
        # CLI error streams may contain credentials or URLs. Never classify by
        # serializing their text, including on a provider validation failure.
        error_hint = discarded_stderr.decode("utf-8", errors="ignore").lower()
        if "429" in error_hint or "rate limit" in error_hint or "request limit" in error_hint:
            raise CredentialSourceError("rate_limited", "1Password service-account limits were reached. Retry after its quota recovers.")
        if "not signed in" in error_hint or "authorization" in error_hint or "biometric" in error_hint:
            raise CredentialSourceError("authorization_required", "Authorize the 1Password runtime before retrying this task.")
        raise CredentialSourceError("resolution_failed", "1Password could not resolve the approved field. Check authorization, vault access and the reference.")
    if len(output) > 65536:
        raise CredentialSourceError("invalid_value", "The credential exceeds the supported size.")
    return output


def cli_path(settings) -> str:
    from .provision import managed_path
    configured = getattr(settings, "onepassword_cli_path", None)
    managed = managed_path()
    executable = configured or (str(managed) if managed.is_file() else shutil.which("op"))
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise CredentialSourceError("cli_unavailable", "Install the pinned 1Password CLI and configure its absolute executable path.")
    return str(Path(executable).resolve())


async def doctor(settings) -> dict:
    """Integrity/version check, never signs in, lists vaults or reads a field."""
    executable = cli_path(settings)
    from .provision import verify_install
    await asyncio.to_thread(verify_install, Path(executable))
    # Version verification does not require a service token or desktop grant.
    env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV}
    version = (await _private_command(executable, ("--version",), env, 10)).decode("utf-8").strip()
    if version != CLI_VERSION or getattr(settings, "onepassword_cli_version", CLI_VERSION) != CLI_VERSION:
        raise CredentialSourceError("cli_version", f"Provision 1Password CLI {CLI_VERSION}; the installed version does not match the tested pin.")
    return {"available": True, "version": CLI_VERSION, "auth_mode": getattr(settings, "onepassword_auth_mode", "desktop")}


async def read_secret(reference: str, settings) -> str:
    validate_reference(reference)
    executable = cli_path(settings)
    await doctor(settings)
    output = await _private_command(executable, ("read", "--no-newline", reference), resolver_environment(settings), float(getattr(settings, "onepassword_read_timeout_seconds", 30)))
    try:
        value = output.decode("utf-8")
    except UnicodeError:
        raise CredentialSourceError("invalid_value", "The credential must be UTF-8 text.") from None
    if not value:
        raise CredentialSourceError("missing_value", "The approved 1Password field is empty.")
    return value
