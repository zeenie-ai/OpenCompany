"""Explicit, optional installation. Download pins are verified before extraction/execution."""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable
from ._paths import mobile_root, runtime_python
from ._process import command

UPSTREAM_SHA = "62913c933e21b27da353a89316a09f60525af496"
SOURCE_SHA256 = "e2bac1f1d47e895d8c9ec840b0e896391ffd664ed2d4149a1ff1744252ca8384"
SCRCPY_VERSION = "4.1"
SCRCPY_SHA256 = "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae"
IMAGE = "system-images;android-36;google_apis_playstore;x86_64"
AVD_NAME = "OpenCompany"
#: The phone the emulator is (avdmanager's device profile), and its name as
#: the owner sees it in the Workspace.
DEVICE_PROFILE = "pixel_7"
DEVICE_NAME = "Pixel 7"
#: The live view's frame-rate cap (scrcpy ``max_fps``).
VIDEO_MAX_FPS = 30
# Minimum required by the pinned API 36 Google Play image. Newer installed
# image revisions can raise this through source.properties Pkg.Dependencies.
MIN_EMULATOR_VERSION = (35, 4, 9)
SDK_INSTALL_TIMEOUT = 2 * 60 * 60
Progress = Callable[[str], None]
COMMAND_TOOLS_REVISION = "15859902"
COMMAND_TOOLS_SHA256 = "90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a"
JAVA_URL = (
    "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip"
)
JAVA_SHA256 = "d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636"


def engine_ready() -> bool:
    from .runtime.patch_source import PATCH_VERSION

    try:
        stamp = json.loads((mobile_root() / "engine-ready.json").read_text(encoding="utf-8"))
        return runtime_python().is_file() and stamp == {"source": UPSTREAM_SHA, "patch": PATCH_VERSION}
    except (OSError, ValueError):
        return False


def sdk_root() -> Path:
    for value in (os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME")):
        if value and Path(value).is_dir():
            return Path(value).resolve()
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"
    return default if os.name == "nt" and default.is_dir() else mobile_root() / "sdk"


def sdk_tool(name: str) -> Path | None:
    suffix = ".exe" if os.name == "nt" else ""
    if name in {"adb", "emulator"}:
        path = sdk_root() / ("platform-tools" if name == "adb" else "emulator") / (name + suffix)
        return path if path.is_file() else None
    suffix = ".bat" if os.name == "nt" else ""
    candidates = list((sdk_root() / "cmdline-tools").glob(f"*/bin/{name}{suffix}"))
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0] if candidates else None


def sdk_command(name: str, *args: str) -> list[str]:
    path = sdk_tool(name)
    if path is None:
        raise ValueError(f"Android {name} is missing. Install Android SDK command-line tools first.")
    if os.name == "nt":
        # Launch the documented Java entry points directly. Never feed paths or
        # SDK package names through cmd.exe / a batch file.
        java = next((mobile_root() / "java").glob("*/bin/java.exe"), None)
        if java is None:
            raise ValueError("Complete Mobile setup to install its private Java runtime")
        main = {"sdkmanager": "com.android.sdklib.tool.sdkmanager.SdkManagerCli", "avdmanager": "com.android.sdklib.tool.AvdManagerCli"}[
            name
        ]
        tools = path.parent.parent
        return [str(java), f"-Dcom.android.sdkmanager.toolsdir={tools}", "-classpath", str(tools / "lib" / "*"), main, *args]
    return [str(path), *args]


