#!/usr/bin/env node
/**
 * Cross-platform start script for MachinaOS services.
 * Works on: Windows, macOS, Linux, WSL, Git Bash
 */
import { execSync, spawn } from 'child_process';
import { readFileSync, existsSync, copyFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const START_TIME = Date.now();

// Parse command line arguments
const args = process.argv.slice(2);
const isDaemonMode = args.includes('--daemon');
const skipWhatsApp = args.includes('--skip-whatsapp');

// ============================================================================
// Platform Detection
// ============================================================================
const isWindows = process.platform === 'win32';
const isGitBash = isWindows && (process.env.MSYSTEM || process.env.SHELL?.includes('bash'));
const isMac = process.platform === 'darwin';
const useUnixCommands = !isWindows || isGitBash;

function getPlatformName() {
  if (isGitBash) return 'Git Bash';
  if (isWindows) return 'Windows';
  if (isMac) return 'macOS';
  return 'Linux';
}

// ============================================================================
// Utilities
// ============================================================================

const elapsed = () => `${((Date.now() - START_TIME) / 1000).toFixed(2)}s`;
const log = (msg) => console.log(`[${elapsed()}] ${msg}`);

function exec(cmd, options = {}) {
  try {
    return execSync(cmd, {
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: options.timeout || 10000,
      cwd: ROOT,
      ...options
    }).trim();
  } catch {
    return '';
  }
}

function sleep(ms) {
  try {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
  } catch {
    const end = Date.now() + ms;
    while (Date.now() < end) { /* spin */ }
  }
}

function loadConfig() {
  const envPath = existsSync(resolve(ROOT, '.env'))
    ? resolve(ROOT, '.env')
    : resolve(ROOT, '.env.template');

  const env = {};
  if (existsSync(envPath)) {
    for (const line of readFileSync(envPath, 'utf-8').split('\n')) {
      const match = line.match(/^([^#=]+)=(.*)$/);
      if (match) {
        env[match[1].trim()] = match[2].trim().replace(/^["']|["']$/g, '');
      }
    }
  }

  return {
    ports: [
      parseInt(env.VITE_CLIENT_PORT) || 3000,
      parseInt(env.PYTHON_BACKEND_PORT) || 3010,
      parseInt(env.WHATSAPP_RPC_PORT) || 9400
    ],
    temporalEnabled: env.TEMPORAL_ENABLED?.toLowerCase() === 'true'
  };
}

// ============================================================================
// Port Management
// ============================================================================

function getPidsOnPort(port) {
  const pids = new Set();

  if (useUnixCommands) {
    const output = exec(`lsof -ti:${port} -sTCP:LISTEN 2>/dev/null`);
    for (const pid of output.split('\n')) {
      if (pid.trim() && /^\d+$/.test(pid.trim())) {
        pids.add(pid.trim());
      }
    }
    if (pids.size === 0 && !isMac && !isGitBash) {
      const ssOutput = exec(`ss -tlnp 2>/dev/null | grep :${port}`);
      for (const match of ssOutput.matchAll(/pid=(\d+)/g)) {
        pids.add(match[1]);
      }
    }
  } else {
    const output = exec(`netstat -ano | findstr :${port} | findstr LISTENING`);
    for (const line of output.split('\n')) {
      const parts = line.trim().split(/\s+/);
      const pid = parts[parts.length - 1];
      if (pid && /^\d+$/.test(pid) && pid !== '0') {
        pids.add(pid);
      }
    }
  }

  return Array.from(pids);
}

function killPid(pid) {
  if (useUnixCommands) {
    exec(`kill -15 ${pid} 2>/dev/null`);
    sleep(100);
    exec(`kill -9 ${pid} 2>/dev/null`);
  } else {
    // /T kills process tree (parent + children), needed for uvicorn --reload
    exec(`taskkill /PID ${pid} /T /F 2>nul`);
  }
}

function freePort(port) {
  const pids = getPidsOnPort(port);
  if (pids.length === 0) return true;

  for (const pid of pids) {
    killPid(pid);
  }
  sleep(500);

  return getPidsOnPort(port).length === 0;
}

// ============================================================================
// Main
// ============================================================================

// Check if build has been run (before anything else)
const rootNodeModules = resolve(ROOT, 'node_modules');
const pythonVenv = resolve(ROOT, 'server', '.venv');
if (!existsSync(rootNodeModules) || !existsSync(pythonVenv)) {
  console.error('\nError: Project not built. Run "npm run build" first.\n');
  process.exit(1);
}

const config = loadConfig();

// Ensure Python UTF-8 encoding
process.env.PYTHONUTF8 = '1';

console.log('\n=== MachinaOS Starting ===\n');
log(`Platform: ${getPlatformName()}`);
log(`Mode: ${isDaemonMode ? 'Daemon (Gunicorn)' : 'Development (uvicorn)'}`);
log(`Ports: ${config.ports.join(', ')}`);
log(`Temporal: ${config.temporalEnabled ? 'enabled' : 'disabled'}`);
log(`WhatsApp: ${skipWhatsApp ? 'skipped' : 'enabled'}`);

// Create .env if not exists
const envPath = resolve(ROOT, '.env');
const templatePath = resolve(ROOT, '.env.template');
if (!existsSync(envPath) && existsSync(templatePath)) {
  copyFileSync(templatePath, envPath);
  log('Created .env from template');
}

// Free ports
log('Freeing ports...');
let allFree = true;
for (const port of config.ports) {
  const pids = getPidsOnPort(port);
  if (pids.length > 0) {
    const freed = freePort(port);
    if (freed) {
      log(`  Port ${port}: Freed (killed PIDs: ${pids.join(', ')})`);
    } else {
      log(`  Port ${port}: Warning - could not free`);
      allFree = false;
    }
  } else {
    log(`  Port ${port}: Already free`);
  }
}

if (!allFree) {
  log('Warning: Some ports could not be freed. Services may fail to start.');
}

// Start services
log('Starting services...');
log('Press Ctrl+C to stop (use npm run stop to kill all services)\n');

// Detect if we should run dev mode (vite) or production mode (static serve)
// Check for vite in both root (npm workspaces hoist here) and client node_modules
const clientDist = resolve(ROOT, 'client', 'dist', 'index.html');
const hasVite = existsSync(resolve(ROOT, 'node_modules', 'vite'))
  || existsSync(resolve(ROOT, 'client', 'node_modules', 'vite'));
const isProduction = existsSync(clientDist) && !hasVite;

if (isProduction) {
  log('Mode: Production (serving pre-built client)');
} else {
  log('Mode: Development (running vite)');
}

// Build concurrently command based on config
const services = [];

// Client: use static server in production, vite in development
if (isProduction) {
  // Use dedicated serve-client.js script
  services.push(`"node ${resolve(ROOT, 'scripts', 'serve-client.js').replace(/\\/g, '/')}"`);
} else {
  services.push('npm:client:start');
}

// Python backend: use gunicorn in daemon mode, uvicorn in development
services.push(isDaemonMode ? 'npm:python:daemon' : 'npm:python:start');

// WhatsApp RPC service (required)
if (!skipWhatsApp) {
  services.push('npm:whatsapp:api');
}

if (config.temporalEnabled) {
  services.push('npm:temporal:worker');
}

const concurrentlyArgs = [
  '--raw',
  '--kill-others-on-fail',
  ...services
];

// Use spawn with shell:true for cross-platform compatibility
const proc = spawn('npx', ['concurrently', ...concurrentlyArgs], {
  cwd: ROOT,
  stdio: 'inherit',
  shell: true,
  env: { ...process.env, FORCE_COLOR: '1' }
});

// Handle Ctrl+C gracefully
process.on('SIGINT', () => {
  proc.kill('SIGINT');
});
process.on('SIGTERM', () => {
  proc.kill('SIGTERM');
});

proc.on('close', (code) => {
  console.log('\nServices stopped.');
  process.exit(code || 0);
});
