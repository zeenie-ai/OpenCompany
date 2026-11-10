/**
 * The Workspace tabs, the same on Home and in the editor: pill tabs in one
 * header row with the dock's controls, a red dot on an inactive tab whose
 * surface is busy (the active one never has it), room under each body for
 * the dock's status pill, and a body that rises in on a switch.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, render, renderHook, screen, within } from '@testing-library/react';
import { useState, type ReactNode } from 'react';
import userEvent from '@testing-library/user-event';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { installWaapiStub } from '@/test/waapi';
import { useSurfaceRunning } from '../activity';
import { WorkspaceTabs, type WorkspaceTab } from '../WorkspaceTabs';

function Tabs({ activity, end }: { activity?: Partial<Record<WorkspaceTab, boolean>>; end?: ReactNode }) {
  const [tab, setTab] = useState<WorkspaceTab>('board');
  return (
    <WorkspaceTabs
      tab={tab}
      onTabChange={setTab}
      activity={activity}
      end={end}
      browser={<p>Live page</p>}
      board={<p>The board</p>}
      android={<p>The phone</p>}
    />
  );
}

/** The dot drawn on a tab while its surface is busy. */
const tabDot = (name: RegExp) => screen.getByRole('tab', { name }).querySelector('[data-slot="workspace-tab-dot"]');

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
    expect(tabDot(/Canvas/)).toBeNull();
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
    // The body itself, not something around it.
    const body = screen.getByRole('tabpanel', { name: 'Mobile' });
    expect(body).toContainElement(screen.getByText('The phone'));
    expect(waapi.calls[0].target).toBe(body);
  });

  it('puts the tabs and the dock’s controls in one header row, the controls outside the tablist', async () => {
    const user = userEvent.setup();
    render(<Tabs end={<button type="button">Close workspace</button>} />);
    const tablist = screen.getByRole('tablist', { name: 'Workspace views' });
    const close = screen.getByRole('button', { name: 'Close workspace' });
    expect(tablist).not.toContainElement(close);
    // In the tabs' row, after them.
    expect(tablist.nextElementSibling).toBe(close);
    expect(tablist.parentElement).toHaveClass('h-(--h-home-header)', 'border-b', 'border-border-default');
    // As high as its row, so the sideways scroll clips neither the raised tab's shadow nor a focus ring.
    expect(tablist).toHaveClass('no-scrollbar', 'min-w-0', 'flex-1', 'gap-1.5', 'self-stretch', 'overflow-x-auto');
    expect(within(tablist).getAllByRole('tab')).toHaveLength(3);
    // The arrow keys still move along the tabs only.
    await user.click(screen.getByRole('tab', { name: 'Canvas' }));
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Mobile' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Mobile' })).toHaveFocus();
  });

  it('keeps the same row with nothing at its end', () => {
    render(<Tabs />);
    const tablist = screen.getByRole('tablist', { name: 'Workspace views' });
    expect(tablist.parentElement).toHaveClass('h-(--h-home-header)', 'border-b', 'border-border-default');
    expect(tablist.parentElement?.children).toHaveLength(1);
  });

  it('raises the selected pill tab and leaves the others ghost, with no underline', () => {
    render(<Tabs />);
    const selected = screen.getByRole('tab', { name: 'Canvas' });
    expect(selected).toHaveAttribute('data-state', 'active');
    expect(selected).toHaveClass(
      'h-8.5', 'rounded-lg', 'px-2.5', 'border', 'border-transparent',
      'data-[state=active]:border-border-default', 'data-[state=active]:bg-bg-elevated',
      'data-[state=active]:text-fg-default', 'data-[state=active]:shadow-card',
    );
    // One name at a time: given two, `not.toHaveClass` fails only when both are there.
    expect(selected).not.toHaveClass('border-b-2');
    expect(selected).not.toHaveClass('data-[state=active]:border-node-agent');
    const other = screen.getByRole('tab', { name: 'Browser' });
    expect(other).toHaveAttribute('data-state', 'inactive');
    expect(other).toHaveClass('text-fg-muted', 'hover:bg-bg-hover', 'hover:text-fg-default');
  });

  it('dots a busy tab in red', () => {
    render(<Tabs activity={{ browser: true }} />);
    expect(tabDot(/Browser/)).toHaveClass('bg-destructive', 'opencompany-pip-pulse');
    expect(tabDot(/Browser/)).not.toHaveClass('bg-node-trigger');
  });

  it('keeps 60px free under every body for the dock’s status pill', () => {
    render(<Tabs />);
    const bodies = screen.getAllByRole('tabpanel', { hidden: true });
    expect(bodies).toHaveLength(3);
    for (const body of bodies) expect(body).toHaveClass('pb-15');
  });
});

describe('useSurfaceRunning', () => {
  it('is true while one of the surface’s nodes runs in the workflow', () => {
    const ids = ['wf:browser:1'];
    const { result, rerender } = renderHook(({ workflowId }) => useSurfaceRunning(workflowId, ids), {
      initialProps: { workflowId: 'wf' as string | null },
    });
    expect(result.current).toBe(false);
    // The store alone tells it: nothing else renders it again.
    act(() => useNodeStatusStore.setState({ allStatuses: { wf: { 'wf:browser:1': { status: 'executing' } } } } as never));
    expect(result.current).toBe(true);
    rerender({ workflowId: null });
    expect(result.current).toBe(false);
  });
});
