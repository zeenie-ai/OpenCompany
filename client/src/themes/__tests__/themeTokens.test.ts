/**
 * Where the editor's dot grid, the light colour scheme, the scrollbars and
 * the employee's accent face get their values. The grid is drawn by the two
 * base themes only: light.css also styles Atomic through `:root`, so
 * `--grid-dot` (and the light `color-scheme`) must live in blocks that match
 * only `[data-theme="light"]` / `[data-theme="dark"]`.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (...path: string[]) => readFileSync(join(__dirname, ...path), 'utf-8');

/** The flat rules of a stylesheet, as [selector, body] pairs. */
function rules(css: string): Array<[string, string]> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => [match[1].trim().replace(/\s+/g, ' '), match[2]]);
}

function bodiesFor(css: string, selector: string): string[] {
  return rules(css)
    .filter(([found]) => found === selector)
    .map(([, body]) => body);
}

describe('theme tokens', () => {
  it('leaves the dot grid transparent unless a base theme sets it', () => {
    expect(bodiesFor(read('..', 'base.css'), ':root').join('\n')).toMatch(/--grid-dot:\s*transparent/);
  });

  it('sets the light grid and colour scheme only for the light theme', () => {
    const light = read('..', 'light.css');
    const own = bodiesFor(light, ':root[data-theme="light"]').join('\n');
    expect(own).toMatch(/--grid-dot:\s*color-mix\(in srgb, var\(--fg-muted\) 20%, transparent\)/);
    expect(own).toMatch(/color-scheme:\s*light/);
    for (const shared of bodiesFor(light, ':root, :root[data-theme="light"]')) {
      expect(shared).not.toMatch(/--grid-dot|color-scheme/);
    }
  });

  it('sets the dark grid only for the dark theme', () => {
    const dark = read('..', 'dark.css');
    expect(bodiesFor(dark, ':root[data-theme="dark"]').join('\n')).toMatch(/--grid-dot:\s*color-mix\(in srgb, var\(--fg-muted\) 22%, transparent\)/);
    for (const shared of bodiesFor(dark, '.dark, :root[data-theme="dark"]')) {
      expect(shared).not.toMatch(/--grid-dot/);
    }
  });

  it('draws the dots and the scrollbars from their tokens', () => {
    const index = read('..', '..', 'index.css');
    expect(bodiesFor(index, '.react-flow__background pattern circle').join('\n')).toMatch(/fill:\s*var\(--grid-dot\)/);
    expect(bodiesFor(index, '::-webkit-scrollbar-track').join('\n')).toMatch(/var\(--scrollbar-track\)/);
    expect(bodiesFor(index, '::-webkit-scrollbar-thumb').join('\n')).toMatch(/var\(--scrollbar-thumb\)/);
    expect(bodiesFor(index, 'html').join('\n')).toMatch(/scrollbar-color:\s*var\(--scrollbar-thumb\) var\(--scrollbar-track\)/);
  });

  it('gives the employee’s accent face its rim and highlight on every theme', () => {
    // Defined once on :root from each theme's own --accent and --fg-default.
    const root = bodiesFor(read('..', 'base.css'), ':root').join('\n');
    expect(root).toMatch(/--tint-accent-edge:\s*70%/);
    expect(root).toMatch(/--accent-edge:\s*color-mix\(in srgb, var\(--accent\) var\(--tint-accent-edge\), var\(--fg-default\)\)/);
    expect(root).toMatch(/--shadow-avatar:\s*inset 0 1px 0 rgb\(255 255 255 \/ 0\.22\)/);
    const index = read('..', '..', 'index.css');
    expect(index).toMatch(/--color-accent-edge:\s*var\(--accent-edge\)/);
    expect(index).toMatch(/--shadow-avatar:\s*var\(--shadow-avatar\)/);
  });
});
