// Parse authored diagrams with the exact Mermaid version in bun.lock.
// No browser, image generation, runtime services or production dependency changes.
import { readFile, readdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const lock = await readFile(resolve(root, 'bun.lock'), 'utf8');
const version = lock.match(/"mermaid":\s*\["mermaid@([^\"]+)"/)?.[1];
if (!version) throw new Error('Mermaid is missing from bun.lock');
try {
  const catalog = JSON.parse(await readFile(resolve(root, 'docs/diagrams/architecture/catalog.json'), 'utf8'));
  if (catalog.mermaid_version !== version) throw new Error('Refresh diagram validation for the newly locked Mermaid version');
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
const modulePath = process.env.ARCHITECTURE_MERMAID_MODULE
  ?? resolve(root, `node_modules/.bun/mermaid@${version}/node_modules/mermaid/dist/mermaid.core.mjs`);
const packagePath = resolve(dirname(modulePath), '../package.json');
const installed = JSON.parse(await readFile(packagePath, 'utf8'));
if (installed.version !== version) throw new Error(`Expected Mermaid ${version}; got ${installed.version}`);
// Reuse the existing frontend test DOM; initialize it before DOMPurify imports.
// The environment override supports isolated Git worktrees without installing again.
const require = createRequire(process.env.ARCHITECTURE_JS_TOOLS_ROOT
  ? resolve(process.env.ARCHITECTURE_JS_TOOLS_ROOT, 'client/package.json')
  : resolve(root, 'client/package.json'));
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const { default: mermaid } = await import(pathToFileURL(modulePath).href);
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', flowchart: { htmlLabels: false } });
const directory = resolve(root, 'docs/diagrams/architecture');
const files = (await readdir(directory)).filter(name => name.endsWith('.mmd')).sort();
if (files.length !== 16) throw new Error(`Expected 16 editable diagrams; got ${files.length}`);
let failures = 0;
for (const name of files) {
  try {
    await mermaid.parse(await readFile(resolve(directory, name), 'utf8'));
  } catch (error) {
    console.error(`${name}: ${error.message}`);
    failures += 1;
  }
}
dom.window.close();
if (failures) process.exitCode = 1;
else console.log(`OK: ${files.length} Mermaid sources parse with locked version ${version}`);
