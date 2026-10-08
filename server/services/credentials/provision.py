"""Pinned official CLI installation, using the existing pooch package pattern."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

import pooch
from services.process_environment import without_onepassword_environment

from .onepassword import CLI_VERSION, CredentialSourceError

_FINGERPRINT = "3FEF9748469ADBE15DA7CA80AC2D62742012EA22"
_LOCK = asyncio.Lock()


def package_root() -> Path:
    from core.paths import package_dir
    return package_dir("onepassword") / CLI_VERSION


def managed_path() -> Path:
    return package_root() / ("op.exe" if platform.system() == "Windows" else "op")


def _run(arguments: list[str], extra_env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        env = dict(os.environ)
        env.update(extra_env or {})
        env = without_onepassword_environment(env)
        # The backend may be launched from PowerShell 7. Its module path can
        # prevent Windows PowerShell 5.1 from loading its signature cmdlet.
        # Let the selected verifier use its own built-in module locations.
        env = {key: value for key, value in env.items() if key.upper() != "PSMODULEPATH"}
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise CredentialSourceError("verification_failed", "The CLI signature verification tool is unavailable or timed out.") from None
    if result.returncode:
        raise CredentialSourceError("verification_failed", "The CLI publisher signature could not be verified.")
    return result


def verify_publisher(binary: Path, *, fetch_key: bool = False) -> None:
    """Never execute an unverified downloaded binary, even for --version."""
    if platform.system() == "Windows":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise CredentialSourceError("verification_failed", "PowerShell is required to verify the CLI publisher signature.")
        # Fixed script; the path comes from a private environment field,
        # never interpolated into PowerShell source.
        script = "$s=Get-AuthenticodeSignature -LiteralPath $env:OPENCOMPANY_SIGNATURE_TARGET; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch '(?:^|,\\s*)O=(?:Agilebits(?: Inc\\.?)?|1Password Inc\\.?)(?:,|$)') { exit 1 }"
        _run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], {"OPENCOMPANY_SIGNATURE_TARGET": str(binary)})
    elif platform.system() == "Darwin":
        _run(["/usr/bin/codesign", "--verify", "--strict", "--test-requirement", 'anchor apple generic and certificate leaf[subject.OU] = "2BUA8C4S2C"', str(binary)])
    else:
        gpg = shutil.which("gpg")
        signature = binary.with_name("op.sig")
        if not gpg or not signature.is_file():
            raise CredentialSourceError("verification_failed", "GnuPG and the official op.sig are required to verify this CLI binary.")
        keyring = package_root() / "verification-keyring"
        keyring.mkdir(parents=True, exist_ok=True)
        keyring.chmod(stat.S_IRWXU)
        if fetch_key:
            _run([gpg, "--batch", "--homedir", str(keyring), "--keyserver", "hkps://keyserver.ubuntu.com", "--recv-keys", _FINGERPRINT])
        result = _run([gpg, "--batch", "--homedir", str(keyring), "--status-fd", "1", "--verify", str(signature), str(binary)])
        valid = [line.split() for line in result.stdout.decode("ascii", errors="ignore").splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
        if not any(parts[2] == _FINGERPRINT or parts[-1] == _FINGERPRINT for parts in valid):
            raise CredentialSourceError("verification_failed", "The CLI was not signed by the approved 1Password publisher key.")


def verify_install(binary: Path) -> None:
    manifest = package_root() / "verified.json"
    if binary.resolve() == managed_path().resolve() and manifest.is_file():
        try:
            metadata = json.loads(manifest.read_text())
            if metadata["version"] == CLI_VERSION and hashlib.sha256(binary.read_bytes()).hexdigest() == metadata["sha256"]:
                return
        except (KeyError, ValueError, OSError):
            pass
    verify_publisher(binary)


def _fetch() -> Path:
    system = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"AMD64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64", "x86": "386"}.get(platform.machine())
    if not system or not arch or (system == "windows" and arch == "arm64"):
        raise CredentialSourceError("unsupported_platform", "No approved CLI archive is configured for this platform.")
    root = package_root()
    root.mkdir(parents=True, exist_ok=True)
    name = f"op_{system}_{arch}_v{CLI_VERSION}.zip"
    extracted = pooch.retrieve(url=f"https://cache.agilebits.com/dist/1P/op2/pkg/v{CLI_VERSION}/{name}", known_hash=None, path=root, fname=name, processor=pooch.Unzip(), downloader=pooch.HTTPDownloader(timeout=120))
    binary_name = managed_path().name
    source = next((Path(path) for path in extracted if Path(path).name == binary_name), None)
    if source is None:
        raise CredentialSourceError("invalid_archive", "The official CLI archive did not contain the expected executable.")
    verify_publisher(source, fetch_key=True)
    target = managed_path()
    shutil.copy2(source, target)
    signature = source.with_name("op.sig")
    if signature.exists():
        shutil.copy2(signature, target.with_name("op.sig"))
    if system != "windows":
        target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    (root / "verified.json").write_text(json.dumps({"version": CLI_VERSION, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}))
    return target


async def ensure_cli() -> Path:
    async with _LOCK:
        target = managed_path()
        if target.is_file():
            await asyncio.to_thread(verify_install, target)
            return target
        try:
            return await asyncio.to_thread(_fetch)
        except CredentialSourceError:
            raise
        except Exception:
            raise CredentialSourceError("install_failed", "The official CLI could not be provisioned. Check network and publisher verification prerequisites.") from None
