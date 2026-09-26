"""System provider selection and startup guards; never launch an installed browser."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nodes.browser import _system_browser as system
from nodes.browser._install_chrome import ChromeInstaller
from services.plugin.base import NodeUserError


def settings(monkeypatch, **values):
    from core.container import container

    result = SimpleNamespace(browser_runtime="system", browser_family="auto", browser_chrome_path="", browser_install_timeout_seconds=10)
    result.__dict__.update(values)
    monkeypatch.setattr(container, "settings", lambda: result)
    return result


def test_explicit_browser_wins_and_invalid_override_never_falls_back(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda p: str(p) == "chosen.exe")
    monkeypatch.setattr(system, "_candidates", Mock(side_effect=AssertionError("must not discover")))
    assert system.discover_browser("chosen.exe") == (Path("chosen.exe"), "override")
    with pytest.raises(NodeUserError, match="does not exist"):
        system.discover_browser("missing.exe")


def test_discovery_order_and_missing_message(monkeypatch):
    monkeypatch.setattr(system, "_candidates", lambda: [(Path("chrome"), "chrome"), (Path("edge"), "edge"), (Path("chromium"), "chromium")])
    monkeypatch.setattr(Path, "is_file", lambda p: p.name in {"edge", "chromium"})
    assert system.discover_browser() == (Path("edge"), "edge")
    monkeypatch.setattr(Path, "is_file", lambda p: False)
    with pytest.raises(NodeUserError, match="BROWSER_RUNTIME=testing"):
        system.discover_browser()


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_platform_candidates_prefer_chrome_then_edge_then_chromium(monkeypatch, platform):
    monkeypatch.setattr(system.sys, "platform", platform)
    monkeypatch.setenv("PROGRAMFILES", "Programs")
    monkeypatch.setenv("PROGRAMFILES(X86)", "Programs86")
    monkeypatch.setenv("LOCALAPPDATA", "Local")
    monkeypatch.setattr(system.shutil, "which", lambda command: command)
    monkeypatch.setattr(system, "_windows_app_paths", lambda command: [])
    names = [name for _, name in system._candidates()]
    assert names and names == sorted(names, key={"chrome": 0, "edge": 1, "chromium": 2}.get)


def test_windows_registry_custom_install_precedes_path_and_defaults(monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "win32")
    monkeypatch.setattr(system, "_windows_app_paths", lambda command: [Path("relocated") / command])
    monkeypatch.setattr(system.shutil, "which", lambda command: str(Path("on-path") / command))
    monkeypatch.setattr(Path, "is_file", lambda path: "relocated" in path.parts or "on-path" in path.parts)
    assert system.discover_browser() == (Path("relocated/chrome.exe"), "chrome")
    monkeypatch.setattr(Path, "is_file", lambda path: "on-path" in path.parts)
    assert system.discover_browser() == (Path("on-path/chrome.exe"), "chrome")


def test_windows_registry_reads_both_scopes_and_views(monkeypatch):
    import sys
    from contextlib import nullcontext

    calls = []

    def open_key(hive, key, reserved, access):
        calls.append((hive, key, access))
        if hive == "machine" and access == 5:
            return nullcontext("registered")
        raise FileNotFoundError

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="machine", KEY_READ=1,
        KEY_WOW64_64KEY=2, KEY_WOW64_32KEY=4, REG_SZ=1, REG_EXPAND_SZ=2,
        OpenKey=open_key, QueryValueEx=lambda key, value: ('"%INSTALL_ROOT%/chrome.exe"', 2),
        ExpandEnvironmentStrings=lambda value: value.replace("%INSTALL_ROOT%", "custom-location"),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert list(system._windows_app_paths("chrome.exe")) == [Path("custom-location/chrome.exe")]
    assert [(hive, access) for hive, _, access in calls] == [("user", 3), ("user", 5), ("machine", 3), ("machine", 5)]
    assert all(key.endswith(r"App Paths\chrome.exe") for _, key, _ in calls)


@pytest.mark.asyncio
async def test_named_chrome_never_switches_to_edge(monkeypatch):
    settings(monkeypatch, browser_family="chrome")
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("chrome"), "chrome"), (Path("edge"), "edge")])
    probe = AsyncMock(side_effect=lambda path: "153.0.0.0" if path.name == "chrome" else "154.0.0.0")
    monkeypatch.setattr(system, "browser_version", probe)
    with pytest.raises(NodeUserError, match="Installed chrome.*requires version 154"):
        await ChromeInstaller().ensure(wait=10, min_major=154)
    probe.assert_awaited_once_with(Path("chrome"))


@pytest.mark.asyncio
async def test_named_browser_is_rediscovered_after_relocation(monkeypatch):
    settings(monkeypatch, browser_family="chrome")
    discover = Mock(side_effect=[[(Path("old/chrome.exe"), "chrome")], [(Path("new/chrome.exe"), "chrome")]])
    monkeypatch.setattr(system, "discover_browsers", discover)
    monkeypatch.setattr(system, "browser_version", AsyncMock(return_value="154.0.0.0"))
    installer = ChromeInstaller()
    assert await installer.ensure(wait=10) == Path("old/chrome.exe")
    assert await installer.ensure(wait=10) == Path("new/chrome.exe")
    assert discover.call_count == 2


@pytest.mark.asyncio
async def test_missing_named_browser_is_actionable(monkeypatch):
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("edge"), "edge")])
    with pytest.raises(NodeUserError, match="No installed chrome was found"):
        await system.select_browser(min_major=154, family="chrome")


@pytest.mark.asyncio
async def test_windows_version_reads_metadata_without_launch(monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "win32")
    monkeypatch.setattr(system, "_windows_version", lambda path: "151.2.3.4")
    monkeypatch.setattr(system.asyncio, "create_subprocess_exec", Mock(side_effect=AssertionError("no GUI probe")))
    assert await system.browser_version(Path("chrome.exe")) == "151.2.3.4"


@pytest.mark.asyncio
async def test_posix_version_timeout_kills_and_reaps(monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "linux")
    process = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=TimeoutError), kill=Mock(), wait=AsyncMock())
    monkeypatch.setattr(system.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(TimeoutError):
        await system.browser_version(Path("chrome"))
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_provider_reports_actual_version_and_never_installs(monkeypatch):
    settings(monkeypatch)
    installer = ChromeInstaller()
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("edge"), "edge")])
    monkeypatch.setattr(system, "browser_version", AsyncMock(return_value="151.2.3.4"))
    monkeypatch.setattr(installer, "_start", AsyncMock(side_effect=AssertionError("no download")))
    assert installer.status()["version"] == ""
    assert await installer.ensure(wait=10) == Path("edge")
    assert installer.status() == {"phase": "ready", "percent": None, "version": "151.2.3.4", "error": None, "exe": "edge", "source": "edge", "provider": "system"}


@pytest.mark.asyncio
async def test_missing_system_browser_does_not_download(monkeypatch):
    settings(monkeypatch)
    installer = ChromeInstaller()
    monkeypatch.setattr(system, "discover_browsers", Mock(side_effect=NodeUserError("install a browser")))
    download = AsyncMock(side_effect=AssertionError("no fallback"))
    monkeypatch.setattr(installer, "_start", download)
    with pytest.raises(NodeUserError, match="install a browser"):
        await installer.ensure(wait=10)
    download.assert_not_called()
    assert installer.status()["phase"] == "failed"


@pytest.mark.asyncio
async def test_minimum_browser_gate(monkeypatch):
    settings(monkeypatch)
    installer = ChromeInstaller()
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("chrome"), "chrome")])
    monkeypatch.setattr(system, "browser_version", AsyncMock(return_value="100.0.0.0"))
    with pytest.raises(NodeUserError, match="requires version"):
        await installer.ensure(wait=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("required,expected", [(0, "chrome"), (154, "edge")])
async def test_selection_uses_first_browser_compatible_with_profile(monkeypatch, required, expected):
    settings(monkeypatch)
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("chrome"), "chrome"), (Path("edge"), "edge")])
    versions = {"chrome": "153.0.0.0", "edge": "154.0.0.0"}
    monkeypatch.setattr(system, "browser_version", AsyncMock(side_effect=lambda path: versions[path.name]))
    installer = ChromeInstaller()
    monkeypatch.setattr(installer, "_start", AsyncMock(side_effect=AssertionError("no download")))
    assert await installer.ensure(wait=10, min_major=required) == Path(expected)
    assert installer.state.version == versions[expected]
    assert installer.state.source == expected


@pytest.mark.asyncio
async def test_all_installed_browsers_too_old_preserves_profile(monkeypatch):
    settings(monkeypatch)
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("chrome"), "chrome"), (Path("edge"), "edge")])
    monkeypatch.setattr(system, "browser_version", AsyncMock(return_value="153.0.0.0"))
    installer = ChromeInstaller()
    with pytest.raises(NodeUserError, match="requires version 154.*profile has been preserved"):
        await installer.ensure(wait=10, min_major=154)
    assert installer.state.phase == "failed"
    assert installer.state.exe is None


@pytest.mark.asyncio
async def test_old_explicit_browser_never_switches_to_another_browser(monkeypatch):
    settings(monkeypatch, browser_chrome_path="chosen.exe")
    monkeypatch.setattr(Path, "is_file", lambda path: path.name == "chosen.exe")
    monkeypatch.setattr(system, "_candidates", Mock(side_effect=AssertionError("override is exclusive")))
    monkeypatch.setattr(system, "browser_version", AsyncMock(return_value="153.0.0.0"))
    with pytest.raises(NodeUserError, match="BROWSER_CHROME_PATH.*requires version 154"):
        await ChromeInstaller().ensure(wait=10, min_major=154)


@pytest.mark.asyncio
async def test_unreadable_candidate_does_not_hide_compatible_browser(monkeypatch):
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path("chrome"), "chrome"), (Path("edge"), "edge")])
    monkeypatch.setattr(system, "browser_version", AsyncMock(side_effect=[OSError("unreadable"), "154.0.0.0"]))
    assert await system.select_browser(min_major=154) == (Path("edge"), "edge", "154.0.0.0")


@pytest.mark.asyncio
async def test_testing_provider_is_explicit_and_override_still_wins(monkeypatch):
    config = settings(monkeypatch, browser_runtime="testing")
    installer = ChromeInstaller()
    monkeypatch.setattr(installer, "installed_exe", lambda: Path("testing"))
    monkeypatch.setattr(system, "browser_version", AsyncMock(return_value="154.0.1.2"))
    assert await installer.ensure(wait=10) == Path("testing")
    assert installer.status()["source"] == "testing"
    config.browser_chrome_path = "custom"
    monkeypatch.setattr(system, "discover_browsers", lambda raw: [(Path(raw), "override")])
    assert await installer.ensure(wait=10) == Path("custom")
    assert installer.status()["source"] == "override"


@pytest.mark.asyncio
async def test_downgrade_guard_uses_selected_browser_before_launch(monkeypatch):
    from nodes.browser import _runtime as module
    from nodes.browser._profiles import Profile

    config = settings(monkeypatch)
    runtime = module.BrowserRuntime()
    monkeypatch.setattr(runtime, "_settings", lambda: config)
    installer = SimpleNamespace(ensure=AsyncMock(return_value=Path("chrome")), state=SimpleNamespace(version="151.0.0.0"))
    monkeypatch.setattr(module, "get_chrome_installer", lambda: installer)
    monkeypatch.setattr(module, "ChromeProcess", Mock(side_effect=AssertionError("must not launch")))
    profile = Profile("id", "owner", "saved", "custom", None, 152)
    with pytest.raises(NodeUserError, match="browser 151"):
        await runtime._start(profile)
    installer.ensure.assert_awaited_once_with(wait=10.0, min_major=152)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,expected", [("system", False), ("testing", True)])
async def test_runtime_preserves_system_user_agent(monkeypatch, provider, expected):
    from nodes.browser import _runtime as module

    session = SimpleNamespace(send=AsyncMock(), target_id="t")
    cdp = SimpleNamespace(on=Mock(), send=AsyncMock(), page_targets=AsyncMock(return_value=[{"targetId": "t", "type": "page"}]), attach=AsyncMock(return_value=session))
    controller = SimpleNamespace(tabs={}, active_target_id=None)
    profile_runtime = SimpleNamespace(cdp=cdp, controller=controller, full_version="151.1.2.3", provider=provider, webmcp=SimpleNamespace(attach=AsyncMock()))
    await module.BrowserRuntime()._wire(profile_runtime)
    methods = [call.args[0] for call in session.send.await_args_list]
    assert ("Network.setUserAgentOverride" in methods) is expected


@pytest.mark.asyncio
async def test_open_reuses_running_profile_for_agent_and_viewer(monkeypatch):
    from nodes.browser import _runtime as module

    runtime = module.BrowserRuntime()
    existing = SimpleNamespace(running=True)
    runtime._profiles["shared"] = existing
    start = AsyncMock(side_effect=AssertionError("must reuse the profile's browser"))
    monkeypatch.setattr(runtime, "_start", start)
    profile = SimpleNamespace(id="shared")
    assert await runtime.open(profile) is existing
    assert await runtime.open(profile) is existing
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_opens_share_one_browser_start(monkeypatch):
    import asyncio
    from nodes.browser import _runtime as module

    runtime = module.BrowserRuntime()
    entered, release = asyncio.Event(), asyncio.Event()
    existing = SimpleNamespace(running=True)

    async def launch(profile):
        entered.set()
        await release.wait()
        return existing

    start = AsyncMock(side_effect=launch)
    monkeypatch.setattr(runtime, "_start", start)
    monkeypatch.setattr(runtime, "_ensure_reaper", Mock())
    profile = SimpleNamespace(id="shared")
    first = asyncio.create_task(runtime.open(profile))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(runtime.open(profile))
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(first, second) == [existing, existing]
    start.assert_awaited_once_with(profile)


@pytest.mark.asyncio
async def test_start_records_connected_version_and_passes_actual_major(monkeypatch):
    import asyncio

    from nodes.browser import _profiles, _runtime as module
    from services.plugin import deps

    config = settings(monkeypatch)
    runtime = module.BrowserRuntime()
    monkeypatch.setattr(runtime, "_settings", lambda: config)
    monkeypatch.setattr(runtime, "_make_room", AsyncMock())
    monkeypatch.setattr(runtime, "_wire", AsyncMock())
    monkeypatch.setattr(runtime, "_watch", AsyncMock())
    monkeypatch.setattr(module, "profile_dir", lambda p: Path("profiles") / p)
    monkeypatch.setattr(module, "user_data_dir", lambda p: Path("profiles") / p / "user-data")
    installer = SimpleNamespace(ensure=AsyncMock(return_value=Path("chrome")), state=SimpleNamespace(version="151.0.0.0"), provider=lambda: "system")
    monkeypatch.setattr(module, "get_chrome_installer", lambda: installer)
    monkeypatch.setattr(module, "get_browser_use_installer", lambda: SimpleNamespace(ensure=AsyncMock(return_value={"cli": Path("cli"), "python": Path("py")})))
    proxy = SimpleNamespace(start=Mock(return_value=5000), port=5000)
    monkeypatch.setattr(module, "EgressProxy", Mock(return_value=proxy))
    cdp = SimpleNamespace(send=AsyncMock(return_value={"product": "Chrome/152.1.2.3"}))
    chrome = SimpleNamespace(launch=AsyncMock(return_value=cdp), port=9000)
    constructor = Mock(return_value=chrome)
    monkeypatch.setattr(module, "ChromeProcess", constructor)
    monkeypatch.setattr(module, "BrowserUseCli", Mock(return_value=SimpleNamespace(stop_daemon=Mock())))
    record = AsyncMock()
    monkeypatch.setattr(_profiles, "ProfileStore", lambda db: SimpleNamespace(record_chrome_major=record))
    monkeypatch.setattr(deps, "get_database", lambda: object())
    profile = _profiles.Profile("id", "owner", "saved", "custom", None, 150)
    running = await runtime._start(profile)
    await asyncio.sleep(0)
    assert constructor.call_args.kwargs["major"] == 151
    assert constructor.call_args.kwargs["headless"] is True
    assert constructor.call_args.kwargs["override_user_agent"] is False
    assert running.full_version == "152.1.2.3"
    assert running.profile.chrome_major == 152
    record.assert_awaited_once_with("id", 152)
