/**
 * cn() must resolve conflicts for every scale name the theme defines in
 * index.css `@theme inline`, not just Tailwind's defaults. An unregistered
 * name is misread by tailwind-merge (a custom `text-*` size is taken for a
 * colour and drops the real colour beside it), so every name found there is
 * checked here; a failure means lib/utils.ts needs the new name.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { cn } from '../utils';

const INDEX_CSS = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf-8');

function themeInlineNames(namespace: string): string[] {
  const block = /@theme inline\s*\{([\s\S]*?)\n\}/.exec(INDEX_CSS)?.[1] ?? '';
  const names = new Set<string>();
  for (const m of block.matchAll(new RegExp(`^\\s*--${namespace}-([a-z0-9-]+)\\s*:`, 'gm'))) names.add(m[1]);
  return [...names];
}

describe('cn() knows every theme scale name', () => {
  it('finds the theme block', () => {
    expect(themeInlineNames('text').length).toBeGreaterThan(0);
  });

  it.each(themeInlineNames('text'))('text-%s is a font size, not a colour', (name) => {
    expect(cn('text-fg-muted', `text-${name}`)).toBe(`text-fg-muted text-${name}`);
    expect(cn('text-sm', `text-${name}`)).toBe(`text-${name}`);
  });

  it.each(themeInlineNames('radius'))('rounded-%s replaces another radius', (name) => {
    expect(cn('rounded-sm', `rounded-${name}`)).toBe(`rounded-${name}`);
  });

  it.each(themeInlineNames('shadow'))('shadow-%s replaces another shadow', (name) => {
    expect(cn('shadow-2xl', `shadow-${name}`)).toBe(`shadow-${name}`);
  });

  it.each(themeInlineNames('ease'))('ease-%s replaces another easing', (name) => {
    expect(cn('ease-in', `ease-${name}`)).toBe(`ease-${name}`);
  });

  it.each(themeInlineNames('tracking'))('tracking-%s replaces another tracking', (name) => {
    expect(cn('tracking-tight', `tracking-${name}`)).toBe(`tracking-${name}`);
  });

  it.each(themeInlineNames('leading'))('leading-%s replaces another leading', (name) => {
    expect(cn('leading-tight', `leading-${name}`)).toBe(`leading-${name}`);
  });

  it.each(themeInlineNames('blur'))('backdrop-blur-%s replaces another blur', (name) => {
    expect(cn('backdrop-blur-xs', `backdrop-blur-${name}`)).toBe(`backdrop-blur-${name}`);
  });

  it.each(themeInlineNames('font'))('font-%s is a family, independent of weight', (name) => {
    expect(cn('font-sans', `font-${name}`, 'font-semibold')).toBe(`font-${name} font-semibold`);
  });
});
