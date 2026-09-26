"""The fleet of running profile browsers.

:class:`BrowserRuntime` (one per backend process) selects an installed browser
(or explicitly opted-in testing build), installs the browser-use CLI on first
use, starts a profile's Chrome when a Browser
node or a viewer needs it, stops it when it has been idle for
``BROWSER_IDLE_TIMEOUT_MS``, and keeps at most ``BROWSER_MAX_INSTANCES``
running by stopping the least recently used idle one.

For each running profile a :class:`ProfileRuntime` holds the Chrome process,
its egress proxy, the backend's CDP connection, the CLI bound to it, the
WebMCP tracker and the :class:`~._session.ProfileController`. On the backend
connection every page is auto-attached (paused until configured) so the
WebMCP tracking and tab tracking are in place before a
page, or a popup, runs a line of script.
"""

from __future__ import annotations

import asyncio
import ipaddress
import sys
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, FrozenSet, List, Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError

from ._cdp import CDPConnection, CDPDisconnected, CDPError, CDPSession
from ._chrome import ChromeProcess, VIEWPORT_HEIGHT, VIEWPORT_WIDTH, user_agent
from ._cli import BrowserUseCli
from ._config import get_config
from ._egress import EgressProxy
from ._host import sandbox_disabled, shm_small
from ._install_bu import get_browser_use_installer
from ._install_chrome import get_chrome_installer
from ._netpolicy import NetPolicy, own_ports_from_env
from ._profiles import Profile, profile_dir, user_data_dir
from ._session import BrowserSession, ControlState, ProfileController
from ._system_browser import version_major
from ._webmcp import WebMcpTracker

logger = get_logger(__name__)

_REAPER_SECONDS = 30.0
_STOP_TIMEOUT = 8.0


def _local_addresses() -> FrozenSet[ipaddress._BaseAddress]:
    found = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                try:
                    found.add(ipaddress.ip_address(addr.address.split("%", 1)[0]))
                except ValueError:
                    continue
    except Exception:  # noqa: BLE001 - loopback alone still protects the local case
        pass
    return frozenset(found)


def _ua_metadata(major: int, full_version: str) -> Dict[str, Any]:
    platform = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    platform_version = {"win32": "10.0.0", "darwin": "15.0.0"}.get(sys.platform, "6.8.0")
    brands = [
        {"brand": "Google Chrome", "version": str(major)},
        {"brand": "Chromium", "version": str(major)},
        {"brand": "Not/A)Brand", "version": "24"},
    ]
    return {
        "brands": brands,
        "fullVersionList": [dict(b, version=full_version if b["brand"] != "Not/A)Brand" else "24.0.0.0") for b in brands],
        "platform": platform,
        "platformVersion": platform_version,
        "architecture": "arm" if "arm" in (__import__("platform").machine() or "").lower() else "x86",
        "model": "",
        "mobile": False,
        "bitness": "64",
        "wow64": False,
    }


@dataclass
class ProfileRuntime:
    profile: Profile
    controller: ProfileController
    chrome: ChromeProcess
    proxy: EgressProxy
    cdp: CDPConnection
    cli: BrowserUseCli
    webmcp: WebMcpTracker
    full_version: str = ""
    provider: str = "system"
    started_at: float = 0.0

    @property
    def running(self) -> bool:
        return self.chrome.is_running() and not self.cdp.is_closed

    async def page_session(self, target_id: Optional[str] = None) -> CDPSession:
        """A fresh flattened session on a page (the active one by default)."""
        target = target_id or self.controller.active_target_id
        if not target:
            pages = await self.cdp.page_targets()
            if not pages:
                created = await self.cdp.send("Target.createTarget", {"url": "about:blank"})
                target = created["targetId"]
            else:
                target = pages[0]["targetId"]
            self.controller.active_target_id = target
        return await self.cdp.attach(target)


