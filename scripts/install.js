#!/usr/bin/env node
/**
 * OpenCompany Installation Script
 *
 * Called by postinstall.js after npm install.
 * Installs all dependencies including Python and uv.
 * WhatsApp RPC is now an npm dependency with pre-built binaries.
 */
import { execSync } from 'child_process';
import { existsSync, copyFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

// Prevent recursive execution when npm install runs in subdirectories
if (
  process.env.OPENCOMPANY_INSTALLING === 'true'
  || process.env.MACHINAOS_INSTALLING === 'true'
) {
  process.exit(0);
}
process.env.OPENCOMPANY_INSTALLING = 'true';
// Keep mixed-version nested installs from invoking an older hook recursively.
process.env.MACHINAOS_INSTALLING = 'true';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

process.env.PYTHONUTF8 = '1';

function run(cmd, cwd = ROOT, timeoutMs = 300000) {
  // Strip VIRTUAL_ENV from the spawned env. When the user runs
  // ``npm install -g @zeenie/opencompany`` from a shell that has activated a
  // venv (very common during dev), uv emits a noisy ``VIRTUAL_ENV
  // ... does not match the project environment path`` warning per
  // invocation. uv only honours VIRTUAL_ENV with ``--active``, which
  // we never pass, so dropping it at the source is the documented
  // workaround. Same fix applied to cli/supervisor.py's _full_env.
  const { VIRTUAL_ENV, ...cleanEnv } = process.env;
  execSync(cmd, {
    cwd,
    stdio: 'inherit',
    shell: true,
    timeout: timeoutMs,
    env: {
      ...cleanEnv,
      OPENCOMPANY_INSTALLING: 'true',
      MACHINAOS_INSTALLING: 'true',
    }
  });
}

function runSilent(cmd) {
  try {
    execSync(cmd, { stdio: 'pipe', shell: true });
    return true;
  } catch {
    return false;
  }
}

function getVersion(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'], shell: true }).trim();
  } catch {
    return null;
  }
}

function checkPython() {
  for (const cmd of ['python3', 'python']) {
    const version = getVersion(`${cmd} --version`);
    if (version) {
      const match = version.match(/Python (\d+)\.(\d+)/);
      if (match) {
        const [, major, minor] = match.map(Number);
        if (major >= 3 && minor >= 12) {
          return { cmd, version };
        }
      }
    }
  }
  return null;
}

function checkUv() {
  return getVersion('uv --version');
}

function ensurePip(pythonCmd) {
  // Check if pip exists, install via ensurepip if missing
  if (!runSilent(`${pythonCmd} -m pip --version`)) {
    console.log('Installing pip via ensurepip...');
    run(`${pythonCmd} -m ensurepip --upgrade`);
  }
}

function installUv(pythonCmd) {
  ensurePip(pythonCmd);
  console.log('Installing uv via pip...');
  run(`${pythonCmd} -m pip install uv`);
}

// ============================================================================
// Main
// ============================================================================

console.log('');
console.log('Checking dependencies...');
console.log('');
console.log(`  Node.js: ${getVersion('node --version')}`);
console.log(`  npm: ${getVersion('npm --version')}`);

// Check Python (required, user must install)
let python = checkPython();
if (python) {
  console.log(`  Python: ${python.version}`);
} else {
  console.log('ERROR: Python 3.12+ is required.');
  console.log('  Install from: https://python.org/downloads/');
  process.exit(1);
}

// Check/Install uv
let uvVersion = checkUv();
if (uvVersion) {
  console.log(`  uv: ${uvVersion}`);
} else {
  installUv(python.cmd);
  uvVersion = checkUv();
  if (uvVersion) {
    console.log(`  uv: ${uvVersion}`);
  } else {
    console.log('ERROR: uv installation failed');
    process.exit(1);
  }
}

// Temporal binary: downloaded eagerly during install (step [6/6])
// by ``python -m services.temporal._install``, the same call that
// ``company build`` already makes. The pooch cache
// (~/.cache/OpenCompany/...)
// makes re-runs sub-second. Done eagerly because
// ``TemporalServerRuntime._pre_spawn`` unconditionally calls
// ``ensure_temporal_binaries`` -- the runtime always uses the
// pooch-downloaded binary regardless of any system ``temporal`` on
// PATH -- so pre-fetching here eliminates a 30-90 s stall on the
// user's first ``company start``.
let temporalVersion = getVersion('temporal --version');
console.log(
  temporalVersion
    ? `  temporal: ${temporalVersion} (system install, pooch copy installed below)`
    : '  temporal: not on PATH, pooch copy installed below',
);

// agent-browser is managed by the Python backend
// (server/nodes/browser/_install.py) — npm-installed into
// platformdirs.user_cache_path("OpenCompany")/browser/npm/ on first
// use, with the Chromium runtime fetched by ``agent-browser install``
// when the browser node first spawns. No npm dep, no postinstall step.

console.log('');
console.log('Installing...');
console.log('');

