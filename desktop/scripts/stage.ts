/**
 * Assemble `stage/` — everything electron-builder ships as extraResources.
 *
 *   stage/app-root/        the backend tree in the sibling layout core.approot expects
 *   stage/runtime/<t>/     uv, python, bun for target <t> (see fetch-runtimes.ts)
 *   stage/manifest.json    what was staged, for the invariant test + release notes
 *
 * The app-root file list is NOT hand-maintained. `bun pm pack --dry-run` at
 * the repo root lists exactly the files the published package ships (the
 * root package.json `files` allowlist filtered by .npmignore), which is the
 * same sibling layout `company start` already runs from. We take that list,
 * drop what a desktop bundle never needs (the CLI, install scripts, client
 * sources, backend tests) and force-include the few files the .npmignore
 * hides but the desktop needs (server/uv.lock).
 *
 * The JS executor sidecar is built here with the sidecar package's own
 * `bun build` script (a self-contained bundle with express inlined, run by
 * the bundled bun at runtime) rather than copied from the checkout, so a
 * stale or missing dist/ in the working tree can never reach a release.
 *
 *   bun run scripts/stage.ts                        # host target runtime
 *   bun run scripts/stage.ts --target mac-arm64,mac-x64
 *   bun run scripts/stage.ts --skip-runtimes        # app-root only
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, posix } from "node:path";

import { DESKTOP_DIR, REPO_ROOT, STAGE_DIR, fail, log, parseTargetArg, readJson, run } from "./_lib";
import { fetchRuntimes, pinnedVersions } from "./fetch-runtimes";

const APP_ROOT = join(STAGE_DIR, "app-root");

/** Prefixes (posix, repo-relative) a desktop bundle has no use for. */
const DROP_PREFIXES = [
  "cli/",
  "bin/",
  "scripts/",
  "client/src/",
  "client/public/",
  "server/tests/",
  "server/scripts/",
  "server/nodejs/", // built below with the sidecar's own bun build
  "server/experiments/",
  "server/.venv/",
];
const DROP_EXACT = new Set(["install.sh", "install.ps1", "pyproject.toml", "client/package.json", "client/index.html", "client/vite.config.js", "client/vitest.config.ts", "client/tsconfig.json", "client/tsconfig.app.json", "client/tsconfig.node.json", "client/eslint.config.js", "client/components.json", "client/tailwind.config.js", "client/postcss.config.js"]);
const DROP_SEGMENTS = ["__pycache__", ".pytest_cache", "node_modules", ".vite"];
// Runtime / test artifacts that .npmignore's `*.db` should hide but npm's
// ignore matcher does not apply to dot-prefixed names (pytest leaves
// `server/.conversation-<hex>.db` behind).
const DROP_SUFFIXES = [".db", ".db-journal", ".db-shm", ".db-wal", ".sqlite", ".log", ".pyc", ".pyo"];

/** Files the .npmignore hides (lockfiles) but the desktop must ship. */
const FORCE_INCLUDE = ["server/uv.lock", "server/pyproject.toml", "server/nodejs/package.json", ".env.template", "package.json"];

const BUN = process.platform === "win32" ? "bun.exe" : "bun";

/** The published file list, from bun's own packer (`packed <size> <path>` lines). */
function packFileList(): string[] {
  run(process.platform === "win32" ? "python" : "python3", ["server/services/builder_documentation.py"], { cwd: REPO_ROOT });
  const out = run(BUN, ["pm", "pack", "--dry-run", "--ignore-scripts"], { cwd: REPO_ROOT });
  const files: string[] = [];
  for (const line of out.split(/\r?\n/)) {
    const m = /^packed\s+\S+\s+(.+?)\s*$/.exec(line);
    if (m) files.push(m[1]!.replace(/\\/g, "/"));
  }
  if (files.length === 0) fail("bun pm pack --dry-run listed no files");
  return files;
}

function keep(path: string): boolean {
  if (DROP_EXACT.has(path)) return false;
  if (DROP_SUFFIXES.some((ext) => path.endsWith(ext))) return false;
  if (DROP_PREFIXES.some((p) => path.startsWith(p))) return false;
  if (path.split("/").some((seg) => DROP_SEGMENTS.includes(seg))) return false;
  if (path.startsWith("client/") && !path.startsWith("client/dist/")) return false;
  return true;
}

function copyInto(rel: string): void {
  const src = join(REPO_ROOT, rel);
  if (!existsSync(src)) fail(`staged file missing from checkout: ${rel} (run \`bun run build\` first?)`);
  const dest = join(APP_ROOT, rel);
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(src, dest);
}

function bundleSidecar(): void {
  const sidecarDir = join(REPO_ROOT, "server", "nodejs");
  const built = join(sidecarDir, "dist", "index.js");
  const outfile = join(APP_ROOT, "server", "nodejs", "dist", "index.js");
  // One definition of the build: the sidecar package's own script
  // (`bun build --target=bun`, which inlines express). It resolves through
  // the sidecar's node_modules, so run it from there.
  run(BUN, ["run", "build"], { cwd: sidecarDir });
  if (!existsSync(built)) fail(`sidecar build produced no ${built}`);
  mkdirSync(dirname(outfile), { recursive: true });
  copyFileSync(built, outfile);
  const text = readFileSync(outfile, "utf-8");
  if (/^\s*(import|export)[^\n]*['"]express['"]/m.test(text) || /require\(["']express["']\)/.test(text)) {
    fail("sidecar bundle still references express externally; inlining failed");
  }
  log(`sidecar bundled -> ${posix.relative(REPO_ROOT.replace(/\\/g, "/"), outfile.replace(/\\/g, "/"))}`);
}

function stageAppRoot(): { files: number } {
  rmSync(APP_ROOT, { recursive: true, force: true });
  mkdirSync(APP_ROOT, { recursive: true });

  const listed = packFileList();
  const kept = listed.filter(keep);
  const all = new Set<string>([...kept, ...FORCE_INCLUDE]);
  for (const rel of all) copyInto(rel);

  if (!existsSync(join(APP_ROOT, "client", "dist", "index.html"))) {
    fail("client/dist/index.html not staged — build the client first (bun run --filter react-flow-client build)");
  }
  bundleSidecar();
  return { files: all.size + 1 };
}

async function main(): Promise<void> {
  const argv = process.argv;
  const targets = parseTargetArg(argv);
  const skipRuntimes = argv.includes("--skip-runtimes");

  const { files } = stageAppRoot();
  log(`app-root staged: ${files} files`);

  if (!skipRuntimes) await fetchRuntimes(targets);

  const rootPkg = readJson<{ version: string }>(join(REPO_ROOT, "package.json"));
  const manifest = {
    stagedAt: new Date().toISOString(),
    appVersion: rootPkg.version,
    targets: skipRuntimes ? [] : targets,
    runtimes: pinnedVersions(),
    appRootFiles: files,
  };
  writeFileSync(join(STAGE_DIR, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  log(`manifest written -> ${join(STAGE_DIR, "manifest.json")}`);
  log(`done (desktop dir: ${DESKTOP_DIR})`);
}

main().catch((err) => fail(String(err?.stack ?? err)));
