/**
 * The Workspace tabs (shared by Home and the editor): an inactive tab whose
 * surface is busy shows a dot, the active one never does, and the body
 * arriving on a switch rises in.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { render, renderHook, screen, within } from '@testing-library/react';
import { useState } from 'react';
import userEvent from '@testing-library/user-event';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { installWaapiStub } from '@/test/waapi';
import { useSurfaceRunning } from '../activity';
import { WorkspaceTabs, type WorkspaceTab } from '../WorkspaceTabs';

function Tabs({ activity }: { activity?: Partial<Record<WorkspaceTab, boolean>> }) {
  const [tab, setTab] = useState<WorkspaceTab>('board');
  return (
    <WorkspaceTabs
      tab={tab}
      onTabChange={setTab}
      activity={activity}
      browser={<p>Live page</p>}
      board={<p>The board</p>}
      android={<p>The phone</p>}
    />
  );
}

let waapi: ReturnType<typeof installWaapiStub>;

beforeEach(() => {
  waapi = installWaapiStub();
  useNodeStatusStore.setState({ allStatuses: {} });
});

afterEach(() => waapi.restore());

describe('WorkspaceTabs', () => {
  it('dots a busy tab until it is the one shown', async () => {
    const user = userEvent.setup();
    render(<Tabs activity={{ browser: true, board: true }} />);
    const browser = screen.getByRole('tab', { name: /Browser/ });
    expect(browser).toHaveAccessibleName('Browser (busy)');
    // The tab shown has no dot, whatever it is doing.
    expect(within(screen.getByRole('tab', { name: /Canvas/ })).queryByText('(busy)')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Mobile' })).toBeInTheDocument();
    await user.click(browser);
    expect(screen.getByRole('tab', { name: 'Browser' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Canvas (busy)' })).toBeInTheDocument();
  });

  it('brings the new body in on a switch, not on the first show', async () => {
    const user = userEvent.setup();
    render(<Tabs />);
    expect(waapi.calls).toHaveLength(0);
    await user.click(screen.getByRole('tab', { name: 'Mobile' }));
    expect(screen.getByText('The phone')).toBeInTheDocument();
    expect(waapi.calls).toHaveLength(1);
    expect(waapi.calls[0].target).toContainElement(screen.getByText('The phone'));
  });
});

describe('useSurfaceRunning', () => {
  it('is true while one of the surface’s nodes runs in the workflow', () => {
    const ids = ['wf:browser:1'];
    const { result, rerender } = renderHook(({ workflowId }) => useSurfaceRunning(workflowId, ids), {
      initialProps: { workflowId: 'wf' as string | null },
    });
    expect(result.current).toBe(false);
    useNodeStatusStore.setState({ allStatuses: { wf: { 'wf:browser:1': { status: 'executing' } } } } as never);
    rerender({ workflowId: 'wf' });
    expect(result.current).toBe(true);
    rerender({ workflowId: null });
    expect(result.current).toBe(false);
  });
});
