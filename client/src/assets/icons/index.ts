/**
 * Wave 10.B: schema-driven icon resolver — n8n-aligned prefix scheme.
 *
 * Backend plugins declare an icon string; the resolver picks the
 * right source based on the prefix (same `file:` / `fa:` / URL
 * pattern n8n uses, extended for React icon libraries):
 *
 *   `asset:<key>`       → filesystem SVG (Vite `import.meta.glob`
 *                         over `client/src/assets/icons/**\/*.svg`).
 *                         Key is the filename minus `.svg`. Dropping
 *                         a `<key>.svg` into any subfolder registers
 *                         it automatically — no central edit.
 *   `<lib>:<brand>`     → React component from an installed NPM icon
 *                         library. `<lib>` names the package
 *                         (`lobehub` today; add more in
 *                         `ICON_LIBRARIES` below). `<brand>` is a
 *                         case-insensitive lookup. `.Color` variant
 *                         preferred, `.Avatar` fallback.
 *   `data:...`          → data-URI passthrough (inline SVG / base64).
 *   `http(s)://...`     → remote URL passthrough.
 *   `/...`              → absolute local URL passthrough.
 *   plain text          → emoji / short label (rendered as-is).
 *
 * Consumers call `resolveLibraryIcon` first for React component
 * icons, then `resolveIcon` for strings / image URIs. No node-type
 * knowledge anywhere in the frontend.
 */

import * as React from 'react';
import * as Lucide from 'lucide-react';

import { API_CONFIG } from '../../config/api';
// Deep imports of just the brand icons we expose via `lobehub:<brand>`.
// A namespace import (`import * as Lobehub`) would re-evaluate the package
// index, which re-exports antd-using feature modules (Editor / Dashboard /
// ProviderCombine / ProviderIcon / ModelIcon) and drags `antd` into the
// bundle. Whenever the backend declares a new `lobehub:<brand>` icon in
// `server/nodes/visuals.json` or `server/config/credential_providers.json`,
// add the matching deep import + entry to LOBEHUB_BRANDS below.
import OpenAI from '@lobehub/icons/es/OpenAI';
import Claude from '@lobehub/icons/es/Claude';
import Gemini from '@lobehub/icons/es/Gemini';
import Groq from '@lobehub/icons/es/Groq';
import Cerebras from '@lobehub/icons/es/Cerebras';
import OpenRouter from '@lobehub/icons/es/OpenRouter';
import DeepSeek from '@lobehub/icons/es/DeepSeek';
import Kimi from '@lobehub/icons/es/Kimi';
import Mistral from '@lobehub/icons/es/Mistral';
import Ollama from '@lobehub/icons/es/Ollama';
import LmStudio from '@lobehub/icons/es/LmStudio';
import Vercel from '@lobehub/icons/es/Vercel';
import Github from '@lobehub/icons/es/Github';
import VertexAI from '@lobehub/icons/es/VertexAI';
import GoogleCloud from '@lobehub/icons/es/GoogleCloud';

type RawSvg = string;

// Eagerly load every .svg file as its raw string contents so the
// resolver is synchronous at render time.
const svgModules = import.meta.glob<RawSvg>('./**/*.svg', {
  eager: true,
  query: '?raw',
  import: 'default',
});

