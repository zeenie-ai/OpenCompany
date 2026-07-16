#!/usr/bin/env node

import { spawn, execSync } from 'child_process';
import { basename, dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { readFileSync, existsSync } from 'fs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PKG = JSON.parse(readFileSync(resolve(ROOT, 'package.json'), 'utf-8'));
const invokedName = basename(process.argv[1] || '').replace(/\.(?:cmd|exe|ps1|js)$/i, '');
const USING_LEGACY_ALIAS = invokedName === 'machina'
  || process.env.OPENCOMPANY_LEGACY_ALIAS === 'machina';

const COMMANDS = {
  start: 'Start in production mode',
  dev: 'Start development server (hot-reload)',
  serve: 'Serve on a single public port (API + WS + SPA; used by deploy)',
  deploy: 'Provision a cloud VM running OpenCompany (Terraform)',
  stop: 'Stop all running services',
  build: 'Build the project for production',
  clean: 'Clean build artifacts',
  doctor: 'Check system dependencies and project health',
  help: 'Show this help message',
  version: 'Show version number',
};

function printHelp() {
  console.log(`
OpenCompany - Workflow Automation Platform

Usage: company <command> [flags]

Commands:
${Object.entries(COMMANDS).map(([cmd, desc]) => `  ${cmd.padEnd(14)} ${desc}`).join('\n')}

Flags:
  --daemon         Bind the backend to 0.0.0.0 instead of 127.0.0.1 (dev)

Examples:
  company start          # Production server (clean output)
  company dev            # Development with hot-reload
  company build          # Build for production

Compatibility: the legacy \`machina\` command remains available but is deprecated.
Documentation: https://docs.zeenie.xyz/
`);
}

function getVersion(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
  } catch {
    return null;
  }
}

// Add common binary paths to PATH (Linux installs uv to ~/.local/bin)
function expandPath() {
  const home = process.env.HOME || process.env.USERPROFILE;
  if (home) {
    const additionalPaths = [
      `${home}/.local/bin`,      // uv, cargo installs
      `${home}/.cargo/bin`,      // Rust tools
      '/usr/local/bin',          // Homebrew on macOS
    ];
    const currentPath = process.env.PATH || '';
    const sep = process.platform === 'win32' ? ';' : ':';
    const newPaths = additionalPaths.filter(p => !currentPath.includes(p));
    if (newPaths.length > 0) {
      process.env.PATH = newPaths.join(sep) + sep + currentPath;
    }
  }
}

function checkDeps() {
  const errors = [];

  // Node.js version check
  const nodeVersion = parseInt(process.version.slice(1));
  if (nodeVersion < 22) {
    errors.push(`Node.js 22+ required (found ${process.version})`);
  }

  // Python version check
  let pyVersion = getVersion('python --version') || getVersion('python3 --version');
  if (!pyVersion) {
    errors.push('Python 3.12+ - https://python.org/');
  } else {
    const match = pyVersion.match(/Python (\d+)\.(\d+)/);
    if (match) {
      const [, major, minor] = match.map(Number);
      if (major < 3 || (major === 3 && minor < 12)) {
        errors.push(`Python 3.12+ required (found ${pyVersion})`);
      }
    }
  }

  // uv package manager check
  if (!getVersion('uv --version')) {
    errors.push('uv (Python package manager) - https://docs.astral.sh/uv/');
  }

  if (errors.length > 0) {
    console.error('Missing required dependencies:\n' + errors.map(e => `  - ${e}`).join('\n'));
    console.error('\nInstall the missing dependencies and try again.');
    process.exit(1);
  }
}

function doctor() {
  console.log('\nOpenCompany Doctor\n');
  try {
    execSync('npx envinfo --system --binaries --npmPackages edgymeow,agent-browser,cross-env', {
      cwd: ROOT, stdio: 'inherit', shell: true,
    });
  } catch { /* envinfo not available, continue with manual checks */ }

  console.log('  Additional checks:');
  const checks = [
    ['uv', getVersion('uv --version')],
    ['temporal', getVersion('temporal --version')],
  ];
  for (const [name, ver] of checks) {
    console.log(ver ? `    ${name}: ${ver}` : `    ${name}: Not Found`);
  }

  const has = (f) => { try { readFileSync(resolve(ROOT, f)); return true; } catch { return false; } };
  console.log(has('pnpm-lock.yaml') ? '    Lockfile: pnpm-lock.yaml' : has('package-lock.json') ? '    Lockfile: package-lock.json' : '    Lockfile: Not Found');
  console.log(has('server/.venv/pyvenv.cfg') ? '    Python venv: OK' : '    Python venv: Missing (run company build)');
  console.log('');
}

// Resolve <ROOT>/.cli-venv Python if the postinstall step provisioned it.
// Returns null on source checkouts (no venv -> fall back to ``npm run``).
function venvPython() {
  const py = process.platform === 'win32'
    ? resolve(ROOT, '.cli-venv', 'Scripts', 'python.exe')
    : resolve(ROOT, '.cli-venv', 'bin', 'python');
  return existsSync(py) ? py : null;
}

function run(script, extraArgs = []) {
  // Global-install fast path: spawn the venv's Python directly with
  // ``-m cli <cmd>``. Skips the ``npm run`` shim that previously re-
  // resolved the system ``python`` (which on PEP 668 systems lacks
  // the CLI runtime deps -- typer/rich/anyio/psutil). The npm-run
  // path stays as the source-checkout fallback (``pnpm run start``
  // uses package.json scripts directly).
  const venvPy = venvPython();
  if (venvPy) {
    const child = spawn(venvPy, ['-m', 'cli', script, ...extraArgs], {
      cwd: ROOT,
      stdio: 'inherit',
    });
    child.on('error', (e) => { console.error(`Failed: ${e.message}`); process.exit(1); });
    child.on('close', (code) => process.exit(code || 0));
    return;
  }

  const npmArgs = ['run', script];
  if (extraArgs.length) npmArgs.push('--', ...extraArgs);
  const child = spawn(process.platform === 'win32' ? 'npm.cmd' : 'npm', npmArgs, {
    cwd: ROOT,
    stdio: 'inherit',
    shell: true,
  });
  child.on('error', (e) => { console.error(`Failed: ${e.message}`); process.exit(1); });
  child.on('close', (code) => process.exit(code || 0));
}

// Expand PATH to find tools like uv installed in user directories
expandPath();

if (USING_LEGACY_ALIAS) {
  console.warn('Warning: `machina` is deprecated; use `company` instead.');
}

const cmd = process.argv[2] || 'help';

if (cmd === 'help' || cmd === '--help' || cmd === '-h') {
  printHelp();
} else if (cmd === 'version' || cmd === '--version' || cmd === '-v') {
  console.log(`company v${PKG.version}`);
} else if (cmd === 'doctor') {
  doctor();
} else if (cmd === 'start' || cmd === 'dev' || cmd === 'build' || cmd === 'serve') {
  checkDeps();
  run(cmd, process.argv.slice(3));
} else if (cmd === 'deploy') {
  // Needs sub-verb + flags forwarded (up/status/destroy --provider ...).
  run(cmd, process.argv.slice(3));
} else if (COMMANDS[cmd]) {
  run(cmd);
} else {
  console.error(`Unknown command: ${cmd}`);
  printHelp();
  process.exit(1);
}
