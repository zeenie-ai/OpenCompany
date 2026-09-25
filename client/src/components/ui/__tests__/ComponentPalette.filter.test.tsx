/**
 * Which nodes the editor palette lists. The absolute blocklist and
 * system-managed nodes are always hidden. With Normal mode on the palette
 * belongs to Dev mode and lists everything else; with the flag off its own
 * Normal filter (allowlist + group visibility) applies unless proMode is on.
 */

import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const flags = vi.hoisted(() => ({ normalMode: false, nodeSpecBackend: true }));

vi.mock('../../../lib/featureFlags', () => ({ featureFlags: flags }));

vi.mock('../../../hooks/useNodeAllowlist', () => ({
  useNodeAllowlist: () => ({
    isBlocked: (type: string) => type === 'androidBattery',
    isAllowed: (type: string) => type === 'aiAgent',
  }),
}));

vi.mock('../../../lib/nodeSpec', () => ({
  useNodeGroups: () => ({
    data: {
      agent: { visibility: 'normal', label: 'Agents', icon: '', color: '' },
      utility: { visibility: 'dev', label: 'Utility', icon: '', color: '' },
      android: { visibility: 'dev', label: 'Android', icon: '', color: '' },
      memory: { visibility: 'normal', label: 'Memory', icon: '', color: '' },
    },
    isPending: false,
  }),
  listCachedNodeSpecs: () => [
    { name: 'aiAgent', displayName: 'AI Agent', group: ['agent'] },
    { name: 'httpRequest', displayName: 'HTTP Request', group: ['utility'] },
    { name: 'androidBattery', displayName: 'Battery Monitor', group: ['android'] },
    { name: 'context', displayName: 'Context', group: ['memory'], uiHints: { systemManaged: true } },
  ],
}));

vi.mock('../../../adapters/nodeSpecToDescription', () => ({ nodeSpecToDescription: (spec: unknown) => spec }));
vi.mock('../../../assets/icons', () => ({ NodeIcon: () => null }));
vi.mock('../ComponentItem', () => ({
  default: ({ definition }: { definition: { displayName: string } }) => <div data-testid="item">{definition.displayName}</div>,
}));
vi.mock('../CollapsibleSection', () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

import ComponentPalette from '../ComponentPalette';

function listed(proMode: boolean): string[] {
  const { unmount } = render(
    <ComponentPalette
      searchQuery=""
      onSearchChange={() => {}}
      collapsedSections={{}}
      onToggleSection={() => {}}
      onDragStart={() => {}}
      proMode={proMode}
    />,
  );
  const names = screen.queryAllByTestId('item').map((el) => el.textContent ?? '');
  unmount();
  return names.sort();
}

describe('ComponentPalette filtering', () => {
  it('flag off, Normal palette: only allowlisted nodes in visible groups', () => {
    flags.normalMode = false;
    expect(listed(false)).toEqual(['AI Agent']);
  });

  it('flag off, Dev palette: everything not blocked or system-managed', () => {
    flags.normalMode = false;
    expect(listed(true)).toEqual(['AI Agent', 'HTTP Request']);
  });

  it('flag on: the editor lists every node the blocklist allows, whatever proMode says', () => {
    flags.normalMode = true;
    expect(listed(false)).toEqual(['AI Agent', 'HTTP Request']);
    expect(listed(true)).toEqual(['AI Agent', 'HTTP Request']);
  });
});