class BrowserRuntime:
    def __init__(self) -> None:
        self._profiles: Dict[str, ProfileRuntime] = {}
        self._controllers: Dict[str, ProfileController] = {}
        self._chromes: Dict[str, ChromeProcess] = {}
        self._proxies: Dict[str, EgressProxy] = {}
        self._starting: Dict[str, asyncio.Task] = {}
        self._sessions: Dict[str, BrowserSession] = {}
        self._reaper: Optional[asyncio.Task] = None
        self._own_ports: FrozenSet[int] = own_ports_from_env()
        self._local_addresses = _local_addresses()

    # -- settings --------------------------------------------------------------

    @staticmethod
    def _settings() -> Any:
        from core.container import container

        return container.settings()

    def base_policy(self, *, allow_private_network: bool = False, allowed_domains: tuple = ()) -> NetPolicy:
        return NetPolicy(
            allow_private_network=allow_private_network,
            blocked_local_ports=self._blocked_ports(),
            local_addresses=self._local_addresses,
            allowed_domains=allowed_domains,
        )

    def _blocked_ports(self) -> FrozenSet[int]:
        dynamic = {c.port for c in self._chromes.values() if c.port} | {p.port for p in self._proxies.values() if p.port}
        return self._own_ports | frozenset(dynamic)

    def _effective_policy(self, controller: ProfileController) -> NetPolicy:
        policy = controller.current_policy()
        return replace(policy, blocked_local_ports=self._blocked_ports() | policy.blocked_local_ports, local_addresses=self._local_addresses)

    # -- sessions --------------------------------------------------------------

    def register_session(self, session: BrowserSession) -> BrowserSession:
        existing = self._sessions.get(session.session_id)
        if existing is not None:
            existing.profile_id = session.profile_id
            existing.label = session.label
            existing.policy = session.policy
            return existing
        self._sessions[session.session_id] = session
        return session

    def session(self, session_id: str) -> Optional[BrowserSession]:
        return self._sessions.get(session_id)

    def find_session(self, workflow_id: str, node_id: str) -> Optional[BrowserSession]:
        return next((s for s in self._sessions.values() if s.workflow_id == workflow_id and s.node_id == node_id), None)

    def sessions_for_workflow(self, workflow_id: str) -> List[BrowserSession]:
        return [s for s in self._sessions.values() if s.workflow_id == workflow_id]

    def forget_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def controller(self, profile_id: str) -> Optional[ProfileController]:
        return self._controllers.get(profile_id)

    def running(self, profile_id: str) -> Optional[ProfileRuntime]:
        runtime = self._profiles.get(profile_id)
        return runtime if runtime is not None and runtime.running else None

    def state_of(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        """Synchronous summary for the Home employee card."""
        session = self.find_session(workflow_id, node_id)
        if session is None:
            return {"state": "idle", "request": None}
        controller = self._controllers.get(session.profile_id)
        if controller is None or controller.lease_session is None or controller.lease_session.session_id != session.session_id:
            return {"state": "idle", "request": None}
        return {"state": controller.state.value, "request": controller.pending.to_wire() if controller.pending else None}

    # -- starting and stopping ----------------------------------------------------

    def controller_for(self, profile: Profile) -> ProfileController:
        controller = self._controllers.get(profile.id)
        if controller is None:
            controller = ProfileController(profile.id, profile.name)
            controller.set_fallback_policy(self.base_policy())
            self._controllers[profile.id] = controller
        controller.profile_name = profile.name
        return controller

    async def open(self, profile: Profile, *, wait: float = 60.0) -> ProfileRuntime:
        """The profile's running browser, starting Chrome if needed."""
        runtime = self._profiles.get(profile.id)
        if runtime is not None and runtime.running:
            return runtime
        self._ensure_reaper()
        task = self._starting.get(profile.id)
        if task is None or task.done():
            task = asyncio.create_task(self._start(profile), name=f"browser-start-{profile.id}")
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._starting[profile.id] = task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(1.0, wait))
        except asyncio.TimeoutError:
            chrome_status = get_chrome_installer().status()
            if chrome_status.get("phase") == "downloading":
                pct = chrome_status.get("percent")
                raise NodeUserError(f"Downloading the browser ({pct or 0}%). This happens once; try again in a minute.") from None
            raise NodeUserError("The browser is still starting; try again in a moment.") from None

    async def _start(self, profile: Profile) -> ProfileRuntime:
        settings = self._settings()
        pin = get_config().chrome
        installer = get_chrome_installer()
        exe = await installer.ensure(wait=float(settings.browser_install_timeout_seconds), min_major=profile.chrome_major or 0)
        selected_version = installer.state.version
        selected_major = version_major(selected_version)
        if profile.chrome_major and profile.chrome_major > selected_major:
            raise NodeUserError(
                f"The profile {profile.name!r} was last opened by a newer Chrome ({profile.chrome_major}); this install "
                f"has browser {selected_major}. Update your browser or select another profile."
            )
        provider = installer.provider()
        bu_paths = await get_browser_use_installer().ensure(wait=float(settings.browser_install_timeout_seconds))
        await self._make_room(exclude=profile.id)

        controller = self.controller_for(profile)
        proxy = self._proxies.get(profile.id)
        if proxy is None:
            proxy = EgressProxy(lambda c=controller: self._effective_policy(c), name=f"browser-egress-{profile.id}")
            self._proxies[profile.id] = proxy
        proxy_port = await asyncio.to_thread(proxy.start)

        no_sandbox, why = sandbox_disabled(str(getattr(settings, "browser_sandbox", "auto")))
        if no_sandbox:
            logger.warning("[browser] Chrome's sandbox is off (%s)", why)
        chrome = self._chromes.get(profile.id)
        if chrome is None:
            chrome = ChromeProcess(
                profile_id=profile.id,
                exe=exe,
                profile_root=profile_dir(profile.id),
                user_data_dir=user_data_dir(profile.id),
                proxy_port=proxy_port,
                major=selected_major,
                no_sandbox=no_sandbox,
                small_shm=shm_small(),
                headless=bool(getattr(settings, "browser_headless", False)),
                override_user_agent=provider == "testing",
            )
            self._chromes[profile.id] = chrome
        else:
            chrome._exe, chrome._proxy_port = exe, proxy_port
            chrome._major = selected_major
            chrome._headless = bool(getattr(settings, "browser_headless", False))
            chrome._override_user_agent = provider == "testing"

        cli = BrowserUseCli(
            cli_path=bu_paths["cli"], python_path=bu_paths["python"], profile_id=profile.id, cdp_http_url="http://127.0.0.1:0"
        )
        cli.stop_daemon()  # one left from an earlier Chrome would point at a dead port

        cdp = await chrome.launch()
        cli.cdp_http_url = f"http://127.0.0.1:{chrome.port}"
        webmcp = WebMcpTracker()
        runtime = ProfileRuntime(
            profile=profile, controller=controller, chrome=chrome, proxy=proxy, cdp=cdp, cli=cli, webmcp=webmcp, started_at=time.monotonic(), provider=provider
        )
        try:
            version = await cdp.send("Browser.getVersion")
            runtime.full_version = str(version.get("product", "")).split("/", 1)[-1]
            actual_major = version_major(runtime.full_version)
            if actual_major < pin.min_major or (profile.chrome_major and actual_major < profile.chrome_major):
                raise NodeUserError("The launched browser is older than this profile or the minimum supported browser version. Update the selected browser or use another profile.")
            await self._wire(runtime)
        except BaseException:
            await chrome.shutdown()
            raise
        self._profiles[profile.id] = runtime
        controller.last_activity = time.monotonic()
        asyncio.create_task(self._watch(runtime))
        try:
            from services.plugin.deps import get_database

            from ._profiles import ProfileStore

            await ProfileStore(get_database()).record_chrome_major(profile.id, actual_major)
            runtime.profile = replace(profile, chrome_major=actual_major)
        except Exception:  # noqa: BLE001 - the downgrade guard is advisory
            logger.debug("[browser] could not record the Chrome version on the profile", exc_info=True)
        logger.info("[browser] profile %s (%s) running Chrome %s on port %s", profile.id, profile.name, runtime.full_version, chrome.port)
        return runtime

    async def _wire(self, runtime: ProfileRuntime) -> None:
        cdp, controller = runtime.cdp, runtime.controller
        major = version_major(runtime.full_version)
        ua = user_agent(major)
        metadata = _ua_metadata(major, runtime.full_version)

        async def configure(session: CDPSession, target: Dict[str, Any], waiting: bool) -> None:
            try:
                if target.get("type") == "page":
                    if runtime.provider == "testing":
                        await session.send("Network.setUserAgentOverride", {"userAgent": ua, "userAgentMetadata": metadata}, timeout=10)
                    await session.send("Page.enable", timeout=10)
                    await runtime.webmcp.attach(session.target_id, session)
            except (CDPError, CDPDisconnected, TimeoutError):
                logger.debug("[browser] configuring page %s failed", target.get("targetId"), exc_info=True)
            finally:
                if waiting:
                    try:
                        await session.send("Runtime.runIfWaitingForDebugger", timeout=10)
                    except (CDPError, CDPDisconnected, TimeoutError):
                        pass

        def on_attached(params: Dict[str, Any]) -> None:
            target = params.get("targetInfo") or {}
            session = CDPSession(cdp, params.get("sessionId", ""), target.get("targetId", ""))
            asyncio.ensure_future(configure(session, target, bool(params.get("waitingForDebugger"))))

        def on_target(params: Dict[str, Any]) -> None:
            info = params.get("targetInfo") or {}
            if info.get("type") != "page":
                return
            controller.tabs[info["targetId"]] = {"target_id": info["targetId"], "url": info.get("url"), "title": info.get("title")}
            asyncio.ensure_future(controller.emit("tabs", {"tabs": list(controller.tabs.values())}))

        def on_destroyed(params: Dict[str, Any]) -> None:
            target_id = params.get("targetId")
            if controller.tabs.pop(target_id, None) is not None:
                runtime.webmcp.detach(target_id)
                if controller.active_target_id == target_id:
                    controller.active_target_id = next(iter(controller.tabs), None)
                asyncio.ensure_future(controller.emit("tabs", {"tabs": list(controller.tabs.values())}))

        cdp.on("Target.attachedToTarget", on_attached)
        cdp.on("Target.targetCreated", on_target)
        cdp.on("Target.targetInfoChanged", on_target)
        cdp.on("Target.targetDestroyed", on_destroyed)
        await cdp.send("Target.setDiscoverTargets", {"discover": True})
        await cdp.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        for page in await cdp.page_targets():
            controller.tabs[page["targetId"]] = {"target_id": page["targetId"], "url": page.get("url"), "title": page.get("title")}
            session = await cdp.attach(page["targetId"])
            await configure(session, page, False)
            try:
                await session.send(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT, "deviceScaleFactor": 1, "mobile": False},
                    timeout=10,
                )
            except (CDPError, CDPDisconnected, TimeoutError):
                pass
        if controller.tabs and controller.active_target_id not in controller.tabs:
            controller.active_target_id = next(iter(controller.tabs))

    async def _watch(self, runtime: ProfileRuntime) -> None:
        """Chrome went away on its own (crash, killed): tidy up."""
        await runtime.cdp.wait_closed()
        if self._profiles.get(runtime.profile.id) is not runtime:
            return
        logger.warning("[browser] Chrome for profile %s stopped unexpectedly", runtime.profile.id)
        await self._teardown(runtime)
        controller = runtime.controller
        controller.tabs.clear()
        controller.active_target_id = None
        await controller.emit("closed", {"reason": "Chrome stopped"})
        if controller.state != ControlState.IDLE:
            await controller.release_lease()

    async def _teardown(self, runtime: ProfileRuntime) -> None:
        self._profiles.pop(runtime.profile.id, None)
        try:
            runtime.cli.stop_daemon()
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(runtime.chrome.shutdown(), timeout=_STOP_TIMEOUT)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - make sure it is gone
            try:
                await runtime.chrome.stop()
            except Exception:  # noqa: BLE001
                pass

    async def stop_profile(self, profile_id: str, *, reason: str = "") -> None:
        runtime = self._profiles.get(profile_id)
        if runtime is None:
            return
        controller = runtime.controller
        await controller.release_lease()
        await self._teardown(runtime)
        controller.tabs.clear()
        controller.active_target_id = None
        await controller.emit("closed", {"reason": reason or "stopped"})
        proxy = self._proxies.pop(profile_id, None)
        if proxy is not None:
            await asyncio.to_thread(proxy.stop)
        logger.info("[browser] profile %s stopped (%s)", profile_id, reason or "requested")

    async def _make_room(self, *, exclude: str) -> None:
        cap = int(getattr(self._settings(), "browser_max_instances", 3) or 3)
        running = [r for pid, r in self._profiles.items() if r.running and pid != exclude]
        if len(running) < cap:
            return
        idle = sorted((r for r in running if r.controller.idle_for() > 0), key=lambda r: r.controller.idle_for(), reverse=True)
        if not idle:
            raise NodeUserError(
                f"{len(running)} browser profiles are busy (the limit is {cap}). Wait for one to finish or raise BROWSER_MAX_INSTANCES."
            )
        await self.stop_profile(idle[0].profile.id, reason="making room for another profile")

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap(), name="browser-reaper")

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(_REAPER_SECONDS)
            idle_limit = float(getattr(self._settings(), "browser_idle_timeout_ms", 0) or 0) / 1000.0
            for profile_id, runtime in list(self._profiles.items()):
                try:
                    await runtime.controller.tick()
                    if idle_limit > 0 and runtime.controller.idle_for() > idle_limit:
                        await self.stop_profile(profile_id, reason="idle")
                except Exception:  # noqa: BLE001 - the reaper must keep running
                    logger.debug("[browser] reaper step failed", exc_info=True)

    async def shutdown(self) -> None:
        """Stop every profile's Chrome gracefully (the plugin shutdown hook)."""
        if self._reaper is not None:
            self._reaper.cancel()
        await asyncio.gather(*(self._teardown(r) for r in list(self._profiles.values())), return_exceptions=True)
        for proxy in list(self._proxies.values()):
            try:
                proxy.stop()
            except Exception:  # noqa: BLE001
                pass
        self._proxies.clear()

    # -- status ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return {
            "chrome": get_chrome_installer().status(),
            "cli": get_browser_use_installer().status(),
            "running": [
                {"profile_id": pid, "name": r.profile.name, "state": r.controller.state.value, "port": r.chrome.port, "version": r.full_version, "provider": r.provider}
                for pid, r in self._profiles.items()
                if r.running
            ],
        }


_runtime: Optional[BrowserRuntime] = None


def get_browser_runtime() -> BrowserRuntime:
    global _runtime
    if _runtime is None:
        _runtime = BrowserRuntime()
    return _runtime


def peek_browser_runtime() -> Optional[BrowserRuntime]:
    """The runtime if one was created, without creating it (for shutdown)."""
    return _runtime


__all__ = ["BrowserRuntime", "ProfileRuntime", "get_browser_runtime", "peek_browser_runtime"]