const svgToDataUri = (svg: string): string => {
  const encoded = encodeURIComponent(svg).replace(/'/g, '%27').replace(/"/g, '%22');
  return `data:image/svg+xml,${encoded}`;
};

const keyFromPath = (path: string): string => {
  // './google/gmail.svg' -> 'gmail'
  const filename = path.split('/').pop() ?? '';
  return filename.replace(/\.svg$/i, '');
};

const entries = Object.entries(svgModules)
  .sort(([a], [b]) => a.localeCompare(b)) // deterministic on collisions
  .map(([path, raw]) => [keyFromPath(path), svgToDataUri(raw as string)] as const);

/** Filesystem-derived asset key -> SVG data URI. */
export const ICON_REGISTRY: Readonly<Record<string, string>> = Object.fromEntries(entries);

/**
 * Resolve a backend-declared icon string to something renderable.
 * Contract:
 *   `asset:<key>`     → look up in ICON_REGISTRY (filesystem-derived)
 *   `/api/...`        → prefix with PYTHON_BASE_URL (backend-served icon
 *                       via GET /api/schemas/nodes/<type>/icon — RFC §6.5)
 *   `data:...`        → pass through
 *   `http(s)://...`   → pass through
 *   other `/...`      → pass through (already same-origin)
 *   otherwise         → render as-is (emoji / short text)
 * Returns `null` when the icon string is empty or an unknown asset key,
 * so callers can apply their own fallback.
 */
export const resolveIcon = (icon: string | undefined | null): string | null => {
  if (!icon) return null;
  if (icon.startsWith('asset:')) {
    // Unknown asset key — backend declared a file that doesn't exist in
    // client/src/assets/icons/. Return null so the gap is visible to
    // the author rather than masked by a fallback emoji.
    return ICON_REGISTRY[icon.slice('asset:'.length)] ?? null;
  }
  if (icon.startsWith('/api/')) {
    // Backend-served icon endpoint. In dev the FE runs on Vite:3000 and
    // the backend on :3010, so a bare relative path would hit the Vite
    // server. Prefix with PYTHON_BASE_URL (empty string in prod, full
    // localhost URL in dev) so the browser fetches the right origin.
    return `${API_CONFIG.PYTHON_BASE_URL}${icon}`;
  }
  if (icon.startsWith('data:') || icon.startsWith('http://') || icon.startsWith('https://') || icon.startsWith('/')) {
    return icon;
  }
  // Library-prefixed strings are handled by `resolveLibraryIcon`; if
  // they reach here it means the caller only consulted `resolveIcon`
  // and should also try the library resolver — treat as unresolvable.
  if (icon.includes(':')) return null;
  return icon; // plain emoji / short text — callers render as <span>
};

/** True when the resolved icon is an image URI (data: or http: or /path). */
export const isImageIcon = (resolved: string): boolean =>
  resolved.startsWith('data:') || resolved.startsWith('http') || resolved.startsWith('/');

/**
 * Library-icon resolver. Dispatches `<lib>:<brand>` strings to the
 * matching NPM icon package. Adding a new library is one entry in
 * `ICON_LIBRARIES` — no per-brand hardcoding, names come from the
 * package's own exports.
 */
// Both lucide and lobehub icon components accept `size` (px) and
// `className` (Tailwind sizing). Widening here lets call sites compose
// with `h-6 w-6` instead of pixel literals.
export type LibraryIcon = React.FC<{ size?: number; className?: string }>;

type LibraryResolver = (brand: string) => LibraryIcon | null;

// Build a case-insensitive name index for libraries we still namespace-import.
const indexLibrary = (lib: Record<string, unknown>): Record<string, string> =>
  Object.fromEntries(Object.keys(lib).map((name) => [name.toLowerCase(), name]));

const lucideIndex = indexLibrary(Lucide as Record<string, unknown>);

// Static brand map keyed by lowercase brand string. Adding a new lobehub:<brand>
// requires the matching deep import at the top of this file plus an entry below.
const LOBEHUB_BRANDS: Readonly<Record<string, any>> = {
  openai: OpenAI,
  claude: Claude,
  gemini: Gemini,
  groq: Groq,
  cerebras: Cerebras,
  openrouter: OpenRouter,
  deepseek: DeepSeek,
  kimi: Kimi,
  mistral: Mistral,
  ollama: Ollama,
  lmstudio: LmStudio,
  vercel: Vercel,
  github: Github,
  vertexai: VertexAI,
  googlecloud: GoogleCloud,
};

const ICON_LIBRARIES: Readonly<Record<string, LibraryResolver>> = {
  lobehub: (brand) => {
    const entry = LOBEHUB_BRANDS[brand.toLowerCase()];
    // `.Color` = multi-color brand artwork (openai, claude, gemini, …).
    // Mono-only brands (Github, Vercel) don't export it — their compound
    // default IS the Mono glyph component (`fill="currentColor"`), which
    // scales with NodeIcon's `h-full w-full` and tints with the site's
    // color cascade, staying visible on every theme. The `.Avatar`
    // variant is deliberately NOT used: it renders a fixed-size tile
    // with a hardcoded brand background (black for GitHub) that neither
    // scales with the wrapper nor adapts to dark surfaces.
    return entry?.Color ?? entry ?? null;
  },
  // Lucide ships ~1,500 PascalCase forwardRef'd icon components. Same
  // prefix-dispatch contract as lobehub: backend plugins declare
  // `lucide:Battery` and every consumer already chains
  // resolveLibraryIcon -> resolveIcon, so they light up automatically.
  // Keeps generic UI symbols (battery, wifi, folder, search, ...)
  // declarative without per-icon SVG drops.
  lucide: (name) => {
    const exportName = lucideIndex[name.toLowerCase()];
    if (!exportName) return null;
    const entry = (Lucide as Record<string, any>)[exportName];
    // Lucide icons are forwardRef objects ({ $$typeof, render }); the
    // package also exports a `createLucideIcon` factory function that
    // is not directly renderable. Filter to renderable values only.
    if (entry && typeof entry === 'object' && '$$typeof' in entry) return entry;
    return null;
  },
  // Add `simpleicons` / other NPM icon packages here as needed:
  // simpleicons: (brand) => import('simple-icons').si[brand]?.svg ...
};

export const resolveLibraryIcon = (icon: string | undefined | null): LibraryIcon | null => {
  if (!icon) return null;
  const sep = icon.indexOf(':');
  if (sep <= 0) return null;
  const lib = icon.slice(0, sep);
  const resolver = ICON_LIBRARIES[lib];
  return resolver ? resolver(icon.slice(sep + 1)) : null;
};

// Single icon-rendering primitive — see NodeIcon.tsx. Re-exported here
// so consumers import everything from `assets/icons`.
export { NodeIcon, type NodeIconProps } from './NodeIcon';