try {
  const clientDir = resolve(ROOT, 'client');
  const serverDir = resolve(ROOT, 'server');
  const clientDistExists = existsSync(resolve(clientDir, 'dist', 'index.html'));

  // Calculate total steps
  let totalSteps = 1;  // .env always
  if (!clientDistExists) totalSteps += 2;  // client deps + build
  totalSteps += 4;  // Python deps + bytecode compile + CLI venv + Temporal binary
  let step = 0;

  // Create .env if needed
  step++;
  const envPath = resolve(ROOT, '.env');
  const templatePath = resolve(ROOT, '.env.template');
  if (!existsSync(envPath) && existsSync(templatePath)) {
    copyFileSync(templatePath, envPath);
    console.log(`[${step}/${totalSteps}] Created .env from template`);
  } else {
    console.log(`[${step}/${totalSteps}] .env exists`);
  }

  // Skip client install/build if dist already exists (pre-built in npm package)
  if (clientDistExists) {
    console.log(`[SKIP] Client already built (dist/index.html exists)`);
  } else {
    // Install client dependencies
    step++;
    console.log(`[${step}/${totalSteps}] Installing client dependencies...`);
    run('npm install', clientDir, 600000);  // 10 min timeout

    // Build client
    step++;
    console.log(`[${step}/${totalSteps}] Building client...`);
    run('npm run build', clientDir, 600000);  // 10 min timeout
  }

  // Install Python dependencies (always needed - venv not included in package)
  step++;
  console.log(`[${step}/${totalSteps}] Installing Python dependencies...`);
  // Check if .venv exists, skip creation if it does
  const venvPath = resolve(serverDir, '.venv');
  if (!existsSync(venvPath)) {
    run('uv venv', serverDir);  // 5 min default
  }
  run('uv sync', serverDir, 600000);  // 10 min timeout

  // Pre-compile our Python sources to optimised bytecode (.opt-1.pyc).
  // `-O` strips assertions and `__debug__` branches; `-q` silences
  // per-file output; `-j 0` parallelises across CPU cores. Scoped to
  // our own source dirs — `uv sync` already compiles `.venv/` and
  // some site-packages contain non-Python template files that would
  // log spurious errors. Failure is non-fatal: the runtime regenerates
  // missing .pyc on first import. Trims a few seconds off cold start.
  step++;
  console.log(`[${step}/${totalSteps}] Compiling Python bytecode...`);
  try {
    run('uv run python -O -m compileall -q -j 0 services core nodes routers models middleware main.py constants.py', serverDir, 120000);
  } catch (err) {
    console.log(`  Warning: bytecode compilation failed (non-fatal): ${err.message}`);
  }

  // Provision a private venv for the CLI runtime deps (typer, rich,
  // anyio, psutil, platformdirs, pywin32-on-Windows). ``python -m cli``
  // re-execs itself under this venv (see ``cli/__main__.py``), so the
  // system Python never needs the deps -- avoids the PEP 668
  // ``externally-managed-environment`` failure on Ubuntu 24.04+,
  // Debian 12+, Homebrew Python, NixOS, etc.
  // (https://peps.python.org/pep-0668/)
  step++;
  console.log(`[${step}/${totalSteps}] Provisioning CLI runtime venv...`);
  const cliVenvDir = resolve(ROOT, '.cli-venv');
  const cliVenvPython = process.platform === 'win32'
    ? resolve(cliVenvDir, 'Scripts', 'python.exe')
    : resolve(cliVenvDir, 'bin', 'python');
  if (!existsSync(cliVenvPython)) {
    run(`uv venv "${cliVenvDir}"`, ROOT);
  }
  run(`uv pip install --python "${cliVenvPython}" --quiet -e .`, ROOT);

  // Eagerly fetch the official Temporal CLI binary (~90 MB tarball
  // from temporal.download/cli/archive/latest). Same call ``company
  // build`` step [6/6] makes. The runtime supervisor unconditionally
  // uses this pooch-cached copy via
  // ``TemporalServerRuntime._pre_spawn`` -- it ignores any system
  // ``temporal`` on PATH -- so pre-fetching at install time turns a
  // 30-90 s stall on first ``company start`` into a sub-second
  // cache hit. Idempotent on re-install (pooch cache).
  step++;
  console.log(`[${step}/${totalSteps}] Installing Temporal binaries...`);
  // Non-fatal: TemporalServerRuntime._pre_spawn() re-runs this download
  // lazily on first `company start` — a failed fetch must never fail
  // `npm install`.
  try {
    run('uv run python -m services.temporal._install', serverDir, 600000);
  } catch (err) {
    console.log(`  Warning: Temporal CLI download failed (non-fatal): ${err.message}`);
    console.log('  It will be fetched automatically on first `company start`.');
  }

  // WhatsApp RPC is now an npm dependency - binary downloaded via postinstall
  console.log('');
  console.log('Done!');
  console.log('');
  console.log('WhatsApp RPC installed as npm dependency (edgymeow)');

} catch (err) {
  console.log('');
  console.log(`Failed: ${err.message}`);
  process.exit(1);
}
