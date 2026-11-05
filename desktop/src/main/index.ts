/**
 * OpenCompany desktop shell — main process.
 *
 * Boot: single-instance lock -> setup window -> provision the Python env if
 * the stamp changed -> pick a stable port (or attach to a running backend)
 * -> spawn uvicorn -> wait on /health/ready -> navigate the main window to
 * http://127.0.0.1:<port> -> check for updates.
 * Quit: POST /api/desktop/shutdown -> wait -> tree-kill fallback.
 *
 * The backend's side of every one of these steps is documented in
 * docs-internal/desktop_host_contract.md.
 */

import { type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { BrowserWindow, app, dialog, ipcMain, shell } from "electron";

import { phaseLabel, spawnBackend, waitReady } from "./backend";
import { buildBackendEnv } from "./env";
import { IPC, type SetupStatePayload } from "./ipc";
import { initLogging } from "./logging";
import { installMenu } from "./menu";
import { type Layout, layoutReport, resolveLayout, targetKey, venvPython } from "./paths";
import { choosePort } from "./ports";
import { expectedStamp, needsProvision, runProvision } from "./provision";
import { STOP_TIMEOUT_MS, stopBackend, treeKill } from "./shutdown";
import { loadState, saveState } from "./state";
import { checkForUpdates, initUpdater } from "./updater";

const RELEASES_URL = "https://github.com/zeenie-ai/OpenCompany/releases";
const DEFAULT_PORT = 5678;
const SCAN_FROM = 5679;
const SCAN_TO = 5699;
const READY_TIMEOUT_MS = 180_000;

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

function computeLayout(): Layout {
  const dev = !app.isPackaged;
  // In development Electron's default name is "Electron" (userData would be
  // Roaming/Electron); packaged builds take productName from package.json.
  if (dev) app.setName("OpenCompany");
  // Tests isolate every launch in a throwaway userData dir.
  if (process.env.OPENCOMPANY_DESKTOP_USER_DATA) app.setPath("userData", process.env.OPENCOMPANY_DESKTOP_USER_DATA);
  const desktopDir = join(__dirname, "..", "..");
  const resources = dev ? join(desktopDir, "stage") : process.resourcesPath;
  return resolveLayout({
    resources,
    userData: app.getPath("userData"),
    // Dev overrides: run against the checkout without staging.
    appRoot: process.env.OPENCOMPANY_DESKTOP_APP_ROOT || undefined,
    runtimeDir: process.env.OPENCOMPANY_DESKTOP_RUNTIME_DIR || (dev ? join(resources, "runtime", targetKey(process.platform, process.arch)) : undefined),
  });
}

const layout = computeLayout();
const log = initLogging(layout.logsDir);
const token = randomBytes(24).toString("hex");

let setupWindow: BrowserWindow | null = null;
let mainWindow: BrowserWindow | null = null;
let backend: ChildProcess | null = null;
let backendPort = 0;
let attachedToExisting = false;
let quitting = false;
let crashRestarts: number[] = [];

// ---------------------------------------------------------------------------
// Setup window (provisioning / progress / error)
// ---------------------------------------------------------------------------

function setupHtml(): { file?: string; url?: string } {
  if (process.env.ELECTRON_RENDERER_URL) return { url: `${process.env.ELECTRON_RENDERER_URL}/setup/index.html` };
  return { file: join(__dirname, "..", "renderer", "setup", "index.html") };
}

function ensureSetupWindow(): BrowserWindow {
  if (setupWindow && !setupWindow.isDestroyed()) return setupWindow;
  setupWindow = new BrowserWindow({
    width: 620,
    height: 460,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    show: false,
    title: "OpenCompany",
    backgroundColor: "#12141f",
    webPreferences: {
      preload: join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  setupWindow.setMenuBarVisibility(false);
  const target = setupHtml();
  if (target.url) void setupWindow.loadURL(target.url);
  else void setupWindow.loadFile(target.file!);
  setupWindow.once("ready-to-show", () => setupWindow?.show());
  setupWindow.on("closed", () => {
    setupWindow = null;
  });
  return setupWindow;
}

function setupSend(channel: string, payload: unknown): void {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send(channel, payload);
}
function setupLog(line: string): void {
  log.info(`[setup] ${line}`);
  setupSend(IPC.setupLog, line);
}
function setupState(payload: SetupStatePayload): void {
  setupSend(IPC.setupState, { ...payload, logPath: layout.logsDir });
}

// ---------------------------------------------------------------------------
// Main window
// ---------------------------------------------------------------------------

function isOurOrigin(url: string): boolean {
  try {
    const u = new URL(url);
    return (u.hostname === "127.0.0.1" || u.hostname === "localhost") && Number(u.port) === backendPort;
  } catch {
    return false;
  }
}

function openExternalIfSafe(url: string): void {
  if (/^(https?:|mailto:)/i.test(url)) void shell.openExternal(url);
}

function createMainWindow(port: number): BrowserWindow {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    show: false,
    title: "OpenCompany",
    // The theme's background from last time, so Light and Atomic do not open dark.
    backgroundColor: loadState(layout.stateFile).windowBackground ?? "#12141f",
    autoHideMenuBar: process.platform !== "darwin",
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  // OAuth / device-flow logins and every target="_blank" anchor open in the
  // OS browser; the window itself stays on the backend origin.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isOurOrigin(url)) return { action: "allow" };
    openExternalIfSafe(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!isOurOrigin(url)) {
      event.preventDefault();
      openExternalIfSafe(url);
    }
  });
  // The SPA keeps its theme-color meta at the theme's background: paint the
  // window behind the page in it, and keep it for the next launch.
  win.webContents.on("did-change-theme-color", (_event, color) => {
    if (!color) return;
    win.setBackgroundColor(color);
    saveState(layout.stateFile, { ...loadState(layout.stateFile), windowBackground: color });
  });
  win.once("ready-to-show", () => {
    win.show();
    if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
  });
  win.on("closed", () => {
    mainWindow = null;
  });
  void win.loadURL(`http://127.0.0.1:${port}/`);
  return win;
}

// ---------------------------------------------------------------------------
// Boot sequence
// ---------------------------------------------------------------------------

function sweepStaleBackend(): void {
  try {
    if (!existsSync(layout.backendPidFile)) return;
    const pid = Number(readFileSync(layout.backendPidFile, "utf-8").trim());
    rmSync(layout.backendPidFile, { force: true });
    if (!pid || Number.isNaN(pid)) return;
    try {
      process.kill(pid, 0); // alive?
    } catch {
      return;
    }
    log.warn(`stale backend pid ${pid} from a previous launch is still alive; tree-killing`);
    treeKill(pid, process.platform);
  } catch (err) {
    log.warn(`stale backend sweep failed: ${String(err)}`);
  }
}

async function resolveInterpreter(): Promise<string> {
  const override = process.env.OPENCOMPANY_DESKTOP_VENV_PYTHON;
  if (override) {
    setupLog(`Using interpreter from OPENCOMPANY_DESKTOP_VENV_PYTHON: ${override}`);
    return override;
  }
  const skip = process.env.OPENCOMPANY_DESKTOP_SKIP_PROVISION === "1";
  const env = buildBackendEnv({ layout, port: 0, token, parentPid: process.pid });
  const expected = await expectedStamp(layout, env);
  if (!skip && needsProvision(layout, expected.stamp)) {
    setupState({ state: "provisioning", message: "Setting up the backend environment (first run or after an update)" });
    setupLog(`uv ${expected.uvVersion}; Python ${expected.pythonVersion}`);
    await runProvision({ layout, env, onLine: setupLog }, expected);
    const state = loadState(layout.stateFile);
    saveState(layout.stateFile, { ...state, lastProvisionStamp: expected.stamp, lastAppVersion: app.getVersion() });
  } else {
    setupLog("Backend environment is up to date.");
  }
  return venvPython(layout.venvDir, layout.platform);
}

async function startBackend(python: string): Promise<number> {
  const state = loadState(layout.stateFile);
  const preferred = [state.port, DEFAULT_PORT].filter((p): p is number => typeof p === "number" && p > 0);
  const decision = await choosePort({ preferred, scanFrom: SCAN_FROM, scanTo: SCAN_TO, allowAttach: process.env.OPENCOMPANY_DESKTOP_NO_ATTACH !== "1" });
  backendPort = decision.port;
  saveState(layout.stateFile, { ...state, port: decision.port, lastAppVersion: app.getVersion() });

  if (decision.attach) {
    attachedToExisting = true;
    setupLog(`An OpenCompany backend (${decision.existingVersion ?? "unknown version"}) is already running on port ${decision.port}; attaching to it.`);
    return decision.port;
  }

  setupState({ state: "starting", message: "Starting the backend" });
  const env = buildBackendEnv({ layout, port: decision.port, token, parentPid: process.pid });
  backend = spawnBackend({ layout, python, port: decision.port, env });
  setupLog(`backend pid ${backend.pid} on port ${decision.port}`);
  backend.once("exit", (code, signal) => onBackendExit(code, signal));

  await waitReady({
    port: decision.port,
    timeoutMs: READY_TIMEOUT_MS,
    aborted: () => backend === null || backend.exitCode !== null,
    onPhase: (phase, body) => {
      setupLog(phaseLabel(phase));
      setupSend(IPC.setupPhase, { phase, label: phaseLabel(phase), body });
    },
  });
  return decision.port;
}

function onBackendExit(code: number | null, signal: NodeJS.Signals | null): void {
  log.warn(`backend exited code=${code} signal=${signal}`);
  backend = null;
  rmSync(layout.backendPidFile, { force: true });
  if (quitting || attachedToExisting) return;
  const now = Date.now();
  crashRestarts = crashRestarts.filter((t) => now - t < 5 * 60_000);
  if (crashRestarts.length >= 3) {
    showFatal("The backend keeps crashing", `It exited ${crashRestarts.length + 1} times in five minutes. See backend.stdout.log in the logs folder.`);
    return;
  }
  crashRestarts.push(now);
  const delay = [1000, 3000, 9000][crashRestarts.length - 1] ?? 9000;
  log.warn(`restarting backend in ${delay}ms`);
  setTimeout(() => void boot().catch((err) => showFatal("Backend restart failed", String(err))), delay);
}

function showFatal(message: string, detail: string): void {
  log.error(`${message}: ${detail}`);
  ensureSetupWindow();
  setupState({ state: "error", message, detail });
}

async function boot(): Promise<void> {
  ensureSetupWindow();
  const report = layoutReport(layout);
  log.info(`layout ${JSON.stringify({ appRoot: layout.appRoot, runtimeDir: layout.runtimeDir, userData: layout.userData, report })}`);
  if (!report.appRoot || !report.clientDist || !report.envTemplate) {
    showFatal("This build is incomplete", `Missing backend files under ${layout.appRoot}. Reinstall OpenCompany.`);
    return;
  }
  mkdirSync(layout.logsDir, { recursive: true });
  sweepStaleBackend();
  try {
    const python = await resolveInterpreter();
    const port = await startBackend(python);
    setupState({ state: "ready", message: "Opening OpenCompany" });
    if (!mainWindow || mainWindow.isDestroyed()) mainWindow = createMainWindow(port);
    else void mainWindow.loadURL(`http://127.0.0.1:${port}/`);
    checkForUpdates(log);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    showFatal("OpenCompany could not start", msg);
  }
}

// ---------------------------------------------------------------------------
// Quit sequence
// ---------------------------------------------------------------------------

async function shutdownBackend(): Promise<void> {
  if (!backend) return;
  const child = backend;
  const outcome = await stopBackend({ child, port: backendPort, token, timeoutMs: STOP_TIMEOUT_MS, log: (m) => log.info(`[stop] ${m}`) });
  log.info(`backend stop outcome: ${outcome}`);
  backend = null;
  rmSync(layout.backendPidFile, { force: true });
}

// ---------------------------------------------------------------------------
// App wiring
// ---------------------------------------------------------------------------

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = mainWindow ?? setupWindow;
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  ipcMain.on(IPC.retry, () => void boot());
  ipcMain.on(IPC.openLogs, () => void shell.openPath(layout.logsDir));
  ipcMain.on(IPC.quit, () => app.quit());

  app.whenReady().then(async () => {
    installMenu({
      logsDir: layout.logsDir,
      dataDir: process.env.DATA_DIR || join(homedir(), ".opencompany"),
      checkForUpdates: () => checkForUpdates(log),
      openAbout: () =>
        void dialog.showMessageBox({
          type: "info",
          title: "About OpenCompany",
          message: `OpenCompany ${app.getVersion()}`,
          detail: `Backend port ${backendPort || "-"}\nData: ${process.env.DATA_DIR || join(homedir(), ".opencompany")}\nLogs: ${layout.logsDir}`,
        }),
    });
    initUpdater({ log, isMac: process.platform === "darwin", isPackaged: app.isPackaged, releasesUrl: RELEASES_URL });
    await boot();
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && backendPort) mainWindow = createMainWindow(backendPort);
  });

  app.on("window-all-closed", () => {
    // The backend keeps running on macOS while the dock icon lives; quit
    // elsewhere. Deployed workflows are Temporal-durable either way.
    if (process.platform !== "darwin") app.quit();
  });

  let shutdownStarted = false;
  app.on("before-quit", (event) => {
    quitting = true;
    if (shutdownStarted || !backend) return;
    event.preventDefault();
    shutdownStarted = true;
    void shutdownBackend().finally(() => app.quit());
  });
}
