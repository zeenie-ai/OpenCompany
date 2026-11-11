import { describe, expect, it } from 'vitest';

import { resolveWorkspaceReference, workspaceFileUrl, workspacePathOf } from '../workspaceLinks';

describe('workspacePathOf', () => {
  it.each([
    // What an agent writes in a note, as the one that broke did.
    ['charts/it_headcount.png', '', 'charts/it_headcount.png'],
    ['./charts/it_headcount.png', '', 'charts/it_headcount.png'],
    ['/charts/a.png', 'reports', 'charts/a.png'],
    ['img/a.png', 'reports', 'reports/img/a.png'],
    ['../charts/a.png', 'reports', 'charts/a.png'],
    ['charts%5Ca.png', '', 'charts/a.png'],
    ['charts/my%20chart.png', '', 'charts/my chart.png'],
    ['charts/100%.png', '', 'charts/100%.png'],
  ])('%j from %j names %j', (reference, baseDir, expected) => {
    expect(workspacePathOf(reference, baseDir)).toBe(expected);
  });

  it.each([
    ['../outside.png', ''],
    ['../../outside.png', 'reports'],
    ['https://example.com/a.png', ''],
    ['//cdn.example.com/a.png', ''],
    ['mailto:someone@example.com', ''],
    ['/api/workspace/wf-1/files/a.png', ''],
    ['', ''],
    ['./', ''],
  ])('%j from %j names no workspace file', (reference, baseDir) => {
    expect(workspacePathOf(reference, baseDir)).toBeNull();
  });
});

describe('resolveWorkspaceReference', () => {
  it('serves a workspace path from the workspace file route, encoded as the server encodes a ref', () => {
    expect(resolveWorkspaceReference('charts/my%20chart.png', 'wf-1')).toBe('/api/workspace/wf-1/files/charts/my%20chart.png');
    expect(workspaceFileUrl('wf-1', 'charts/it_headcount.png')).toBe('/api/workspace/wf-1/files/charts/it_headcount.png');
  });

  it('keeps a query and a fragment', () => {
    expect(resolveWorkspaceReference('summary.md#totals', 'wf-1', 'reports')).toBe('/api/workspace/wf-1/files/reports/summary.md#totals');
    expect(resolveWorkspaceReference('chart.png?v=2', 'wf-1')).toBe('/api/workspace/wf-1/files/chart.png?v=2');
  });

  it('leaves everything else as written', () => {
    for (const reference of ['https://example.com/a.png', '#totals', '/api/workspace/wf-1/files/a.png', '']) {
      expect(resolveWorkspaceReference(reference, 'wf-1')).toBe(reference);
    }
    // Without a workflow there is no workspace to read a path against.
    expect(resolveWorkspaceReference('charts/a.png', null)).toBe('charts/a.png');
  });
});
