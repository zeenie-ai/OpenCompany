/**
 * index.html carries a pre-paint copy of ThemeProvider's initial-theme logic
 * and of the shell's Home rule (plain ES5, so it can run before the bundle).
 * These tests keep the two in lockstep: the same theme lists, and the same
 * answer for every storage state.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import { readStoredShellMode } from '../../store/useAppStore';
import { AVAILABLE_THEMES, DARK_FAMILY, ThemeProvider, useTheme } from '../ThemeContext';

// LF line endings, as the HTML parser produces, so a script's text can be
// found in the source on a CRLF checkout too.
const INDEX_HTML = readFileSync(join(__dirname, '..', '..', '..', 'index.html'), 'utf-8').replace(/\r\n/g, '\n');

function prePaintScript(): string {
  // Parsed, not matched with a tag regex, which would miss `</script >`,
  // attributes and case variants (CodeQL js/bad-tag-filter).
  const doc = new DOMParser().parseFromString(INDEX_HTML, 'text/html');
  const scripts = Array.from(doc.querySelectorAll('script:not([src])'), (el) => el.textContent ?? '');
  const script = scripts.find((s) => s.includes('data-theme'));
  if (!script) throw new Error('index.html has no inline pre-paint theme script');
  return script;
}

function arrayLiteral(script: string, name: string): string[] {
  const match = new RegExp(`var ${name} = \\[([^\\]]*)\\]`).exec(script);
  if (!match) throw new Error(`pre-paint script declares no ${name}`);
  return Array.from(match[1].matchAll(/'([^']+)'/g), (m) => m[1]);
}

const html = document.documentElement;

function resetDom(): void {
  localStorage.clear();
  html.removeAttribute('data-theme');
  html.className = '';
}

afterEach(resetDom);

describe('pre-paint theme script', () => {
  it('runs before the app bundle', () => {
    const scriptAt = INDEX_HTML.indexOf(prePaintScript());
    expect(scriptAt).toBeGreaterThan(-1);
    expect(scriptAt).toBeLessThan(INDEX_HTML.indexOf('/src/main.tsx'));
  });

  it('lists exactly AVAILABLE_THEMES, in order', () => {
    expect(arrayLiteral(prePaintScript(), 'THEMES')).toEqual([...AVAILABLE_THEMES]);
  });

  it('lists exactly DARK_FAMILY', () => {
    expect(new Set(arrayLiteral(prePaintScript(), 'DARK_FAMILY'))).toEqual(new Set(DARK_FAMILY));
  });

  it.each<[string, Record<string, string>]>([
    ['nothing stored', {}],
    ['a stored theme', { 'opencompany-theme': 'renaissance' }],
    ['a stored dark-family theme', { 'opencompany-theme': 'surveillance' }],
    ['only the pre-rebrand key', { 'machinaos-theme': 'cyber' }],
    ['both brand keys', { 'opencompany-theme': 'edo', 'machinaos-theme': 'rot' }],
    ['legacy darkMode false', { darkMode: 'false' }],
    ['legacy darkMode true', { darkMode: 'true' }],
    ['an unknown stored name', { 'opencompany-theme': 'neon' }],
    ['an unknown name with darkMode false', { 'opencompany-theme': 'neon', darkMode: 'false' }],
    ['Home with a stylized light theme', { 'opencompany-theme': 'atomic', ui_shell_mode: 'normal' }],
    ['Home with a stylized dark theme', { 'opencompany-theme': 'cyber', ui_shell_mode: 'normal' }],
    ['the editor with a stylized theme', { 'opencompany-theme': 'atomic', ui_shell_mode: 'dev' }],
    ['the old Dev palette choice', { 'opencompany-theme': 'renaissance', ui_pro_mode: 'true' }],
    ['an unknown saved screen', { 'opencompany-theme': 'edo', ui_shell_mode: 'kiosk' }],
  ])('agrees with ThemeProvider for %s', (_label, stored) => {
    for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
    new Function(prePaintScript())();
    const prePainted = { theme: html.dataset.theme, dark: html.classList.contains('dark') };
    // The script is read-only: storage is untouched for the provider.
    for (const [key, value] of Object.entries(stored)) expect(localStorage.getItem(key)).toBe(value);

    resetDom();
    for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
    // What ShellThemeProvider passes, from the store's own reading of storage.
    const baseOnly = readStoredShellMode() === 'normal';
    const { result, unmount } = renderHook(() => useTheme(), {
      wrapper: ({ children }: { children: ReactNode }) => createElement(ThemeProvider, { baseOnly, children }),
    });
    expect(prePainted).toEqual({ theme: result.current.theme, dark: result.current.isDarkMode });
    unmount();
  });

  it.each<[string, Record<string, string>, string]>([
    ['Home shows a stylized light theme as light', { 'opencompany-theme': 'atomic' }, 'light'],
    ['Home shows a stylized dark theme as dark', { 'opencompany-theme': 'surveillance' }, 'dark'],
    ['the editor shows the stored theme', { 'opencompany-theme': 'atomic', ui_shell_mode: 'dev' }, 'atomic'],
  ])('%s', (_label, stored, expected) => {
    for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
    new Function(prePaintScript())();
    expect(html.dataset.theme).toBe(expected);
  });
});
