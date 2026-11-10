/**
 * The staged bundle (desktop/stage, produced by scripts/stage.ts) must
 * satisfy every path the backend's core.approot and the shell's paths.ts
 * assume. Run after `bun run stage` and before packaging; CI fails the
 * release if the tree drifts from what cli/commands/build.py produces.
 */

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { resolveLayout, targetKey } from "@main/paths";

const DESKTOP = resolve(__dirname, "..", "..");
const STAGE = join(DESKTOP, "stage");
const APP_ROOT = join(STAGE, "app-root");
const RUNTIME = join(STAGE, "runtime", targetKey(process.platform, process.arch));
const staged = existsSync(join(APP_ROOT, "server", "main.py"));
const runtimesStaged = existsSync(RUNTIME);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

describe.skipIf(!staged)("staged app-root", () => {
  it("contains the sibling layout core.approot expects", () => {
    for (const rel of [
      "server/main.py",
      "server/core/approot.py",
      "server/core/desktop.py",
      "server/pyproject.toml",
      "server/uv.lock",
      "server/nodejs/dist/index.js",
      "server/nodejs/package.json",
      "server/pi/dist/index.js",
      "server/pi/dist/package.json",
      "server/pi/dist/THIRD-PARTY-LICENSES.txt",
      "client/dist/index.html",
      ".env.template",
      "package.json",
      ".opencompany/workflows",
    ]) {
      expect(existsSync(join(APP_ROOT, rel)), rel).toBe(true);
    }
    expect(readdirSync(join(APP_ROOT, ".opencompany", "workflows")).filter((f) => f.endsWith(".json")).length).toBeGreaterThan(0);
    expect(readdirSync(join(APP_ROOT, "client", "dist", "assets")).length).toBeGreaterThan(0);
  });

  it("ships no venv, caches, tests, CLI or client sources", () => {
    const files = walk(APP_ROOT).map((p) => p.slice(APP_ROOT.length + 1).replace(/\\/g, "/"));
    const leaked = files.filter(
      (f) =>
        f.startsWith("server/.venv/") ||
        f.includes("/__pycache__/") ||
        f.endsWith(".pyc") ||
        f.startsWith("server/tests/") ||
        f.startsWith("cli/") ||
        f.startsWith("bin/") ||
        f.startsWith("scripts/") ||
        f.startsWith("client/src/") ||
        f.includes("/node_modules/") ||
        f.endsWith(".db"),
    );
    expect(leaked).toEqual([]);
  });

  it("bundled the JS executor sidecar with express inlined", () => {
    const js = readFileSync(join(APP_ROOT, "server", "nodejs", "dist", "index.js"), "utf-8");
    expect(js.length).toBeGreaterThan(50_000);
    expect(/from\s+["']express["']/.test(js)).toBe(false);
    expect(/require\(["']express["']\)/.test(js)).toBe(false);
  });

  it("ships the Pi host as one bundle with the SDK inlined", () => {
    const pi = join(APP_ROOT, "server", "pi");
    const js = readFileSync(join(pi, "dist", "index.js"), "utf-8");
    expect(/from\s+["']@earendil-works\//.test(js)).toBe(false);
    const sdk = JSON.parse(readFileSync(join(pi, "dist", "package.json"), "utf-8")) as { name: string; version: string };
    expect(sdk.name).toBe("@earendil-works/pi-coding-agent");
    expect(sdk.version).toBe("1.1.0");
    expect(readdirSync(join(pi, "dist")).sort()).toEqual(["THIRD-PARTY-LICENSES.txt", "index.js", "package.json"]);
    expect(readdirSync(pi)).toEqual(["dist"]);
  });

  it("carries the version the root package.json declares", () => {
    const root = JSON.parse(readFileSync(join(DESKTOP, "..", "package.json"), "utf-8")) as { version: string };
    const stagedPkg = JSON.parse(readFileSync(join(APP_ROOT, "package.json"), "utf-8")) as { version: string };
    expect(stagedPkg.version).toBe(root.version);
    const manifest = JSON.parse(readFileSync(join(STAGE, "manifest.json"), "utf-8")) as { appVersion: string; runtimes: Record<string, string> };
    expect(manifest.appVersion).toBe(root.version);
    expect(manifest.runtimes.python ?? "").toMatch(/^3\.12\./);
    expect(manifest.runtimes.bun ?? "").toMatch(/^\d+\.\d+\.\d+$/);
    expect(manifest.runtimes.node).toBeUndefined();
  });
});

describe.skipIf(!staged || !runtimesStaged)("staged runtimes", () => {
  const layout = resolveLayout({ resources: STAGE, userData: join(STAGE, "_ud"), runtimeDir: RUNTIME });

  it("has uv, python and bun where paths.ts looks, and no node or npm", () => {
    expect(existsSync(layout.uvBin), layout.uvBin).toBe(true);
    expect(existsSync(layout.pythonBin), layout.pythonBin).toBe(true);
    expect(existsSync(layout.bunBin), layout.bunBin).toBe(true);
    expect(existsSync(join(layout.bunDir, "LICENSE.md"))).toBe(true);
    expect(existsSync(join(layout.runtimeDir, "node"))).toBe(false);
  });

  it("runs the bundled interpreters", () => {
    const py = spawnSync(layout.pythonBin, ["--version"], { encoding: "utf-8" });
    expect(py.status, py.stderr).toBe(0);
    expect(`${py.stdout}${py.stderr}`).toMatch(/Python 3\.12\./);
    const bun = spawnSync(layout.bunBin, ["--version"], { encoding: "utf-8" });
    expect(bun.status).toBe(0);
    expect(bun.stdout.trim()).toMatch(/^\d+\.\d+\.\d+$/);
    const uv = spawnSync(layout.uvBin, ["--version"], { encoding: "utf-8" });
    expect(uv.status).toBe(0);
    expect(uv.stdout).toMatch(/^uv \d+\.\d+\.\d+/);
  });

  it("the bundled sidecar boots on the bundled bun with no node on PATH", async () => {
    const { spawn } = await import("node:child_process");
    const port = 5600 + Math.floor(Math.random() * 300);
    const token = "staged-tree-test-token";
    const pathKey = Object.keys(process.env).find((k) => k.toUpperCase() === "PATH") ?? "PATH";
    const strippedPath = (process.env[pathKey] ?? "")
      .split(process.platform === "win32" ? ";" : ":")
      .filter((p) => !/nodejs|[\\/]npm|pnpm|\.nvm|fnm|volta/i.test(p))
      .join(process.platform === "win32" ? ";" : ":");
    const child = spawn(layout.bunBin, [join(APP_ROOT, "server", "nodejs", "dist", "index.js")], {
      cwd: join(APP_ROOT, "server", "nodejs"),
      env: {
        ...process.env,
        [pathKey]: strippedPath,
        NODEJS_EXECUTOR_PORT: String(port),
        NODEJS_EXECUTOR_HOST: "127.0.0.1",
        NODEJS_EXECUTOR_TOKEN: token,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (d) => (output += d));
    child.stderr.on("data", (d) => (output += d));
    try {
      let ok = false;
      const deadline = Date.now() + 15_000;
      while (Date.now() < deadline && !ok) {
        try {
          const res = await fetch(`http://127.0.0.1:${port}/health`);
          ok = res.ok;
        } catch {
          await new Promise((r) => setTimeout(r, 200));
        }
      }
      expect(ok, output).toBe(true);
      const body = JSON.stringify({ code: "const n: number = 21; output = n * 2;", language: "typescript" });
      // /execute runs code only for a request carrying the token the
      // sidecar was started with.
      const refused = await fetch(`http://127.0.0.1:${port}/execute`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      });
      expect(refused.status).toBe(401);
      // TypeScript is type-stripped by Bun.Transpiler inside the sidecar; a
      // Node-hosted sidecar would fail this with a SyntaxError.
      const res = await fetch(`http://127.0.0.1:${port}/execute`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-opencompany-executor-token": token },
        body,
      });
      expect(((await res.json()) as { output: unknown }).output).toBe(42);
    } finally {
      child.kill();
    }
  });
});
