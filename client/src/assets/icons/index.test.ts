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

import { describe, it, expect } from 'vitest';
import { resolveLibraryIcon, resolveIcon } from './index';

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
    // PYTHON_BASE_URL so dev (Vite:3000 → backend:3010) and prod
    // (same-origin, empty prefix) both hit the right server.
    const resolved = resolveIcon('/api/schemas/nodes/aiAgent/icon');
    expect(resolved).not.toBeNull();
    expect(resolved).toMatch(/\/api\/schemas\/nodes\/aiAgent\/icon$/);
  });
});
