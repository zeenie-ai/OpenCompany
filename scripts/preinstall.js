#!/usr/bin/env node
/**
 * Preinstall cleanup script for OpenCompany.
 *
 * Fixes npm ENOTEMPTY error by cleaning up leftover temp directories
 * that npm fails to remove during failed install/uninstall operations.
 *
 * @see https://github.com/anthropics/claude-code/issues/7373
 * @see https://bobbyhadz.com/blog/npm-err-code-enotempty
 */
import { readdirSync, rmSync, statSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const currentPackageDir = resolve(__dirname, '..');
const legacyTempPrefixes = ['.machina-'];
const scopedTempPrefixes = ['.opencompany-', '.machina-'];
const packageScopes = ['@zeenie', '@zeenie-ai'];

// Skip in CI
if (process.env.CI === 'true' || process.env.GITHUB_ACTIONS === 'true') {
  process.exit(0);
}

// Enforce pnpm for source checkouts (pnpm-workspace.yaml exists).
// Skipped for global npm installs (end users) which don't have the workspace file.
try {
  statSync(resolve(__dirname, '..', 'pnpm-workspace.yaml'));
  const agent = process.env.npm_config_user_agent || '';
  if (!agent.startsWith('pnpm')) {
    console.error('This project requires pnpm. Install it: npm install -g pnpm');
    console.error('Then run: pnpm install');
    process.exit(1);
  }
} catch {
  // No pnpm-workspace.yaml = global npm install, allow npm
}

function getGlobalNodeModules() {
  try {
    const prefix = execSync('npm config get prefix', {
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe']
    }).trim();

    if (prefix) {
      return process.platform === 'win32'
        ? resolve(prefix, 'node_modules')
        : resolve(prefix, 'lib', 'node_modules');
    }
  } catch {
    // Ignore
  }
  return null;
}

function cleanupTempDirectories(parentDir, prefixes) {
  try {
    const entries = readdirSync(parentDir);

    // Clean current and pre-rebrand npm temp directories without deleting the
    // temporary package directory that is running this preinstall script.
    for (const name of entries) {
      if (prefixes.some((prefix) => name.startsWith(prefix))) {
        const fullPath = resolve(parentDir, name);
        if (fullPath === currentPackageDir) continue;

        try {
          if (statSync(fullPath).isDirectory()) {
            rmSync(fullPath, { recursive: true, force: true });
            console.log(`Cleaned: ${fullPath}`);
          }
        } catch {
          // Ignore
        }
      }
    }
  } catch {
    // Missing or unreadable directory is fine.
  }
}

function cleanup() {
  const nodeModules = getGlobalNodeModules();
  if (!nodeModules) return;

  // Only the official legacy `machinaos` package owned a top-level package
  // here. Never remove `.opencompany-*` at this level: the unscoped
  // `opencompany` package belongs to an unrelated publisher.
  cleanupTempDirectories(nodeModules, legacyTempPrefixes);

  // Scoped installs place them inside the package scope. Cover the canonical
  // npmjs scope and the GitHub Packages mirror scope.
  for (const scope of packageScopes) {
    cleanupTempDirectories(resolve(nodeModules, scope), scopedTempPrefixes);
  }
}

cleanup();
