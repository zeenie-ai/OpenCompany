/**
 * The app icon is the design handoff's signed SVG (a C2PA manifest covers its
 * exact bytes). It must ship unmodified: no SVGO, no formatter, no line-ending
 * conversion (.gitattributes marks it -text). A changed hash means the file
 * was rewritten; restore it from the handoff instead of updating the pin.
 */
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const CLIENT_DIR = join(__dirname, '..', '..', '..', '..');
const ICON = join(CLIENT_DIR, 'public', 'opencompany-icon.svg');
const ICON_SHA256 = 'f3f3726e4cffb167439b2800e172108954d667a7c54b72929beb5906cc10110f';

describe('app icon', () => {
  it('ships byte-for-byte as signed', () => {
    const bytes = readFileSync(ICON);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(ICON_SHA256);
    expect(bytes.includes(Buffer.from('\r\n'))).toBe(false);
  });

  it('is the page favicon', () => {
    const html = readFileSync(join(CLIENT_DIR, 'index.html'), 'utf-8');
    expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/opencompany-icon.svg" />');
    expect(existsSync(join(CLIENT_DIR, 'public', 'vite.svg'))).toBe(false);
  });
});
