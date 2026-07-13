#!/usr/bin/env node
/**
 * Postinstall script for OpenCompany.
 *
 * Runs install.js to check deps, install npm/Python packages, build.
 * WhatsApp RPC is now an npm dependency - binary downloaded by its own postinstall.
 */
import { spawn, execSync } from 'child_process';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { existsSync, chmodSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

// Fix executable permissions on Unix (npm doesn't preserve them from git)
function fixPermissions() {
  if (process.platform === 'win32') return;

  const files = [
    resolve(ROOT, 'bin/cli.js'),
    resolve(ROOT, 'bin/machina.js'),
    resolve(ROOT, 'scripts/install.js'),
    resolve(ROOT, 'install.sh'),
  ];

  for (const file of files) {
    if (existsSync(file)) {
      try {
        chmodSync(file, 0o755);
      } catch (e) {
        // Ignore permission errors
      }
    }
  }
}

const isCI = process.env.CI === 'true' || process.env.GITHUB_ACTIONS === 'true';
const isBuilding = process.env.OPENCOMPANY_BUILDING === 'true'
  || process.env.MACHINAOS_BUILDING === 'true';

if (isCI) {
  console.log('CI detected, skipping postinstall.');
  process.exit(0);
}

if (isBuilding) {
  // build.js is orchestrating -- skip install.js to avoid duplicate work
  fixPermissions();
  process.exit(0);
}

console.log('');
console.log('========================================');
console.log('  OpenCompany - Installing...');
console.log('========================================');
console.log('');

function runScript(scriptPath) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [scriptPath], {
      cwd: ROOT,
      stdio: 'inherit',
      env: { ...process.env, FORCE_COLOR: '1' }
    });

    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`Script exited with code ${code}`));
      }
    });
  });
}

async function main() {
  try {
    // Fix executable permissions on Unix
    fixPermissions();

    // Run full installation
    console.log('Installing dependencies...');
    await runScript(resolve(__dirname, 'install.js'));

    // Note: we deliberately do NOT `pip install -e ./cli` here. An
    // editable install creates a Python console-script entry-point that
    // imports `cli.cli` from the
    // package's npm install directory. When npm later moves, prunes,
    // or upgrades that directory, the shim survives but its target
    // disappears -- the user runs `company start` and gets
    // `ModuleNotFoundError: No module named 'cli'`. The same
    // shim also wins PATH precedence over the npm bin shim at
    // bin/cli.js, masking the real entry point.
    //
    // Both call sites that need the Python CLI use `python -m cli
    // <cmd>` (npm run start, .github/workflows/release.yml version
    // sync). `-m` resolves the package from the working directory's
    // sys.path entry, which `bin/cli.js` already pins to the npm
    // package root via `cwd: ROOT`. No pip install required.

    // Detect a stale `Scripts/machina.exe` (or `bin/machina`) left
    // behind by a prior version's `pip install -e ./machina` (the dir
    // was renamed from `machina/` to `cli/`; pip's metadata can lag).
    // The legacy pip distribution name was ``machina``; the source dir is
    // ``cli/``. If pip shows that old package, surface the cleanup command.
    // The supported public CLI is the npm-provided `company` shim.
    try {
      const pipShow = execSync('python -m pip show machina 2>nul || python3 -m pip show machina 2>/dev/null', {
        encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], shell: true,
      }).trim();
      if (pipShow) {
        console.log('');
        console.log('Notice: a previous install left `machina` registered with pip.');
        console.log('If `machina start` fails with "No module named \'machina\'",');
        console.log('clean it up once with:');
        console.log('  python -m pip uninstall -y machina');
        console.log('');
      }
    } catch {
      // pip not present, or the package isn't installed -- nothing to warn about.
    }

    console.log('');
    console.log('========================================');
    console.log('  OpenCompany installed successfully!');
    console.log('========================================');
    console.log('');
    console.log('Run: company start');
    console.log('Open: http://localhost:3000');
    console.log('');

  } catch (err) {
    console.log('');
    console.log('========================================');
    console.log('  Installation failed!');
    console.log('========================================');
    console.log('');
    console.log(`Error: ${err.message}`);
    console.log('');
    console.log('Try: company build');
    console.log('');
    process.exit(1);
  }
}

main();