def download(url: str, target: Path, digest: str, *, limit: int = 512 * 1024 * 1024, progress: Progress | None = None) -> None:
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        if progress:
            progress(f"Using verified download: {target.name}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    hasher = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temp.open("wb") as output:
            size = 0
            total = int(getattr(response, "headers", {}).get("Content-Length", 0) or 0)
            last_update = 0.0
            if progress:
                progress(f"Downloading {target.name}")
            while data := response.read(256 * 1024):
                size += len(data)
                if size > limit:
                    raise ValueError("Download exceeds expected size")
                hasher.update(data)
                output.write(data)
                if progress and time.monotonic() - last_update >= 0.5:
                    suffix = f" / {total / 1024**2:.1f} MiB ({min(100, size * 100 // total)}%)" if total else " MiB"
                    progress(f"Downloading {target.name}: {size / 1024**2:.1f}{suffix}")
                    last_update = time.monotonic()
        if hasher.hexdigest() != digest:
            raise ValueError("Runtime download checksum mismatch")
        temp.replace(target)
        if progress:
            progress(f"Verified {target.name} ({size / 1024**2:.1f} MiB)")
    finally:
        temp.unlink(missing_ok=True)


async def _download(url: str, target: Path, digest: str, progress: Progress | None) -> None:
    # urllib runs off-loop. All observable installer/runtime state updates
    # still happen on the owning asyncio loop, never the downloader thread.
    loop = asyncio.get_running_loop()
    callback = (lambda message: loop.call_soon_threadsafe(progress, message)) if progress is not None else None
    await asyncio.to_thread(download, url, target, digest, progress=callback)


def _properties(path: Path) -> dict[str, str]:
    try:
        return {
            key.strip(): value.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
            for key, value in [line.split("=", 1)]
        }
    except (OSError, UnicodeError):
        return {}


def _version(value: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else ()


def sdk_package_ready(package: str) -> bool:
    """Check core package files and revision; never update a complete package blindly."""
    root = sdk_root() / Path(*package.split(";"))
    props = _properties(root / "source.properties")
    if not _version(props.get("Pkg.Revision", "")):
        return False
    if package == "platform-tools":
        files = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
    elif package == "emulator":
        image = _properties(sdk_root() / Path(*IMAGE.split(";")) / "source.properties")
        dependency = re.search(r"(?:^|[,;\s])emulator#([\d.]+)", image.get("Pkg.Dependencies", ""))
        minimum = max(MIN_EMULATOR_VERSION, _version(dependency.group(1)) if dependency else MIN_EMULATOR_VERSION)
        if _version(props["Pkg.Revision"]) < minimum:
            return False
        files = ("emulator.exe", "qemu/windows-x86_64/qemu-system-x86_64.exe")
    elif package == IMAGE:
        if (
            props.get("AndroidVersion.ApiLevel") != "36"
            or props.get("SystemImage.Abi") != "x86_64"
            or props.get("SystemImage.TagId") != "google_apis_playstore"
        ):
            return False
        files = ("system.img", "vendor.img", "ramdisk.img", "kernel-ranchu")
    else:
        raise ValueError("Unsupported managed Android package")
    return all((root / file).is_file() and (root / file).stat().st_size > 0 for file in files)


def extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        if sum(entry.file_size for entry in source.infolist()) > 2 * 1024**3:
            raise ValueError("Runtime archive is too large")
        for entry in source.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe runtime archive")
        source.extractall(destination)


async def install_engine(progress: Progress | None = None) -> None:
    from .runtime.patch_source import patch

    root = mobile_root()
    source = root / "engine"
    marker = source / ".opencompany-patch"
    from .runtime.patch_source import PATCH_VERSION

    if marker.is_file() and marker.read_text(encoding="ascii") != str(PATCH_VERSION):
        raise ValueError(
            "The installed mobile engine uses a different patch version. Stop the server and remove only DATA_DIR/mobile/engine, then run setup again. Device apps and sign-ins remain in DATA_DIR/mobile/avd."
        )
    ready = engine_ready() and marker.is_file()
    server = root / "scrcpy-server"
    if ready and server.is_file() and hashlib.sha256(server.read_bytes()).hexdigest() == SCRCPY_SHA256:
        if progress:
            progress("Mobile engine and video server are already installed")
        return
    uv = os.environ.get("OPENCOMPANY_UV_BIN") or shutil.which("uv")
    if not uv:
        raise ValueError("uv is required for optional mobile runtime setup")
    if not marker.is_file():
        archive = root / "downloads" / f"mobile-use-{UPSTREAM_SHA}.zip"
        await _download(f"https://codeload.github.com/minitap-ai/mobile-use/zip/{UPSTREAM_SHA}", archive, SOURCE_SHA256, progress)
        staging = root / "source"
        if progress:
            progress("Extracting and preparing the mobile engine")
        await asyncio.to_thread(extract_zip, archive, staging)
        extracted = staging / f"mobile-use-{UPSTREAM_SHA}"
        patch(extracted)
        if source.exists():
            raise ValueError("Incomplete mobile runtime exists; use Repair setup")
        extracted.rename(source)
    if not ready:
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(source / ".venv"), "UV_NO_CONFIG": "1"}
        env.pop("VIRTUAL_ENV", None)
        if progress:
            progress("Installing the isolated Python runtime and mobile engine dependencies")
        await command(
            [uv, "sync", "--frozen", "--no-dev", "--python", "3.12", "--project", str(source)],
            timeout=SDK_INSTALL_TIMEOUT,
            env=env,
            progress=progress,
        )
        stamp = root / "engine-ready.json.tmp"
        stamp.write_text(json.dumps({"source": UPSTREAM_SHA, "patch": PATCH_VERSION}), encoding="utf-8")
        stamp.replace(root / "engine-ready.json")
    await _download(
        f"https://github.com/Genymobile/scrcpy/releases/download/v{SCRCPY_VERSION}/scrcpy-server-v{SCRCPY_VERSION}",
        server,
        SCRCPY_SHA256,
        progress,
    )


async def doctor() -> dict:
    sdk = sdk_root()
    emulator = sdk_tool("emulator")
    try:
        acceleration = (
            await command([str(emulator), "-accel-check"], timeout=15, check=False) if emulator else "Android Emulator is not installed"
        )
    except (OSError, asyncio.TimeoutError):
        acceleration = "Unable to check acceleration. Enable Windows Hypervisor Platform and restart Windows."
    return {
        "supported": sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"},
        "platform": sys.platform,
        "sdk_root": str(sdk),
        "sdk_tools": sdk_tool("sdkmanager") is not None,
        "adb": sdk_tool("adb") is not None,
        "emulator": emulator is not None,
        "engine": engine_ready(),
        "video": (mobile_root() / "scrcpy-server").is_file(),
        "acceleration": acceleration[-1500:],
        "image": (sdk / Path(*IMAGE.split(";"))).is_dir() and (mobile_root() / "avd" / f"{AVD_NAME}.ini").is_file(),
        "image_id": IMAGE,
        "license_url": "https://developer.android.com/studio/terms",
    }


async def install_sdk_tools(progress: Progress | None = None) -> None:
    root = mobile_root()
    if not next((root / "java").glob("*/bin/java.exe"), None):
        archive = root / "downloads" / "temurin-jre.zip"
        await _download(JAVA_URL, archive, JAVA_SHA256, progress)
        if progress:
            progress("Extracting the private Java runtime")
        await asyncio.to_thread(extract_zip, archive, root / "java")
    manager = sdk_tool("sdkmanager")
    tools_ready = (
        manager is not None
        and _properties(manager.parent.parent / "source.properties")
        and all((manager.parent.parent / "lib" / name).is_file() for name in ("sdkmanager-classpath.jar", "avdmanager-classpath.jar"))
    )
    if not tools_ready:
        archive = root / "downloads" / f"commandlinetools-win-{COMMAND_TOOLS_REVISION}.zip"
        await _download(
            f"https://dl.google.com/android/repository/commandlinetools-win-{COMMAND_TOOLS_REVISION}_latest.zip",
            archive,
            COMMAND_TOOLS_SHA256,
            progress,
        )
        staging = root / "sdk-staging"
        if progress:
            progress("Extracting Android SDK command-line tools")
        await asyncio.to_thread(extract_zip, archive, staging)
        target = sdk_root() / "cmdline-tools" / COMMAND_TOOLS_REVISION
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Incomplete SDK tools exist; remove that incomplete revision before retrying setup")
        (staging / "cmdline-tools").rename(target)
    elif progress:
        progress("Android SDK command-line tools are already installed")


async def create_device(*, licenses_accepted: bool, progress: Progress | None = None) -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("This release supports managed Android setup on Windows x64 only")
    if not licenses_accepted:
        raise ValueError("Accept the Android SDK license in setup before installing packages")
    await install_sdk_tools(progress=progress)
    args = [f"--sdk_root={sdk_root()}"]
    # Explicit user acceptance above is the only path allowed to answer these prompts.
    # Accept only the packages selected below, not every unrelated pending SDK
    # license in a shared Android Studio installation.
    # Install the image before checking emulator compatibility: its package
    # metadata can demand a newer emulator than the baseline minimum.
    for package in ("platform-tools", IMAGE, "emulator"):
        if sdk_package_ready(package):
            if progress:
                progress(f"Already installed: {package}")
            continue
        if progress:
            progress(f"Installing Android package: {package}. Large images may take a while on slower connections.")
        await command(sdk_command("sdkmanager", *args, package), timeout=SDK_INSTALL_TIMEOUT, input_text="y\n" * 100, progress=progress)
        if not sdk_package_ready(package):
            raise ValueError(f"Android package {package} did not finish installing. Retry setup to repair the incomplete package.")
    avd_home = mobile_root() / "avd"
    avd_home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ANDROID_AVD_HOME": str(avd_home), "ANDROID_SDK_ROOT": str(sdk_root())}
    if not (avd_home / f"{AVD_NAME}.ini").is_file():
        if progress:
            progress("Creating the persistent OpenCompany Android device")
        await command(
            sdk_command("avdmanager", "create", "avd", "--name", AVD_NAME, "--package", IMAGE, "--device", DEVICE_PROFILE),
            timeout=600,
            env=env,
            input_text="no\n",
            progress=progress,
        )
    elif progress:
        progress("Reusing the existing Android device, apps, and sign-ins")
    (mobile_root() / "resource.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": AVD_NAME,
                "platform": "android",
                "image": IMAGE,
                "sdk_root": str(sdk_root()),
                "upstream_sha": UPSTREAM_SHA,
            }
        ),
        encoding="utf-8",
    )
    if progress:
        progress("Android setup complete. The device is ready to start.")
