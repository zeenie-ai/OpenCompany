/**
 * Icon resolver tests.
 *
 * Locks in:
 *   - lucide library refs resolve to a renderable React component
 *   - lookup is case-insensitive (mirrors the lobehub behavior)
 *   - unknown lucide names return null instead of an unrenderable value
 *   - lobehub regression: `lobehub:Claude` still wires to the branded
 *     library export
 *   - asset:* / data: / http: / emoji passthrough still behaves
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, it, expect } from 'vitest';
import { resolveLibraryIcon, resolveIcon } from './index';

/** Every `meta.json` under server/nodes, found without a glob dependency. */
const pluginMetaFiles = (dir: string): string[] => {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === '__pycache__' || entry === 'node_modules') continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) found.push(...pluginMetaFiles(path));
    else if (entry === 'meta.json') found.push(path);
  }
  return found;
};

describe('resolveLibraryIcon', () => {
  it('resolves a lucide icon to a forwardRef React component', () => {
    const Icon = resolveLibraryIcon('lucide:Battery');
    expect(Icon).not.toBeNull();
    // Lucide icons are forwardRef'd: object with $$typeof.
    expect(typeof Icon).toBe('object');
  });

  it('is case-insensitive for lucide lookups', () => {
    const lower = resolveLibraryIcon('lucide:battery');
    const upper = resolveLibraryIcon('lucide:BATTERY');
    const mixed = resolveLibraryIcon('lucide:Battery');
    expect(lower).toBe(mixed);
    expect(upper).toBe(mixed);
  });

  it('returns null for unknown lucide names', () => {
    expect(resolveLibraryIcon('lucide:NonexistentIconXYZ')).toBeNull();
  });

  it('still resolves lobehub brand icons (regression)', () => {
    const Icon = resolveLibraryIcon('lobehub:Claude');
    expect(Icon).not.toBeNull();
  });

  /**
   * Cross-language contract. The backend lets a plugin point at a library
   * glyph from its own meta.json instead of vendoring an SVG, and nothing
   * on the Python side can tell whether the name actually exists here.
   *
   * The specific trap: lucide's lookup index is keyed on lowercased
   * *export* names, so `lucide:CheckCheck` resolves and the kebab-case
   * file name `lucide:check-check` does not. That failure is silent -- the
   * node renders no icon rather than erroring -- so it is asserted here.
   */
  it('resolves every library icon declared in a plugin meta.json', () => {
    const nodesDir = join(__dirname, '..', '..', '..', '..', 'server', 'nodes');
    const metaFiles = pluginMetaFiles(nodesDir);
    // Guards the guard: color-only meta.json files exist regardless of how
    // many plugins currently use icon refs, so an empty list here means the
    // discovery walk broke — not that the inventory shrank. (Zero *refs* is
    // a legitimate state: brand-heavy plugins ship SVGs instead.)
    expect(metaFiles.length).toBeGreaterThan(0);

    const declared: Array<[string, string]> = [];
    for (const file of metaFiles) {
      const meta = JSON.parse(readFileSync(file, 'utf-8')) as {
        icon?: string;
        icons?: Record<string, string>;
      };
      const refs = [meta.icon, ...Object.values(meta.icons ?? {})];
      for (const ref of refs) {
        if (ref && ref.includes(':')) declared.push([file, ref]);
      }
    }

    const unresolved = declared.filter(([, ref]) => resolveLibraryIcon(ref) === null);
    expect(unresolved).toEqual([]);
  });

  /**
   * The same contract for the credential catalogue: a `lobehub:` brand
   * with no deep import here draws an empty tile on Home's Connectors page
   * and in the Credentials modal, with no error anywhere.
   */
  it('resolves every library icon declared in the credential catalogue', () => {
    const file = join(__dirname, '..', '..', '..', '..', 'server', 'config', 'credential_providers.json');
    const catalogue = JSON.parse(readFileSync(file, 'utf-8')) as {
      providers: Record<string, { icon_ref?: string | null }>;
    };
    // Library refs only: `lobehub:xai`, not a URL's `https://`.
    const refs = Object.entries(catalogue.providers).filter(
      (entry): entry is [string, { icon_ref: string }] =>
        typeof entry[1].icon_ref === 'string' && /^[a-z]+:(?!\/\/)/i.test(entry[1].icon_ref),
    );
    expect(refs.length).toBeGreaterThan(0);
    const unresolved = refs.filter(([, provider]) => resolveLibraryIcon(provider.icon_ref) === null).map(([id]) => id);
    expect(unresolved).toEqual([]);
  });

  it('returns null for unknown library prefixes', () => {
    expect(resolveLibraryIcon('madeup:Battery')).toBeNull();
  });

  it('returns null for non-prefixed strings (callers fall through to resolveIcon)', () => {
    expect(resolveLibraryIcon('🔋')).toBeNull();
    expect(resolveLibraryIcon('asset:python')).toBeNull();
    expect(resolveLibraryIcon('')).toBeNull();
    expect(resolveLibraryIcon(null)).toBeNull();
  });
});

describe('resolveIcon', () => {
  it('passes through data: / http: / absolute URLs', () => {
    expect(resolveIcon('data:image/svg+xml,<svg/>')).toBe('data:image/svg+xml,<svg/>');
    expect(resolveIcon('https://example.com/x.svg')).toBe('https://example.com/x.svg');
    expect(resolveIcon('/local.svg')).toBe('/local.svg');
  });

  it('passes through plain emoji / short text', () => {
    expect(resolveIcon('🔋')).toBe('🔋');
  });

  it('returns null for empty / null / library-prefixed strings', () => {
    expect(resolveIcon('')).toBeNull();
    expect(resolveIcon(null)).toBeNull();
    expect(resolveIcon('lucide:Battery')).toBeNull();
    expect(resolveIcon('lobehub:Claude')).toBeNull();
  });

  it('returns null for unknown asset keys (visible gap, not silent fallback)', () => {
    expect(resolveIcon('asset:nonexistent-icon-xyz')).toBeNull();
  });

  it('prefixes /api/ icon paths with PYTHON_BASE_URL (RFC §6.5 — backend-served icons)', () => {
    // Per-plugin icon.svg endpoint emitted by BaseNode._metadata_dict
    // when the plugin folder has a co-located icon. Resolver prefixes
    // PYTHON_BASE_URL ('' same-origin in dev AND prod — the dev Vite
    // server proxies /api) so a VITE_PYTHON_SERVICE_URL remote-backend
    // override still lands on the right server.
    const resolved = resolveIcon('/api/schemas/nodes/aiAgent/icon');
    expect(resolved).not.toBeNull();
    expect(resolved).toMatch(/\/api\/schemas\/nodes\/aiAgent\/icon$/);
  });
});
