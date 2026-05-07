/**
 * themedGlyphs — per-theme SVG glyph set ported from
 * `design_handoff_machinaos_themes/app/icons.js`.
 *
 * Ten themes (renaissance, cyber, greek, edo, steampunk, atomic,
 * wasteland, rot, plague, surveillance) declare their own glyph
 * language for the same 29 conceptual keys (agent, trigger, tool,
 * memory, output, send, file, folder, settings, credentials, run,
 * save, stop, plus, close, search, chevron, check, warning, error,
 * info, sidebar, upload, download, copy, moon, deploy, grid, shield).
 *
 * Each value is a complete `<svg>` string (the upstream `_wrap`
 * function has been baked in per theme). The renaissance wrap adds
 * a heraldic shield cartouche behind the inner glyph; the cyber
 * wrap adds a `drop-shadow` filter for the neon glow; the rest use
 * a plain 32x32 viewBox.
 *
 * `width="100%" height="100%"` lets the SVG fill the wrapper that
 * `<NodeIcon>` puts around it, so Tailwind sizing classes on the
 * wrapper (`h-6 w-6`, etc.) stay authoritative.
 *
 * Themes that omit a key (`Partial<Record<IconKey, string>>`) fall
 * through to the existing `<NodeIcon>` dispatch (lucide / lobehub /
 * asset / emoji).
 *
 * SECURITY: every value here is author-trusted markup committed to
 * the repo, never user input. `<NodeIcon>` injects via
 * `dangerouslySetInnerHTML`; that's safe for this constant set
 * because nothing in this file is reachable from network or user
 * state. Do NOT extend `THEMED_GLYPHS` with values built from
 * runtime input.
 */

import type { ThemeName } from '../../contexts/ThemeContext';

/** The 29 conceptual icon keys. Any of these may be passed as the
 *  `icon` prop to `<NodeIcon>` to opt into per-theme glyph dispatch. */
export type IconKey =
  | 'agent' | 'trigger' | 'tool' | 'memory' | 'output' | 'send'
  | 'file' | 'folder' | 'settings' | 'credentials'
  | 'run' | 'save' | 'stop'
  | 'plus' | 'close' | 'search' | 'chevron' | 'check'
  | 'warning' | 'error' | 'info'
  | 'sidebar' | 'upload' | 'download' | 'copy'
  | 'moon' | 'deploy' | 'grid' | 'shield';

/** Set of all valid `IconKey` strings, exposed for fast `.has()`
 *  membership checks at the resolver call site. */
export const ICON_KEYS: ReadonlySet<IconKey> = new Set<IconKey>([
  'agent', 'trigger', 'tool', 'memory', 'output', 'send',
  'file', 'folder', 'settings', 'credentials',
  'run', 'save', 'stop',
  'plus', 'close', 'search', 'chevron', 'check',
  'warning', 'error', 'info',
  'sidebar', 'upload', 'download', 'copy',
  'moon', 'deploy', 'grid', 'shield',
]);

/** Outer wrappers, baked into each entry below. Kept here as a
 *  reference for future theme additions:
 *  - renaissance: viewBox + heraldic shield cartouche behind the glyph.
 *  - cyber: viewBox + `drop-shadow(currentColor)` filter (neon glow).
 *  - all others: plain `viewBox="0 0 32 32" fill="none"`.
 */

const SVG_OPEN = '<svg viewBox="0 0 32 32" fill="none" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">';
const SVG_OPEN_CYBER = '<svg viewBox="0 0 32 32" fill="none" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="filter:drop-shadow(0 0 3px currentColor)">';
const RENAISSANCE_CARTOUCHE = '<path d="M4 4 H28 V22 Q28 28, 16 30 Q4 28, 4 22 Z" fill="#fbf3dc" stroke="#5a3a14" stroke-width="1.5"/><path d="M5 5 H27 V22 Q27 27, 16 29 Q5 27, 5 22 Z" fill="none" stroke="#d4a030" stroke-width="0.8"/>';

const wrap = (inner: string): string => `${SVG_OPEN}${inner}</svg>`;
const wrapCyber = (inner: string): string => `${SVG_OPEN_CYBER}${inner}</svg>`;
const wrapRenaissance = (inner: string): string => `${SVG_OPEN}${RENAISSANCE_CARTOUCHE}${inner}</svg>`;

