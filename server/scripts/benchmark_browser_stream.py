"""Real local Chrome → production stream → reference canvas latency benchmark.

No download, real user profile, application database, or external website is used.
Authentication/session lookup are replaced inside this benchmark process only.
The measured viewer is a minimal reference decoder, NOT the React application.
Run from server/: .venv/Scripts/python scripts/benchmark_browser_stream.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

PAGE = """<!doctype html><meta charset=utf-8><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#888}canvas{display:block}
</style><canvas id=p width=1280 height=800></canvas><script>
const ctx=p.getContext('2d');let value=0;
function paint(){ctx.fillStyle='#888';ctx.fillRect(0,0,1280,800);
for(let i=0;i<16;i++){ctx.fillStyle=(value>>i)&1?'#fff':'#000';ctx.fillRect(i*32,0,32,32)}
ctx.fillStyle='#111';ctx.font='40px sans-serif';ctx.fillText('Local stream benchmark '+value,80,150)}
addEventListener('mousedown',()=>{value++;paint()});paint();
</script>"""

VIEWER = """<!doctype html><meta charset=utf-8><canvas id=p width=1280 height=800></canvas><script>
const cfg=__CONFIG__,ctx=p.getContext('2d'),metrics={latencies:[],frameAge:[],decodeDraw:[],paintWait:[],misses:[],observed:[],errors:[],visibility:document.visibilityState};
window.benchmark={ready:false,done:false,metrics};let count=0,pending=new Map(),chain=Promise.resolve();
const ws=new WebSocket('ws://'+location.host+'/ws/browser');ws.binaryType='arraybuffer';
const send=m=>ws.send(JSON.stringify(m));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const paintTurn=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
ws.onopen=()=>send({type:'attach',target:{kind:'node',workflow_id:'benchmark',node_id:'browser'},
viewport:{width:1280,height:800,dpr:1},visible:true,max_fps:cfg.fps});
ws.onmessage=e=>{if(typeof e.data==='string'){const m=JSON.parse(e.data);
if(m.type==='attached')send({type:'control_request'});
if(m.type==='state'&&m.controller==='you')window.benchmark.ready=true;
if(m.type==='error')metrics.errors.push(m);return}
chain=chain.then(async()=>{let seq;try{const decodeStarted=performance.now(),v=new DataView(e.data),end=4+v.getUint16(2,false);
const h=JSON.parse(new TextDecoder().decode(new Uint8Array(e.data,4,end-4)));seq=h.seq;
const image=await createImageBitmap(new Blob([e.data.slice(end)],{type:'image/jpeg'}));
ctx.drawImage(image,0,0,1280,800);image.close();let code=0;
for(let i=0;i<16;i++)if(ctx.getImageData(i*32+16,16,1,1).data[0]>128)code|=1<<i;
const drawn=performance.now();await paintTurn();const now=performance.now();
metrics.decodeDraw.push(drawn-decodeStarted);metrics.paintWait.push(now-drawn);
metrics.observed.push(code);
if(h.ts)metrics.frameAge.push(Date.now()-h.ts*1000);
const sample=pending.get(code);if(sample){metrics.latencies.push(now-sample.started);pending.delete(code);sample.resolve()}
}catch(error){metrics.errors.push(String(error))}finally{if(ws.readyState===1)send({type:'ack',seq})}})};
ws.onerror=()=>metrics.errors.push('socket error');
window.runBenchmark=async()=>{for(let i=0;i<cfg.samples+cfg.warmup;i++){
await pause(cfg.interval_ms+(i%7));count++;
let resolve;const seen=new Promise(r=>resolve=r);pending.set(count,{started:performance.now(),resolve});
send({type:'mouse',action:'down',x:600,y:400,button:'left',buttons:1,click_count:1});
send({type:'mouse',action:'up',x:600,y:400,button:'left',buttons:0,click_count:1});
await Promise.race([seen,pause(cfg.deadline_ms)]);
if(pending.has(count)){metrics.misses.push(count);pending.delete(count)}
if(i===cfg.warmup-1){metrics.latencies=[];metrics.frameAge=[];metrics.decodeDraw=[];metrics.paintWait=[];metrics.misses=[];metrics.observed=[]}}
send({type:'control_release'});window.benchmark.done=true;return metrics};
</script>"""


class BenchController:
    """Identity/control fixture; production stream reader and CDP input are unchanged."""

    def __init__(self, target: str, url: str):
        self.active_target_id = target
        self.tabs = {target: {"target_id": target, "url": url, "title": "Local benchmark"}}
        self.controller_viewer = None
        self.listeners = []

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    def viewer_attached(self, _viewer):
        pass

    def viewer_detached(self, viewer):
        if viewer == self.controller_viewer:
            self.controller_viewer = None

    def snapshot(self, viewer=None):
        return {
            "state": "user" if self.controller_viewer else "idle",
            "controller": "you" if viewer == self.controller_viewer and viewer else None,
            "request": None,
            "profile": {"id": "benchmark", "name": "Benchmark"},
        }

    async def acquire_lease(self, _session, **_kwargs):
        pass

    async def take_over(self, viewer, **_kwargs):
        self.controller_viewer = viewer
        for listener in self.listeners:
            await listener("state", {})
        return True, "granted"

    async def hand_back(self, _viewer, **_kwargs):
        self.controller_viewer = None

    def can_inject_input(self, viewer):
        return viewer == self.controller_viewer

    def touch_user_input(self):
        pass


def chrome_path(value: str | None) -> Path:
    candidates = (
        [value]
        if value
        else [
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise SystemExit("No installed Chrome found. Supply --chrome; this benchmark never downloads a browser.")


def summarize(values):
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "p50_ms": round(ordered[math.ceil(len(values) * 0.5) - 1], 2),
        "p95_ms": round(ordered[math.ceil(len(values) * 0.95) - 1], 2),
        "max_ms": round(ordered[-1], 2),
    }


async def run(args, scratch: Path):
    os.environ["DATA_DIR"] = str(scratch / "data")
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    import uvicorn

    # Load only the two measured modules, avoiding the app's eager plugin
    # discovery and all user settings/database initialization. These fixtures
    # exist solely in this standalone process and never replace project files.
    def fixture(name, **values):
        module = ModuleType(name)
        module.__dict__.update(values)
        sys.modules[name] = module
        return module

    fixture("nodes", __path__=[str(SERVER / "nodes")])
    fixture("nodes.browser", __path__=[str(SERVER / "nodes/browser")])
    fixture("nodes.browser._chrome", VIEWPORT_WIDTH=1280, VIEWPORT_HEIGHT=800)
    runtime_module = fixture("nodes.browser._runtime")
    auth_module = fixture("services.authz")
    fixture("services.plugin", __path__=[str(SERVER / "services/plugin")])
    fixture("services.plugin.base", NodeUserError=type("NodeUserError", (Exception,), {}))
    fixture("core.container", container=SimpleNamespace(settings=lambda: None, user_auth_service=None))
    from nodes.browser._cdp import CDPConnection, read_devtools_active_port

    source = (args.stream_source or SERVER / "nodes/browser/_stream.py").resolve()
    source_bytes = source.read_bytes()
    spec = importlib.util.spec_from_file_location("nodes.browser._benchmark_stream", source)
    stream = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stream
    exec(compile(source_bytes, str(source), "exec"), stream.__dict__)
    config = {
        "samples": args.samples,
        "warmup": args.warmup,
        "fps": args.fps,
        "interval_ms": args.interval_ms,
        "deadline_ms": args.deadline_ms,
    }
    app = FastAPI()
    app.add_api_route("/page", lambda: HTMLResponse(PAGE))
    app.add_api_route("/viewer", lambda: HTMLResponse(VIEWER.replace("__CONFIG__", json.dumps(config))))
    app.add_api_websocket_route("/ws/browser", stream.browser_live_view)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    http = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off", timeout_graceful_shutdown=3))
    server_task = asyncio.create_task(http.serve(sockets=[listener]))
    profile = scratch / "chrome-profile"
    profile.mkdir()
    chrome = chrome_path(args.chrome)
    proc = None
    cdp = None
    try:
        while not http.started:
            if server_task.done():
                await server_task
                raise RuntimeError("Benchmark HTTP server did not start")
            await asyncio.sleep(0.02)
        command = [
            str(chrome),
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-component-update",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--window-size=1280,800",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
            "about:blank",
        ]
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "DISPLAY", "XDG_RUNTIME_DIR"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 20
        endpoint = None
        while time.monotonic() < deadline:
            endpoint = read_devtools_active_port(profile)
            if endpoint:
                break
            if proc.poll() is not None:
                raise RuntimeError(f"Isolated Chrome exited with code {proc.returncode}")
            await asyncio.sleep(0.05)
        if not endpoint:
            raise RuntimeError("Isolated Chrome did not expose CDP within 20s")
        cdp = await CDPConnection.connect(f"ws://127.0.0.1:{endpoint[0]}{endpoint[1]}")
        version = await cdp.send("Browser.getVersion")
        target = (await cdp.send("Target.createTarget", {"url": f"http://127.0.0.1:{port}/page"}))["targetId"]
        target_session = await cdp.attach(target)
        await target_session.send(
            "Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False}
        )
        controller = BenchController(target, f"http://127.0.0.1:{port}/page")

        async def page_session(_target=None):
            return await cdp.attach(_target or target)

        runtime = SimpleNamespace(controller=controller, running=True, page_session=page_session, cdp=cdp)
        facade = SimpleNamespace(running=lambda _profile: runtime, controller=lambda _profile: controller)
        runtime_module.get_browser_runtime = lambda: facade

        async def auth(_websocket, **_kwargs):
            return "benchmark-owner"

        async def attach(_owner, _target):
            return SimpleNamespace(profile_id="benchmark", session_id="benchmark-session", policy=None)

        auth_module.authenticate_ws = auth
        stream._resolve_attach = attach
        viewer_target = (await cdp.send("Target.createTarget", {"url": f"http://127.0.0.1:{port}/viewer"}))["targetId"]
        viewer = await cdp.attach(viewer_target)
        await viewer.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = await viewer.send("Runtime.evaluate", {"expression": "Boolean(window.benchmark?.ready)", "returnByValue": True})
            if state.get("result", {}).get("value"):
                break
            await asyncio.sleep(0.05)
        else:
            debug = await viewer.send(
                "Runtime.evaluate",
                {
                    "expression": "JSON.stringify({url:location.href,benchmark:window.benchmark,body:document.body.innerText})",
                    "returnByValue": True,
                },
            )
            raise RuntimeError(f"Viewer did not acquire benchmark control within 20s: {debug}")
        await asyncio.sleep(0.3)
        result = await viewer.send(
            "Runtime.evaluate",
            {"expression": "window.runBenchmark()", "awaitPromise": True, "returnByValue": True},
            timeout=(args.samples + args.warmup) * (args.deadline_ms + args.interval_ms + 20) / 1000 + 20,
        )
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"]))
        raw = result["result"]["value"]
        target_count = await target_session.send("Runtime.evaluate", {"expression": "value", "returnByValue": True})
        latency = summarize(raw["latencies"])
        report = {
            "label": args.label,
            "measurement": "real Chrome CDP + production stream + reference canvas double-rAF paint proxy",
            "limitations": [
                "Not the production React renderer",
                "Authentication and profile/control lookup are fixtures",
                "Local static page; not WAN or third-party site latency",
                "Missed updates are censored and reported separately; percentiles cover observed updates only",
            ],
            "stream_source": str(source),
            "stream_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "chrome": version.get("product"),
            "config": config,
            "input_to_paint": latency,
            "frame_age": summarize(raw["frameAge"]),
            "reference_decode_draw": summarize(raw["decodeDraw"]),
            "reference_draw_to_double_raf": summarize(raw["paintWait"]),
            "reference_visibility": raw["visibility"],
            "missed_updates": len(raw["misses"]),
            "target_clicks": target_count.get("result", {}).get("value"),
            "expected_clicks": args.samples + args.warmup,
            "errors": raw["errors"],
            "target_200ms": bool(latency["p95_ms"] is not None and latency["p95_ms"] <= 200 and not raw["misses"]),
            "raw": raw,
        }
        if args.output:
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "raw"}, indent=2), flush=True)
    finally:
        if cdp:
            with contextlib.suppress(Exception):
                await cdp.send("Browser.close", timeout=5)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(cdp.close(), timeout=10)
        if proc and proc.poll() is None:
            try:
                await asyncio.to_thread(proc.wait, timeout=8)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    proc.kill()
        http.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server_task, timeout=10)
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", help="Existing Chrome executable; never downloaded")
    parser.add_argument("--stream-source", type=Path, help="Preserved baseline _stream.py; default is current source")
    parser.add_argument("--label", default="current")
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--interval-ms", type=int, default=25)
    parser.add_argument("--deadline-ms", type=int, default=1000)
    parser.add_argument("--output", type=Path, help="JSON report path, preferably in your temporary directory")
    args = parser.parse_args()
    if args.samples < 1 or args.warmup < 0 or args.fps <= 0 or args.deadline_ms < 1 or args.interval_ms < 0:
        parser.error("samples/fps/deadline must be positive; warmup/interval must be nonnegative")
    with tempfile.TemporaryDirectory(prefix="opencompany-stream-benchmark-") as temp:
        asyncio.run(run(args, Path(temp)))


if __name__ == "__main__":
    main()
