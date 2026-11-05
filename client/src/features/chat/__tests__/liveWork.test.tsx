/**
 * The live work pill above the box: what the run is doing, how many of its
 * steps have finished of those begun and how long it has worked, opening to
 * the steps' names once there are any; that it waits for the owner's OK
 * while it does; and the owner's choice to open it, kept for the next run.
 * Neutral throughout: the waiting dot is its only colour.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { emptyRun, type RunSnapshot, type RunStep } from '@/lib/agui/reduceRun';
import { ApprovalsContext } from '../approval/context';
import { LiveWork } from '../composer/LiveWork';
import { parseApproval, type ChatApprovals } from '../data/approvals';

const NOW = Date.parse('2026-10-04T09:00:00Z');

function run(patch: Partial<RunSnapshot> = {}): RunSnapshot {
  return { ...emptyRun('r1', 'w1'), state: 'running', startedAt: new Date(NOW - 4_000).toISOString(), ...patch };
}

function step(stepId: string, state: RunStep['state'], name = stepId): RunStep {
  return { stepId, name, state };
}

function pill(working: RunSnapshot | null, { compact = false, stopping = false } = {}): ReactNode {
  return <LiveWork run={working} name="Maya" stopping={stopping} compact={compact} />;
}

function region() {
  return screen.getByRole('region', { name: 'Maya’s progress' });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('LiveWork', () => {
  it('shows nothing while no run works', () => {
    const { container } = render(pill(null));
    expect(container).toBeEmptyDOMElement();
  });

  it('says what the run is doing and keeps its clock', () => {
    render(pill(run()));
    expect(within(region()).getByRole('status')).toHaveTextContent('Getting started');
    expect(region()).toHaveTextContent('4s');
    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(region()).toHaveTextContent('6s');
  });

  it('opens to the steps once there are any', () => {
    const { rerender } = render(pill(run()));
    const button = within(region()).getByRole('button');
    // Nothing to open before the first step: no chevron.
    expect(button).toBeDisabled();
    expect(button.querySelector('svg')).toBeNull();

    rerender(pill(run({ steps: [step('c1', 'running', 'Checked Google Calendar')] })));
    expect(button).toBeEnabled();
    expect(within(region()).getByRole('status')).toHaveTextContent('Checked Google Calendar');
    expect(region()).toHaveTextContent('0/1 · 4s');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(within(region()).getByRole('list', { name: 'Steps' })).toHaveTextContent('Checked Google Calendar');
  });

  it('keeps the owner’s choice to open it for the next run', () => {
    const { rerender } = render(pill(run({ steps: [step('c1', 'done', 'Checked Google Calendar')] })));
    fireEvent.click(within(region()).getByRole('button'));
    rerender(pill(null));
    expect(screen.queryByRole('region', { name: 'Maya’s progress' })).not.toBeInTheDocument();

    rerender(pill(run({ runId: 'r2', steps: [step('s1', 'running', 'Searched the web')] })));
    expect(within(region()).getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    expect(within(region()).getByRole('list', { name: 'Steps' })).toHaveTextContent('Searched the web');
  });

  it('says so while the run waits for the owner’s OK on a call', () => {
    const approval = parseApproval({ approval_id: 'ap1', status: 'pending', waits: true, run_id: 'r1', tool_call_id: 'call_1' })!;
    const approvals: ChatApprovals = { byId: new Map([['ap1', approval]]), order: ['ap1'], offsetMs: 0, receivedAt: 0 };
    render(
      <ApprovalsContext.Provider value={{ approvals, name: 'Maya', compact: false, decide: vi.fn(), deciding: new Set(), byMode: false }}>
        {pill(run({ steps: [step('call_1', 'done', 'Send a WhatsApp message')] }))}
      </ApprovalsContext.Provider>,
    );
    expect(within(region()).getByRole('status')).toHaveTextContent('Waiting for your OK');
    // The held call is the step under way.
    expect(region()).toHaveTextContent('0/1 · 4s');
    expect(region().querySelector('.opencompany-pip-pulse')).toHaveClass('bg-action-config-ink');
    expect(region().querySelector('.opencompany-spinner')).toBeNull();
  });

  it('keeps its ticking clock out of the button’s name', () => {
    render(pill(run({ steps: [step('c1', 'running', 'Checked Google Calendar')] })));
    expect(within(region()).getByRole('button', { name: 'Checked Google Calendar' })).toBeInTheDocument();
    expect(region()).toHaveTextContent('4s');
  });

  it('says each step’s state, not only its mark', () => {
    render(pill(run({ steps: [step('a', 'done'), step('b', 'failed'), step('c', 'skipped'), step('d', 'running')] })));
    fireEvent.click(within(region()).getByRole('button'));
    const items = within(region()).getAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual(['a, done', 'b, failed', 'c, skipped', 'd, in progress']);
  });

  it('hands focus on when it goes while its button has it', () => {
    const onLeave = vi.fn();
    const working = run({ steps: [step('c1', 'running')] });
    const view = (current: RunSnapshot | null) => <LiveWork run={current} name="Maya" stopping={false} compact={false} onLeave={onLeave} />;
    const { rerender } = render(view(working));
    rerender(view(null));
    expect(onLeave).not.toHaveBeenCalled();
    rerender(view(working));
    within(region()).getByRole('button').focus();
    rerender(view(null));
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it('says it is stopping', () => {
    render(pill(run({ steps: [step('c1', 'running', 'Checked Google Calendar')] }), { stopping: true }));
    expect(within(region()).getByRole('status')).toHaveTextContent('Stopping…');
  });

  it('takes no action colour, and stays small in a compact chat', () => {
    render(
      pill(
        run({ steps: [step('a', 'done'), step('b', 'failed'), step('c', 'skipped'), step('d', 'running')] }),
        { compact: true },
      ),
    );
    const button = within(region()).getByRole('button');
    expect(button).toHaveClass('h-7', 'text-2xs');
    fireEvent.click(button);
    expect(within(region()).getAllByRole('listitem')).toHaveLength(4);
    expect(region().innerHTML).not.toMatch(/action-(run|tools|stop|save|config|secret)/);
  });
});
