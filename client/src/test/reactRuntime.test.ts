// @vitest-environment node

import { cpSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { resolveConfig } from 'vite';
import { expect, it, vi } from 'vitest';

it('renders compiler-generated hooks from a package with its own React copy', async () => {
  const clientRoot = resolve(import.meta.dirname, '../..');
  const clientRequire = createRequire(join(clientRoot, 'package.json'));
  // Keep Vite/esbuild in Node's realm; only the renderer needs a DOM.
  const { JSDOM } = clientRequire('jsdom');
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  vi.stubGlobal('window', dom.window);
  vi.stubGlobal('document', dom.window.document);
  vi.stubGlobal('navigator', dom.window.navigator);
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubEnv('VITE_CLIENT_PORT', '5678');
  vi.stubEnv('PYTHON_BACKEND_PORT', '5679');
  const fixture = mkdtempSync(join(tmpdir(), 'opencompany-react-runtime-'));
  const importer = join(fixture, 'HomeShell.js');
  let unmount: (() => void) | undefined;
  try {
    // Linked and isolated packages may resolve a second copy even when the
    // version matches. Its compiler hook sees a null renderer dispatcher.
    cpSync(dirname(clientRequire.resolve('react/package.json')), join(fixture, 'node_modules/react'), { recursive: true });
    writeFileSync(importer, 'export default function HomeShell() {}');
    const config = await resolveConfig({ configFile: join(clientRoot, 'vite.config.js'), root: clientRoot }, 'serve', 'test');
    const resolveRuntime = config.createResolver();
    const compilerPath = await resolveRuntime('react/compiler-runtime', importer);
    expect(compilerPath).toBeDefined();
    const compiler = clientRequire(compilerPath!);
    const React = clientRequire('react') as typeof import('react');
    const { createRoot } = clientRequire('react-dom/client') as typeof import('react-dom/client');
    const container = document.createElement('div');
    const root = createRoot(container);
    unmount = () => root.unmount();
    function CompiledHome() {
      // This is the first hook emitted by React Compiler, before user hooks.
      compiler.c(1);
      return React.createElement('p', null, 'Home is ready');
    }
    await React.act(async () => root.render(React.createElement(CompiledHome)));
    expect(container.textContent).toBe('Home is ready');
    await React.act(async () => root.unmount());
    unmount = undefined;

    for (const id of ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react/compiler-runtime', 'react-dom/client']) {
      expect(await resolveRuntime(id, importer)).toBe(await resolveRuntime(id, join(clientRoot, 'src/main.tsx')));
    }
  } finally {
    unmount?.();
    rmSync(fixture, { recursive: true, force: true });
    dom.window.close();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  }
});