export const THEMED_GLYPHS: Partial<Record<ThemeName, Partial<Record<IconKey, string>>>> = {
  // ─────────── Renaissance — illuminated heraldic ───────────
  renaissance: {
    agent: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030" stroke-linejoin="round"><circle cx="16" cy="13" r="4"/><path d="M9 22 Q16 17, 23 22 L23 25 Q16 27, 9 25 Z"/><path d="M14 12 L13 9 M18 12 L19 9" stroke-width="1"/></g>`),
    trigger: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M16 8 L12 18 L16 18 L14 24 L20 14 L16 14 Z"/></g>`),
    tool: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M10 22 L18 14 M22 12 L24 14 M22 12 L20 10 M22 12 Q26 8, 20 8 L18 10 Z"/><circle cx="11" cy="22" r="1.5" fill="#5a3a14"/></g>`),
    memory: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#fbf3dc"><rect x="9" y="10" width="14" height="14"/><path d="M9 14 L23 14 M9 18 L23 18 M13 10 L13 24 M19 10 L19 24" stroke-width="0.8"/></g>`),
    output: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M9 16 L21 16 M17 12 L21 16 L17 20"/></g>`),
    send: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M9 22 L24 14 L11 13 L13 17 L9 22 Z"/></g>`),
    file: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#fbf3dc"><path d="M11 9 L19 9 L23 13 L23 24 L11 24 Z"/><path d="M19 9 L19 13 L23 13" fill="#d4a030"/></g>`),
    folder: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M9 12 L13 12 L15 14 L23 14 L23 23 L9 23 Z"/></g>`),
    settings: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><circle cx="16" cy="16" r="3"/><path d="M16 9 L16 12 M16 20 L16 23 M9 16 L12 16 M20 16 L23 16 M11 11 L13 13 M19 19 L21 21 M11 21 L13 19 M19 13 L21 11"/></g>`),
    credentials: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><circle cx="14" cy="16" r="4"/><path d="M18 16 L24 16 L24 18 M22 16 L22 19"/></g>`),
    run: wrapRenaissance(`<g stroke="#2a5a18" stroke-width="1.4" fill="#4a8230"><path d="M12 10 L22 16 L12 22 Z"/></g>`),
    save: wrapRenaissance(`<g stroke="#5a0a08" stroke-width="1.2" fill="#8a1410"><circle cx="16" cy="16" r="6"/><path d="M14 14 L18 18 M18 14 L14 18" stroke="#fbf3dc" stroke-width="1"/></g>`),
    stop: wrapRenaissance(`<g stroke="#5a1a08" stroke-width="1.4" fill="#8a3a14"><rect x="11" y="11" width="10" height="10"/></g>`),
    plus: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="2"><path d="M16 10 L16 22 M10 16 L22 16"/></g>`),
    close: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="2"><path d="M11 11 L21 21 M21 11 L11 21"/></g>`),
    search: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="none"><circle cx="14" cy="14" r="4"/><path d="M17 17 L22 22"/></g>`),
    chevron: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.6" fill="none"><path d="M13 12 L19 16 L13 20"/></g>`),
    check: wrapRenaissance(`<g stroke="#2a5a18" stroke-width="2" fill="none"><path d="M10 16 L14 20 L22 12"/></g>`),
    warning: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M16 9 L24 22 L8 22 Z"/><path d="M16 14 L16 18 M16 20 L16 20.5" stroke="#5a0a08" stroke-width="1.6"/></g>`),
    error: wrapRenaissance(`<g stroke="#5a0a08" stroke-width="1.4" fill="#8a1410"><circle cx="16" cy="16" r="6"/><path d="M13 13 L19 19 M19 13 L13 19" stroke="#fbf3dc" stroke-width="1.4"/></g>`),
    info: wrapRenaissance(`<g stroke="#1a3a5a" stroke-width="1.4" fill="#fbf3dc"><circle cx="16" cy="16" r="6"/><path d="M16 14 L16 19 M16 12 L16 12.5" stroke="#1a3a5a" stroke-width="1.6"/></g>`),
    sidebar: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#fbf3dc"><rect x="7" y="8" width="18" height="16"/><path d="M13 8 L13 24" stroke-width="1.4"/><rect x="8" y="9" width="4" height="14" fill="#d4a030" stroke="none"/></g>`),
    upload: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M16 22 L16 10 M11 15 L16 10 L21 15"/><path d="M9 22 L23 22"/></g>`),
    download: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M16 10 L16 22 M11 17 L16 22 L21 17"/><path d="M9 10 L23 10"/></g>`),
    copy: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#fbf3dc"><rect x="11" y="11" width="11" height="13"/><path d="M14 11 L14 8 L25 8 L25 21 L22 21" fill="#d4a030"/></g>`),
    moon: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M22 18 Q22 23, 17 23 Q11 23, 11 16 Q11 10, 17 10 Q14 13, 14 16 Q14 20, 18 20 Q21 20, 22 18 Z"/><circle cx="22" cy="11" r="0.8" fill="#5a3a14"/><circle cx="24" cy="14" r="0.6" fill="#5a3a14"/></g>`),
    deploy: wrapRenaissance(`<g stroke="#2a5a18" stroke-width="1.4" fill="#4a8230"><path d="M16 8 L20 14 L20 20 L18 22 L18 25 L14 25 L14 22 L12 20 L12 14 Z"/><circle cx="16" cy="14" r="1.5" fill="#fbf3dc" stroke="none"/><path d="M12 22 L8 26 M20 22 L24 26" stroke-width="1"/></g>`),
    grid: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><rect x="9" y="9" width="6" height="6"/><rect x="17" y="9" width="6" height="6"/><rect x="9" y="17" width="6" height="6"/><rect x="17" y="17" width="6" height="6"/></g>`),
    shield: wrapRenaissance(`<g stroke="#5a3a14" stroke-width="1.4" fill="#d4a030"><path d="M16 8 L23 11 L23 17 Q23 22, 16 25 Q9 22, 9 17 L9 11 Z"/><path d="M13 16 L15 18 L19 14" stroke="#5a0a08" stroke-width="1.6" fill="none"/></g>`),
  },

  // ─────────── Cyber — wireframe / glitch ───────────
  cyber: {
    agent: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="9" y="6" width="14" height="20"/><rect x="12" y="11" width="2" height="2" fill="currentColor"/><rect x="18" y="11" width="2" height="2" fill="currentColor"/><path d="M12 18 L20 18 M14 22 L18 22"/><path d="M16 6 L16 4 M16 26 L16 28" stroke-dasharray="1 1"/></g>`),
    trigger: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M16 4 L11 16 L16 16 L13 28 L21 14 L16 14 Z" fill="currentColor" fill-opacity="0.3"/></g>`),
    tool: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="6" y="14" width="20" height="4"/><path d="M10 14 L10 12 L14 12 L14 14 M18 18 L18 20 L22 20 L22 18"/></g>`),
    memory: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="7" y="9" width="18" height="14"/><path d="M7 13 L25 13 M7 17 L25 17 M7 21 L25 21 M11 9 L11 23 M16 9 L16 23 M21 9 L21 23"/></g>`),
    output: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><path d="M6 16 L24 16 M18 10 L24 16 L18 22"/></g>`),
    send: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M5 16 L27 6 L20 27 L17 18 L8 16 Z" fill="currentColor" fill-opacity="0.2"/></g>`),
    file: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M9 4 L19 4 L24 9 L24 28 L9 28 Z"/><path d="M19 4 L19 9 L24 9"/><path d="M12 14 L21 14 M12 18 L21 18 M12 22 L17 22"/></g>`),
    folder: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M5 10 L13 10 L15 12 L27 12 L27 24 L5 24 Z"/></g>`),
    settings: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><circle cx="16" cy="16" r="3"/><circle cx="16" cy="16" r="8"/><path d="M16 4 L16 8 M16 24 L16 28 M4 16 L8 16 M24 16 L28 16"/></g>`),
    credentials: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><circle cx="12" cy="16" r="4"/><path d="M16 16 L28 16 L28 19 M24 16 L24 20 M20 16 L20 18"/></g>`),
    run: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="currentColor" fill-opacity="0.4"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="6" y="6" width="20" height="20"/><rect x="10" y="6" width="12" height="6" fill="currentColor" fill-opacity="0.3"/><rect x="11" y="18" width="10" height="8"/></g>`),
    stop: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="currentColor" fill-opacity="0.4"><rect x="9" y="9" width="14" height="14"/></g>`),
    plus: wrapCyber(`<g stroke="currentColor" stroke-width="1.6"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrapCyber(`<g stroke="currentColor" stroke-width="1.6"><path d="M7 7 L25 25 M25 7 L7 25"/></g>`),
    search: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><circle cx="14" cy="14" r="6"/><path d="M19 19 L26 26"/></g>`),
    chevron: wrapCyber(`<g stroke="currentColor" stroke-width="1.6" fill="none"><path d="M11 8 L21 16 L11 24"/></g>`),
    check: wrapCyber(`<g stroke="currentColor" stroke-width="1.8" fill="none"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><path d="M16 4 L28 26 L4 26 Z"/><path d="M16 12 L16 20 M16 23 L16 24" stroke-width="1.8"/></g>`),
    error: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><circle cx="16" cy="16" r="10"/><path d="M11 11 L21 21 M21 11 L11 21" stroke-width="1.8"/></g>`),
    info: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><circle cx="16" cy="16" r="10"/><path d="M16 14 L16 22 M16 10 L16 11" stroke-width="1.8"/></g>`),
    sidebar: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/><rect x="6" y="7" width="5" height="18" fill="currentColor" fill-opacity="0.25"/></g>`),
    upload: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/><path d="M6 26 L26 26"/></g>`),
    download: wrapCyber(`<g stroke="currentColor" stroke-width="1.4" fill="none"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/><path d="M6 6 L26 6"/></g>`),
    copy: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M16 4 L22 12 L22 22 L18 26 L14 26 L10 22 L10 12 Z"/><circle cx="16" cy="13" r="2.5" fill="currentColor" fill-opacity="0.4"/><path d="M10 22 L4 28 M22 22 L28 28" stroke-dasharray="2 1"/></g>`),
    grid: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrapCyber(`<g stroke="currentColor" stroke-width="1.2" fill="none"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke-width="1.6"/></g>`),
  },

  // ─────────── Greek — engraved on marble, lapis-blue glyphs ───────────
  greek: {
    agent: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#284b82"><circle cx="16" cy="11" r="3.5" fill="none"/><path d="M9 23 Q9 17, 16 17 Q23 17, 23 23 Z" fill="none"/><path d="M14 9 L13 7 L19 7 L18 9" stroke-width="1"/></g>`),
    trigger: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M16 5 L8 17 L14 17 L12 27 L24 13 L18 13 L20 5 Z"/></g>`),
    tool: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="none"><path d="M6 22 L18 10 M22 6 L26 10 M24 8 Q28 4, 22 4 L18 8 Z" fill="#c8a040"/><circle cx="7" cy="22" r="1.5" fill="#284b82"/></g>`),
    memory: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="none"><rect x="6" y="8" width="20" height="16"/><path d="M6 13 L26 13 M6 18 L26 18 M11 8 L11 24 M21 8 L21 24"/></g>`),
    output: wrap(`<g stroke="#284b82" stroke-width="1.6" fill="none"><path d="M6 16 L24 16 L20 12 M24 16 L20 20"/></g>`),
    send: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M5 27 L27 5 L19 13 L13 11 L11 19 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#faf6e9"><path d="M9 5 L19 5 L23 9 L23 27 L9 27 Z"/><path d="M19 5 L19 9 L23 9" fill="#c8a040"/><path d="M12 14 L20 14 M12 18 L20 18 M12 22 L17 22"/></g>`),
    folder: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M5 10 L13 10 L15 12 L27 12 L27 24 L5 24 Z"/><path d="M5 14 L27 14"/></g>`),
    settings: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="none"><circle cx="16" cy="16" r="3"/><circle cx="16" cy="16" r="8"/><path d="M16 4 L16 8 M16 24 L16 28 M4 16 L8 16 M24 16 L28 16 M8 8 L11 11 M21 21 L24 24 M8 24 L11 21 M21 11 L24 8"/></g>`),
    credentials: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><circle cx="12" cy="16" r="4" fill="none"/><path d="M16 16 L28 16 L28 19 M24 16 L24 20 M20 16 L20 18"/></g>`),
    run: wrap(`<g stroke="#6a7a32" stroke-width="1.4" fill="#6a7a32"><path d="M9 7 L25 16 L9 25 Z"/></g>`),
    save: wrap(`<g stroke="#7a1a18" stroke-width="1.4" fill="#7a1a18"><path d="M16 5 L19 13 L27 13 L21 18 L23 26 L16 22 L9 26 L11 18 L5 13 L13 13 Z"/></g>`),
    stop: wrap(`<g stroke="#7a1a18" stroke-width="1.4" fill="#7a1a18"><rect x="8" y="8" width="16" height="16"/></g>`),
    plus: wrap(`<g stroke="#284b82" stroke-width="2.4"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrap(`<g stroke="#284b82" stroke-width="2.4"><path d="M7 7 L25 25 M25 7 L7 25"/></g>`),
    search: wrap(`<g stroke="#284b82" stroke-width="1.6" fill="none"><circle cx="13" cy="13" r="6"/><path d="M18 18 L26 26"/></g>`),
    chevron: wrap(`<g stroke="#284b82" stroke-width="2" fill="none"><path d="M11 8 L21 16 L11 24"/></g>`),
    check: wrap(`<g stroke="#6a7a32" stroke-width="2.4" fill="none"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M16 5 L28 26 L4 26 Z"/><path d="M16 13 L16 20 M16 23 L16 23.5" stroke="#7a1a18" stroke-width="2"/></g>`),
    error: wrap(`<g stroke="#7a1a18" stroke-width="1.4" fill="#7a1a18"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21" stroke="#faf6e9" stroke-width="2"/></g>`),
    info: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#284b82"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11" stroke="#faf6e9" stroke-width="2"/></g>`),
    sidebar: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#faf6e9"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/><rect x="6" y="7" width="5" height="18" fill="#c8a040" stroke="none"/></g>`),
    upload: wrap(`<g stroke="#284b82" stroke-width="1.6" fill="none"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/><path d="M6 27 L26 27"/></g>`),
    download: wrap(`<g stroke="#284b82" stroke-width="1.6" fill="none"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/><path d="M6 5 L26 5"/></g>`),
    copy: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#faf6e9"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20" fill="#c8a040"/></g>`),
    moon: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M16 4 L23 9 L23 18 L19 24 L13 24 L9 18 L9 9 Z"/><circle cx="16" cy="13" r="2.5" fill="#284b82" stroke="none"/></g>`),
    grid: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#284b82" stroke-width="1.4" fill="#c8a040"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke="#7a1a18" stroke-width="1.8" fill="none"/></g>`),
  },

  // ─────────── Edo — single ink-brush stroke, vermillion seal ───────────
  edo: {
    agent: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none" stroke-linecap="round"><circle cx="16" cy="11" r="3.5"/><path d="M9 24 Q9 18, 16 18 Q23 18, 23 24"/></g>`),
    trigger: wrap(`<g stroke="#1a1410" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M17 4 L10 17 L15 17 L13 28 L22 14 L17 14 Z" fill="#b41e1e" fill-opacity="0.15"/></g>`),
    tool: wrap(`<g stroke="#1a1410" stroke-width="1.8" fill="none" stroke-linecap="round"><path d="M6 24 L20 10 M22 8 L24 10 M22 8 Q26 4, 22 4 L20 6"/></g>`),
    memory: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><rect x="6" y="9" width="20" height="14"/><path d="M6 13 L26 13 M6 18 L26 18 M13 9 L13 23 M19 9 L19 23"/></g>`),
    output: wrap(`<g stroke="#1a1410" stroke-width="1.8" fill="none" stroke-linecap="round"><path d="M5 16 L25 16 L20 11 M25 16 L20 21"/></g>`),
    send: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#b41e1e" stroke-linejoin="round"><path d="M5 27 L27 5 L17 13 L13 11 L13 17 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><path d="M9 5 L19 5 L24 10 L24 27 L9 27 Z"/><path d="M19 5 L19 10 L24 10"/></g>`),
    folder: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><path d="M5 11 L13 11 L15 13 L27 13 L27 24 L5 24 Z"/></g>`),
    settings: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><circle cx="16" cy="16" r="3"/><path d="M16 5 L16 9 M16 23 L16 27 M5 16 L9 16 M23 16 L27 16 M8 8 L11 11 M21 21 L24 24 M8 24 L11 21 M21 11 L24 8"/></g>`),
    credentials: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><circle cx="12" cy="16" r="4"/><path d="M16 16 L27 16 L27 19 M24 16 L24 20 M20 16 L20 18"/></g>`),
    run: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#4a6a3a" stroke-linejoin="round"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrap(`<g fill="#b41e1e"><rect x="9" y="9" width="14" height="14" transform="rotate(6 16 16)"/></g>`),
    stop: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#1a1410"><rect x="9" y="9" width="14" height="14"/></g>`),
    plus: wrap(`<g stroke="#1a1410" stroke-width="2" stroke-linecap="round"><path d="M16 7 L16 25 M7 16 L25 16"/></g>`),
    close: wrap(`<g stroke="#1a1410" stroke-width="2" stroke-linecap="round"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none" stroke-linecap="round"><circle cx="13" cy="13" r="6"/><path d="M18 18 L25 25"/></g>`),
    chevron: wrap(`<g stroke="#1a1410" stroke-width="2" fill="none" stroke-linecap="round"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#4a6a3a" stroke-width="2.4" fill="none" stroke-linecap="round"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#c89832" stroke-linejoin="round"><path d="M16 5 L27 25 L5 25 Z"/><path d="M16 13 L16 20 M16 22.5 L16 23" stroke="#1a1410" stroke-width="2"/></g>`),
    error: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#b41e1e"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21" stroke="#fdf8ea" stroke-width="2"/></g>`),
    info: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11" stroke-width="2"/></g>`),
    sidebar: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/></g>`),
    upload: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none" stroke-linecap="round"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/></g>`),
    download: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none" stroke-linecap="round"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/></g>`),
    copy: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none" stroke-linecap="round"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><path d="M16 5 L22 11 L22 22 L18 26 L14 26 L10 22 L10 11 Z"/><circle cx="16" cy="13" r="2"/></g>`),
    grid: wrap(`<g stroke="#1a1410" stroke-width="1.4" fill="none"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="none"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/></g>`),
  },

  // ─────────── Steampunk — riveted brass plates with bolts ───────────
  steampunk: {
    agent: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><circle cx="16" cy="11" r="3.5"/><path d="M9 24 Q9 18, 16 18 Q23 18, 23 24 Z"/><circle cx="16" cy="11" r="1" fill="#4a2818"/></g>`),
    trigger: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><circle cx="16" cy="16" r="9"/><path d="M16 8 L16 16 L20 19" stroke-width="1.6"/><circle cx="16" cy="16" r="1.5" fill="#4a2818"/></g>`),
    tool: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><circle cx="16" cy="16" r="6"/><path d="M16 8 L16 12 M16 20 L16 24 M8 16 L12 16 M20 16 L24 16 M11 11 L13 13 M19 19 L21 21 M11 21 L13 19 M19 13 L21 11"/><circle cx="16" cy="16" r="2" fill="#b8602a"/></g>`),
    memory: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#b8602a"><rect x="6" y="9" width="20" height="14"/><path d="M6 14 L26 14 M6 19 L26 19" stroke="#d8a848"/><circle cx="9" cy="11" r="0.8" fill="#4a2818"/><circle cx="23" cy="11" r="0.8" fill="#4a2818"/><circle cx="9" cy="21" r="0.8" fill="#4a2818"/><circle cx="23" cy="21" r="0.8" fill="#4a2818"/></g>`),
    output: wrap(`<g stroke="#4a2818" stroke-width="1.6" fill="#d8a848"><path d="M5 16 L23 16 L19 12 L19 20 Z"/></g>`),
    send: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M5 27 L27 5 L19 13 L13 11 L13 19 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M9 5 L19 5 L24 10 L24 27 L9 27 Z"/><circle cx="11" cy="7" r="0.8" fill="#4a2818"/><circle cx="22" cy="25" r="0.8" fill="#4a2818"/></g>`),
    folder: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#b8602a"><path d="M5 11 L13 11 L15 13 L27 13 L27 24 L5 24 Z"/><circle cx="7" cy="22" r="0.8" fill="#4a2818"/><circle cx="25" cy="22" r="0.8" fill="#4a2818"/></g>`),
    settings: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M16 4 L18 8 L22 6 L22 11 L26 13 L24 16 L26 19 L22 21 L22 26 L18 24 L16 28 L14 24 L10 26 L10 21 L6 19 L8 16 L6 13 L10 11 L10 6 L14 8 Z"/><circle cx="16" cy="16" r="3" fill="#b8602a"/></g>`),
    credentials: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><circle cx="12" cy="16" r="4"/><circle cx="12" cy="16" r="1.5" fill="#4a2818"/><path d="M16 16 L27 16 L27 20 M23 16 L23 20 M19 16 L19 18"/></g>`),
    run: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#6a8a3a"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><circle cx="16" cy="16" r="9"/><path d="M16 7 L16 25 M7 16 L25 16" stroke-width="1"/><circle cx="16" cy="16" r="3" fill="#b8602a"/></g>`),
    stop: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#8a3a1a"><rect x="8" y="8" width="16" height="16"/><circle cx="10" cy="10" r="0.8" fill="#4a2818"/><circle cx="22" cy="10" r="0.8" fill="#4a2818"/><circle cx="10" cy="22" r="0.8" fill="#4a2818"/><circle cx="22" cy="22" r="0.8" fill="#4a2818"/></g>`),
    plus: wrap(`<g stroke="#4a2818" stroke-width="2.4"><path d="M16 7 L16 25 M7 16 L25 16"/></g>`),
    close: wrap(`<g stroke="#4a2818" stroke-width="2.4"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#4a2818" stroke-width="1.5" fill="none"><circle cx="13" cy="13" r="6"/><path d="M18 18 L25 25" stroke-width="2"/></g>`),
    chevron: wrap(`<g stroke="#4a2818" stroke-width="2" fill="none"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#6a8a3a" stroke-width="2.4" fill="none"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M16 5 L27 25 L5 25 Z"/><path d="M16 13 L16 20" stroke-width="2"/><circle cx="16" cy="22.5" r="0.8" fill="#4a2818"/></g>`),
    error: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#8a3a1a"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21" stroke="#d8a848" stroke-width="2"/></g>`),
    info: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#4a8aa8"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11" stroke="#d8a848" stroke-width="2"/></g>`),
    sidebar: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/><circle cx="7" cy="8" r="0.6" fill="#4a2818"/><circle cx="25" cy="24" r="0.6" fill="#4a2818"/></g>`),
    upload: wrap(`<g stroke="#4a2818" stroke-width="1.6" fill="#d8a848"><path d="M16 23 L16 8 L11 13 M16 8 L21 13"/></g>`),
    download: wrap(`<g stroke="#4a2818" stroke-width="1.6" fill="#d8a848"><path d="M16 8 L16 23 L11 18 M16 23 L21 18"/></g>`),
    copy: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M16 4 L23 11 L23 22 L18 27 L14 27 L9 22 L9 11 Z"/><circle cx="16" cy="14" r="3" fill="#b8602a"/></g>`),
    grid: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#4a2818" stroke-width="1.3" fill="#d8a848"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke="#8a3a1a" stroke-width="2"/></g>`),
  },

  // ─────────── Atomic Modern — boomerang & starburst, Eames cartoon ───────────
  atomic: {
    agent: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><circle cx="16" cy="11" r="4"/><path d="M8 25 Q8 18, 16 18 Q24 18, 24 25"/></g>`),
    trigger: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M16 3 L11 16 L16 16 L13 29 L23 13 L17 13 L21 3 Z"/></g>`),
    tool: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0" stroke-linejoin="round"><path d="M5 25 L18 12 M22 8 L26 12 M22 8 Q28 2, 22 4 L18 8 Z"/></g>`),
    memory: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><rect x="5" y="9" width="22" height="14" rx="3"/><path d="M5 14 L27 14 M5 19 L27 19" stroke="#2a3a4a"/></g>`),
    output: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M5 16 L23 16 L18 11 M23 16 L18 21"/></g>`),
    send: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><path d="M4 28 L28 4 L18 14 L12 11 L11 19 Z"/></g>`),
    file: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#ffffff" stroke-linejoin="round"><path d="M9 4 L20 4 L24 8 L24 28 L9 28 Z"/><path d="M20 4 L20 8 L24 8" fill="#d8a838"/></g>`),
    folder: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M4 11 L13 11 L15 13 L28 13 L28 25 L4 25 Z"/></g>`),
    settings: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><circle cx="16" cy="16" r="4"/><path d="M16 3 L16 8 M16 24 L16 29 M3 16 L8 16 M24 16 L29 16" stroke-linecap="round"/></g>`),
    credentials: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838"><circle cx="12" cy="16" r="4"/><path d="M16 16 L28 16 L28 20 M23 16 L23 21 M19 16 L19 19"/></g>`),
    run: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#5a8a5a" stroke-linejoin="round"><path d="M9 5 L26 16 L9 27 Z"/></g>`),
    save: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><path d="M16 4 L20 13 L29 14 L22 20 L24 29 L16 25 L8 29 L10 20 L3 14 L12 13 Z"/></g>`),
    stop: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26"><rect x="7" y="7" width="18" height="18" rx="2"/></g>`),
    plus: wrap(`<g stroke="#2a3a4a" stroke-width="3" stroke-linecap="round"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrap(`<g stroke="#2a3a4a" stroke-width="3" stroke-linecap="round"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="#d8a838" stroke-linecap="round"><circle cx="13" cy="13" r="6"/><path d="M18 18 L26 26"/></g>`),
    chevron: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#5a8a5a" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M16 4 L28 26 L4 26 Z"/><path d="M16 13 L16 20" stroke-linecap="round"/></g>`),
    error: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26"><circle cx="16" cy="16" r="10"/><path d="M11 11 L21 21 M21 11 L11 21" stroke="#ffffff" stroke-linecap="round"/></g>`),
    info: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><circle cx="16" cy="16" r="10"/><path d="M16 14 L16 22 M16 10 L16 11" stroke="#ffffff" stroke-linecap="round"/></g>`),
    sidebar: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><rect x="5" y="6" width="22" height="20" rx="2"/><path d="M12 6 L12 26"/></g>`),
    upload: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M16 24 L16 7 M9 14 L16 7 L23 14"/></g>`),
    download: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M16 8 L16 25 M9 18 L16 25 L23 18"/></g>`),
    copy: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><rect x="8" y="8" width="13" height="16" rx="1"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><path d="M16 3 L23 11 L23 23 L18 28 L14 28 L9 23 L9 11 Z"/><circle cx="16" cy="13" r="2.5" fill="#ffffff"/></g>`),
    grid: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838"><rect x="6" y="6" width="8" height="8" rx="1"/><rect x="18" y="6" width="8" height="8" rx="1"/><rect x="6" y="18" width="8" height="8" rx="1"/><rect x="18" y="18" width="8" height="8" rx="1"/></g>`),
    shield: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0" stroke-linejoin="round"><path d="M16 3 L26 7 L26 16 Q26 24, 16 29 Q6 24, 6 16 L6 7 Z"/><path d="M11 16 L14 19 L21 12" stroke="#ffffff" stroke-linecap="round"/></g>`),
  },

  // ─────────── Wasteland — stenciled / spray-painted, jagged ───────────
  wasteland: {
    agent: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none" stroke-linejoin="round"><path d="M12 7 L20 7 L20 14 L23 14 L23 24 L9 24 L9 14 L12 14 Z"/><rect x="13" y="10" width="2" height="2" fill="#e88a28"/><rect x="17" y="10" width="2" height="2" fill="#e88a28"/></g>`),
    trigger: wrap(`<g stroke="#c8d038" stroke-width="2" fill="#c8d038" fill-opacity="0.3" stroke-linejoin="round"><path d="M16 4 L9 18 L15 18 L12 28 L23 13 L17 13 L19 4 Z"/></g>`),
    tool: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none" stroke-linejoin="round"><path d="M5 25 L17 13 M19 11 L21 13 M19 11 Q25 5, 21 7 L17 11 Z"/><path d="M5 25 L7 27"/></g>`),
    memory: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><rect x="6" y="9" width="20" height="14"/><path d="M6 13 L26 13 M6 18 L26 18 M11 9 L11 23 M21 9 L21 23"/></g>`),
    output: wrap(`<g stroke="#e88a28" stroke-width="2.4" fill="none" stroke-linecap="square"><path d="M5 16 L24 16 L19 11 M24 16 L19 21"/></g>`),
    send: wrap(`<g stroke="#e88a28" stroke-width="2" fill="#e88a28" fill-opacity="0.3" stroke-linejoin="round"><path d="M5 27 L27 5 L19 13 L13 11 L13 19 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none" stroke-linejoin="round"><path d="M9 5 L19 5 L24 10 L24 27 L9 27 Z"/><path d="M19 5 L19 10 L24 10"/><path d="M12 14 L20 14 M12 18 L20 18 M12 22 L17 22"/></g>`),
    folder: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none" stroke-linejoin="round"><path d="M5 11 L13 11 L15 13 L27 13 L27 24 L5 24 Z"/></g>`),
    settings: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><circle cx="16" cy="16" r="3"/><path d="M16 4 L16 9 M16 23 L16 28 M4 16 L9 16 M23 16 L28 16 M8 8 L11 11 M21 21 L24 24 M8 24 L11 21 M21 11 L24 8"/></g>`),
    credentials: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><circle cx="12" cy="16" r="4"/><path d="M16 16 L27 16 L27 20 M23 16 L23 20"/></g>`),
    run: wrap(`<g stroke="#8a9028" stroke-width="2" fill="#8a9028" fill-opacity="0.4" stroke-linejoin="round"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrap(`<g stroke="#c8d038" stroke-width="2" fill="none"><circle cx="16" cy="16" r="9"/><path d="M16 11 L16 16 L19 18" stroke-linecap="square"/></g>`),
    stop: wrap(`<g stroke="#b8281a" stroke-width="2" fill="#b8281a" fill-opacity="0.4"><rect x="8" y="8" width="16" height="16"/></g>`),
    plus: wrap(`<g stroke="#e88a28" stroke-width="3" stroke-linecap="square"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrap(`<g stroke="#e88a28" stroke-width="3" stroke-linecap="square"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none" stroke-linecap="square"><circle cx="13" cy="13" r="6"/><path d="M18 18 L25 25"/></g>`),
    chevron: wrap(`<g stroke="#e88a28" stroke-width="2.4" fill="none" stroke-linecap="square"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#8a9028" stroke-width="3" fill="none" stroke-linecap="square"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#c8d038" stroke-width="2" fill="#c8d038" fill-opacity="0.3"><path d="M16 4 L28 26 L4 26 Z"/><path d="M16 13 L16 20" stroke-linecap="square"/></g>`),
    error: wrap(`<g stroke="#b8281a" stroke-width="2" fill="#b8281a" fill-opacity="0.4"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21" stroke-linecap="square"/></g>`),
    info: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11" stroke-linecap="square"/></g>`),
    sidebar: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/></g>`),
    upload: wrap(`<g stroke="#e88a28" stroke-width="2.4" fill="none" stroke-linecap="square"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/></g>`),
    download: wrap(`<g stroke="#e88a28" stroke-width="2.4" fill="none" stroke-linecap="square"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/></g>`),
    copy: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none" stroke-linejoin="round"><path d="M16 5 L22 11 L22 22 L18 26 L14 26 L10 22 L10 11 Z"/><circle cx="16" cy="14" r="2.5" fill="#e88a28" fill-opacity="0.4"/></g>`),
    grid: wrap(`<g stroke="#e88a28" stroke-width="2" fill="none"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#c8d038" stroke-width="2" fill="none"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke-width="2.4" stroke-linecap="square"/></g>`),
  },

  // ─────────── Rot — bone-pale fragile lines, drips ───────────
  rot: {
    agent: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><circle cx="16" cy="11" r="3.5"/><path d="M9 24 Q9 18, 16 18 Q23 18, 23 24"/><path d="M14 13 L14 16 M18 13 L18 16" stroke="#78c878" stroke-width="0.8"/></g>`),
    trigger: wrap(`<g stroke="#78c878" stroke-width="1.6" fill="none" stroke-linecap="round"><path d="M16 5 L11 17 L16 17 L13 27 L21 14 L16 14 Z" fill="#78c878" fill-opacity="0.2"/></g>`),
    tool: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><path d="M6 25 L18 13 M22 9 L24 11 M22 9 Q26 5, 22 5 L18 9 Z" fill="#78c878" fill-opacity="0.3"/></g>`),
    memory: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><rect x="6" y="9" width="20" height="14"/><path d="M6 13 L26 13 M6 18 L26 18 M11 9 L11 23 M21 9 L21 23"/></g>`),
    output: wrap(`<g stroke="#78c878" stroke-width="1.6" fill="none" stroke-linecap="round"><path d="M5 16 L23 16 L18 11 M23 16 L18 21"/></g>`),
    send: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="#78c878" fill-opacity="0.2" stroke-linejoin="round"><path d="M5 27 L27 5 L19 13 L13 11 L13 19 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><path d="M9 5 L19 5 L24 10 L24 27 L9 27 Z"/><path d="M19 5 L19 10 L24 10"/><path d="M12 14 L20 14 M12 18 L20 18 M12 22 L17 22"/></g>`),
    folder: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><path d="M5 11 L13 11 L15 13 L27 13 L27 24 L5 24 Z"/></g>`),
    settings: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><circle cx="16" cy="16" r="3"/><path d="M16 4 L16 9 M16 23 L16 28 M4 16 L9 16 M23 16 L28 16 M8 8 L11 11 M21 21 L24 24 M8 24 L11 21 M21 11 L24 8"/></g>`),
    credentials: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><circle cx="12" cy="16" r="4"/><path d="M16 16 L27 16 L27 20 M23 16 L23 20"/></g>`),
    run: wrap(`<g stroke="#78c878" stroke-width="1.4" fill="#78c878" fill-opacity="0.4" stroke-linejoin="round"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrap(`<g stroke="#e8a838" stroke-width="1.4" fill="#e8a838" fill-opacity="0.3"><path d="M16 5 L18 11 L25 11 L19 15 L22 22 L16 18 L10 22 L13 15 L7 11 L14 11 Z"/></g>`),
    stop: wrap(`<g stroke="#a83838" stroke-width="1.4" fill="#a83838" fill-opacity="0.4"><rect x="9" y="9" width="14" height="14"/></g>`),
    plus: wrap(`<g stroke="#d8d0b8" stroke-width="2"><path d="M16 7 L16 25 M7 16 L25 16"/></g>`),
    close: wrap(`<g stroke="#d8d0b8" stroke-width="2"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><circle cx="13" cy="13" r="6"/><path d="M18 18 L25 25"/></g>`),
    chevron: wrap(`<g stroke="#d8d0b8" stroke-width="1.8" fill="none"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#78c878" stroke-width="2" fill="none"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#e8a838" stroke-width="1.4" fill="#e8a838" fill-opacity="0.3"><path d="M16 5 L27 25 L5 25 Z"/><path d="M16 13 L16 20"/></g>`),
    error: wrap(`<g stroke="#a83838" stroke-width="1.4" fill="#a83838" fill-opacity="0.4"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21"/></g>`),
    info: wrap(`<g stroke="#5898b8" stroke-width="1.4" fill="none"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11"/></g>`),
    sidebar: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/></g>`),
    upload: wrap(`<g stroke="#d8d0b8" stroke-width="1.6" fill="none"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/></g>`),
    download: wrap(`<g stroke="#d8d0b8" stroke-width="1.6" fill="none"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/></g>`),
    copy: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#e8a838" stroke-width="1.4" fill="#e8a838" fill-opacity="0.2"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#78c878" stroke-width="1.4" fill="none"><path d="M16 5 L22 11 L22 22 L18 26 L14 26 L10 22 L10 11 Z"/><circle cx="16" cy="14" r="2.5" fill="#78c878" fill-opacity="0.3"/></g>`),
    grid: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#d8d0b8" stroke-width="1.4" fill="none"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke="#78c878" stroke-width="1.8"/></g>`),
  },

  // ─────────── Plague — woodcut, hatched shadows ───────────
  plague: {
    agent: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><circle cx="16" cy="11" r="3.5"/><path d="M9 24 Q9 18, 16 18 Q23 18, 23 24 Z"/><path d="M14 22 L14 24 M16 22 L16 24 M18 22 L18 24" stroke-width="0.8"/></g>`),
    trigger: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><path d="M16 4 L10 16 L15 16 L12 28 L22 13 L17 13 L20 4 Z"/></g>`),
    tool: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><path d="M6 25 L18 13 M22 9 L24 11 M22 9 Q26 5, 22 5 L18 9 Z"/></g>`),
    memory: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><rect x="6" y="9" width="20" height="14"/><path d="M6 13 L26 13 M6 18 L26 18" stroke-width="1"/></g>`),
    output: wrap(`<g stroke="#1a1410" stroke-width="2" fill="none"><path d="M5 16 L23 16 L18 11 M23 16 L18 21"/></g>`),
    send: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><path d="M5 27 L27 5 L19 13 L13 11 L13 19 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><path d="M9 5 L19 5 L24 10 L24 27 L9 27 Z"/><path d="M19 5 L19 10 L24 10"/><path d="M12 14 L20 14 M12 18 L20 18 M12 22 L16 22"/></g>`),
    folder: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><path d="M5 11 L13 11 L15 13 L27 13 L27 24 L5 24 Z"/></g>`),
    settings: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><circle cx="16" cy="16" r="3"/><circle cx="16" cy="16" r="8"/><path d="M16 4 L16 8 M16 24 L16 28 M4 16 L8 16 M24 16 L28 16"/></g>`),
    credentials: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><circle cx="12" cy="16" r="4" fill="#efeacf"/><path d="M16 16 L27 16 L27 20 M23 16 L23 20"/></g>`),
    run: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#5a7028"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><circle cx="16" cy="16" r="8"/><path d="M14 14 L18 14 L18 18 L14 18 Z M12 12 L20 20 M20 12 L12 20" stroke="#efeacf" stroke-width="1.2"/></g>`),
    stop: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><rect x="8" y="8" width="16" height="16"/></g>`),
    plus: wrap(`<g stroke="#1a1410" stroke-width="2.4"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrap(`<g stroke="#1a1410" stroke-width="2.4"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#1a1410" stroke-width="1.8" fill="none"><circle cx="13" cy="13" r="6"/><path d="M18 18 L25 25"/></g>`),
    chevron: wrap(`<g stroke="#1a1410" stroke-width="2" fill="none"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#5a7028" stroke-width="2.4" fill="none"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#98a838"><path d="M16 4 L28 26 L4 26 Z"/><path d="M16 13 L16 20" stroke-width="2"/><circle cx="16" cy="23" r="0.8" fill="#1a1410"/></g>`),
    error: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21" stroke="#efeacf" stroke-width="2"/></g>`),
    info: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11" stroke-width="2"/></g>`),
    sidebar: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/></g>`),
    upload: wrap(`<g stroke="#1a1410" stroke-width="2" fill="none"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/></g>`),
    download: wrap(`<g stroke="#1a1410" stroke-width="2" fill="none"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/></g>`),
    copy: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><path d="M16 5 L22 11 L22 22 L18 26 L14 26 L10 22 L10 11 Z"/><circle cx="16" cy="14" r="2.5" fill="#efeacf"/></g>`),
    grid: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#efeacf"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#1a1410" stroke-width="1.6" fill="#783c28"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke="#efeacf" stroke-width="2"/></g>`),
  },

  // ─────────── Surveillance — terminal monospace, corner brackets ───────────
  surveillance: {
    agent: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="9" y="6" width="14" height="20"/><circle cx="13" cy="12" r="1" fill="#e82626"/><circle cx="19" cy="12" r="1" fill="#c8ccd0"/><path d="M12 18 L20 18 M12 21 L20 21" stroke-width="0.8"/></g>`),
    trigger: wrap(`<g stroke="#e82626" stroke-width="1.4" fill="#e82626" fill-opacity="0.3"><path d="M16 4 L10 16 L15 16 L12 28 L22 13 L17 13 L20 4 Z"/></g>`),
    tool: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="6" y="14" width="20" height="4"/><path d="M10 14 L10 12 L14 12 L14 14 M18 18 L18 20 L22 20 L22 18"/></g>`),
    memory: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="6" y="9" width="20" height="14"/><path d="M6 13 L26 13 M6 17 L26 17 M6 21 L26 21 M11 9 L11 23 M21 9 L21 23"/></g>`),
    output: wrap(`<g stroke="#6acc6a" stroke-width="1.4" fill="none"><path d="M5 16 L24 16 M19 11 L24 16 L19 21"/></g>`),
    send: wrap(`<g stroke="#e82626" stroke-width="1.2" fill="#e82626" fill-opacity="0.3"><path d="M5 27 L27 5 L19 13 L13 11 L13 19 L5 27 Z"/></g>`),
    file: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><path d="M9 5 L19 5 L24 10 L24 27 L9 27 Z"/><path d="M19 5 L19 10 L24 10"/><path d="M12 14 L20 14 M12 18 L20 18 M12 22 L17 22" stroke="#6acc6a"/></g>`),
    folder: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><path d="M5 11 L13 11 L15 13 L27 13 L27 24 L5 24 Z"/></g>`),
    settings: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="13" y="13" width="6" height="6"/><path d="M16 4 L16 9 M16 23 L16 28 M4 16 L9 16 M23 16 L28 16 M8 8 L12 12 M20 20 L24 24 M8 24 L12 20 M20 12 L24 8"/></g>`),
    credentials: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><circle cx="12" cy="16" r="4"/><path d="M16 16 L27 16 L27 20 M23 16 L23 20 M19 16 L19 18"/></g>`),
    run: wrap(`<g stroke="#6acc6a" stroke-width="1.4" fill="#6acc6a" fill-opacity="0.4"><path d="M10 6 L26 16 L10 26 Z"/></g>`),
    save: wrap(`<g stroke="#d8a020" stroke-width="1.2" fill="none"><rect x="6" y="6" width="20" height="20"/><rect x="10" y="6" width="12" height="6" fill="#d8a020" fill-opacity="0.3"/><rect x="11" y="18" width="10" height="8"/></g>`),
    stop: wrap(`<g stroke="#e82626" stroke-width="1.4" fill="#e82626" fill-opacity="0.5"><rect x="9" y="9" width="14" height="14"/></g>`),
    plus: wrap(`<g stroke="#c8ccd0" stroke-width="1.6" stroke-linecap="square"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrap(`<g stroke="#e82626" stroke-width="1.6" stroke-linecap="square"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#c8ccd0" stroke-width="1.4" fill="none"><circle cx="13" cy="13" r="6"/><path d="M18 18 L26 26"/></g>`),
    chevron: wrap(`<g stroke="#c8ccd0" stroke-width="1.6" fill="none"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#6acc6a" stroke-width="1.8" fill="none"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#d8a020" stroke-width="1.4" fill="none"><path d="M16 4 L28 26 L4 26 Z"/><path d="M16 13 L16 20 M16 23 L16 23.5" stroke-width="1.8"/></g>`),
    error: wrap(`<g stroke="#e82626" stroke-width="1.4" fill="#e82626" fill-opacity="0.3"><circle cx="16" cy="16" r="9"/><path d="M11 11 L21 21 M21 11 L11 21" stroke-width="1.8"/></g>`),
    info: wrap(`<g stroke="#5a8cc8" stroke-width="1.4" fill="none"><circle cx="16" cy="16" r="9"/><path d="M16 14 L16 22 M16 10 L16 11" stroke-width="1.8"/></g>`),
    sidebar: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="5" y="6" width="22" height="20"/><path d="M12 6 L12 26"/><rect x="6" y="7" width="5" height="18" fill="#e82626" fill-opacity="0.3" stroke="none"/></g>`),
    upload: wrap(`<g stroke="#c8ccd0" stroke-width="1.4" fill="none" stroke-linecap="square"><path d="M16 24 L16 8 M9 15 L16 8 L23 15"/></g>`),
    download: wrap(`<g stroke="#c8ccd0" stroke-width="1.4" fill="none" stroke-linecap="square"><path d="M16 8 L16 24 M9 17 L16 24 L23 17"/></g>`),
    copy: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="8" y="8" width="13" height="16"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#e82626" stroke-width="1.2" fill="none"><path d="M16 4 L22 11 L22 22 L18 26 L14 26 L10 22 L10 11 Z"/><circle cx="16" cy="13" r="2.5" fill="#e82626" fill-opacity="0.4"/></g>`),
    grid: wrap(`<g stroke="#c8ccd0" stroke-width="1.2" fill="none"><rect x="6" y="6" width="8" height="8"/><rect x="18" y="6" width="8" height="8"/><rect x="6" y="18" width="8" height="8"/><rect x="18" y="18" width="8" height="8"/></g>`),
    shield: wrap(`<g stroke="#e82626" stroke-width="1.2" fill="none"><path d="M16 4 L26 8 L26 16 Q26 23, 16 28 Q6 23, 6 16 L6 8 Z"/><path d="M11 16 L14 19 L21 12" stroke-width="1.6"/></g>`),
  },
};
