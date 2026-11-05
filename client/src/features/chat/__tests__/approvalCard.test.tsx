/**
 * The approval card's colours (chat V2 colour pass): colour marks only where
 * the draft stands, the waiting frame and the status pill with its dot. The
 * buttons are neutral outlines; Send / Allow, and a confirming Decline or Send
 * again, is the one invert. A card on its way or sent is neutral but for its
 * pill, and one held for Resume reads as paused.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, within } from '@testing-library/react';
import { ApprovalCard } from '../approval/ApprovalCard';
import { ApprovalsContext } from '../approval/context';
import { readApprovals } from '../data/approvals';

type Wire = Record<string, unknown>;

function summary(patch: Wire = {}): Wire {
  return {
    approval_id: 'ap1',
    workflow_id: 'w1',
    kind: 'tool_call',
    status: 'pending',
    channel: 'WhatsApp',
    action: 'Send a WhatsApp message',
    recipient: '447700900123',
    recipient_label: 'Ana',
    body: 'Running late, there at 10.',
    details: [],
    created_at: '2026-10-04T08:59:00Z',
    revision: 0,
    max_length: 4096,
    editable: true,
    run_id: 'r1',
    tool_call_id: 'call_1',
    ...patch,
  };
}

/** A time `seconds` from now, as the server writes it. */
function inSeconds(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

function renderCard(patch: Wire = {}): HTMLElement {
  const approvals = readApprovals({ success: true, approvals: [summary(patch)], server_time: new Date().toISOString() }, Date.now());
  const { container } = render(
    <ApprovalsContext.Provider value={{ approvals, name: 'Maya', compact: false, decide: vi.fn(), deciding: new Set<string>(), byMode: false }}>
      <ApprovalCard approvalId="ap1" />
    </ApprovalsContext.Provider>,
  );
  return container.querySelector('[data-approval="ap1"]') as HTMLElement;
}

/** What carries the run or tools tint: only a status pill and its dot may. */
function tinted(card: HTMLElement) {
  return card.querySelectorAll('[class*="action-run"], [class*="action-tools"]');
}

function tile(card: HTMLElement) {
  return card.querySelector('.lucide-message-circle')!.parentElement!;
}

describe('a card waiting for the owner', () => {
  it('sends with the one invert button; Decline, Edit and Always allow are neutral', () => {
    const card = renderCard({ always: { label: 'Send a WhatsApp message to 447700900123', scope: 'recipient' } });
    expect(within(card).getByRole('button', { name: 'Send' })).toHaveAttribute('data-variant', 'invert');
    const decline = within(card).getByRole('button', { name: 'Decline' });
    expect(decline).toHaveAttribute('data-variant', 'quiet');
    expect(decline).toHaveClass('border-border-default');
    expect(decline.className).not.toMatch(/action-stop/);
    const edit = within(card).getByRole('button', { name: 'Edit' });
    expect(edit).toHaveAttribute('aria-pressed', 'false');
    expect(edit).toHaveClass('border-border-default', 'text-fg-default');
    const always = within(card).getByRole('button', { name: 'Always allow Send a WhatsApp message to 447700900123' });
    expect(always).toHaveAttribute('data-variant', 'quiet');
    expect(always).toHaveClass('border-border-default', 'text-fg-default');
    // The tile waits in the frame's colour, on the panel.
    expect(tile(card)).toHaveClass('bg-bg-panel', 'border-action-config-border', 'text-action-config-ink');
    expect(tinted(card)).toHaveLength(0);
  });

  it('allows a call with no message with the same invert button', () => {
    const card = renderCard({ channel: 'Shell', action: 'Use Shell', recipient: '', recipient_label: '', body: '', editable: false, generic: true });
    expect(within(card).getByRole('button', { name: 'Allow' })).toHaveAttribute('data-variant', 'invert');
  });

  it('edits in a box framed in the focus colour while focused, with Done pressed', () => {
    const card = renderCard({ subject: 'Hello' });
    fireEvent.click(within(card).getByRole('button', { name: 'Edit' }));
    const done = within(card).getByRole('button', { name: 'Done' });
    expect(done).toHaveAttribute('aria-pressed', 'true');
    expect(done).toHaveClass('bg-bg-active', 'border-border-strong', 'text-fg-default');
    const box = within(card).getByRole('textbox', { name: 'Edit the draft' });
    expect(box).toHaveClass('focus-visible:border-border-focus', 'focus-visible:ring-node-agent-soft');
    // At rest the plain border, so focus visibly leaves it.
    expect(box).not.toHaveClass('border-border-focus', 'ring-node-agent-soft');
    // Its own fill in dark mode too, not the field primitive's.
    expect(box).toHaveClass('bg-bg-input', 'dark:bg-bg-input');
    expect(box.className).not.toMatch(/bg-input\/30/);
    const subject = within(card).getByRole('textbox', { name: 'Subject' });
    expect(subject).toHaveClass('border-input', 'focus-visible:border-border-focus', 'focus-visible:ring-node-agent-soft', 'dark:bg-bg-input');
    expect(tinted(card)).toHaveLength(0);
  });

  it('declines with a note, confirmed by the invert button', () => {
    const card = renderCard();
    fireEvent.click(within(card).getByRole('button', { name: 'Decline' }));
    expect(within(card).getByRole('button', { name: 'Decline' })).toHaveAttribute('data-variant', 'invert');
    expect(within(card).getByRole('button', { name: 'Back' })).toHaveClass('border-border-default', 'text-fg-default');
    expect(within(card).getByRole('textbox', { name: 'Tell Maya what to do instead' })).toHaveClass('dark:bg-bg-input');
  });
});

describe('a card on its way', () => {
  it('counts down the Undo window in the neutral ink; only its pill is green', () => {
    const card = renderCard({ status: 'approved', undo_until: inSeconds(5) });
    const undo = within(card).getByRole('button', { name: /^Undo · \ds$/ });
    expect(undo).toHaveAttribute('data-variant', 'quiet');
    expect(undo).toHaveClass('bg-bg-panel', 'border-border-default', 'text-fg-default');
    expect(card.querySelector('.opencompany-spinner')).toHaveClass('border-border-default', 'border-t-fg-default');
    expect(card).toHaveClass('border-border-default');
    expect(tile(card)).toHaveClass('bg-bg-active', 'border-border-default', 'text-fg-default');
    expect(tinted(card)).toHaveLength(2);
  });

  it('sends with the neutral spinner', () => {
    const card = renderCard({ status: 'sending' });
    expect(within(card).getByRole('status')).toHaveTextContent('Sending on WhatsApp…');
    expect(card.querySelector('.opencompany-spinner')).toHaveClass('border-t-fg-default');
    expect(tinted(card)).toHaveLength(2);
  });

  it('held for Resume, it shows the paused pill', () => {
    const card = renderCard({ status: 'approved', undo_until: inSeconds(-1), deployment_state: 'paused' });
    expect(within(card).getByText('Waiting')).toHaveClass('bg-bg-active', 'border-border-default', 'text-fg-muted');
    expect(within(card).getByRole('button', { name: 'Undo' })).toHaveClass('bg-bg-panel', 'border-border-default');
    expect(tinted(card)).toHaveLength(0);
  });
});

describe('a card that is done', () => {
  it('a sent card is neutral but for its pill, its ticks in the primary colour', () => {
    const card = renderCard({ status: 'sent', outcome: { certainty: 'sent', at: new Date().toISOString() } });
    expect(card).toHaveClass('border-border-default');
    expect(within(card).getByText('Running late, there at 10.').parentElement).toHaveClass('bg-bg-elevated', 'border-border-default');
    expect(card.querySelector('.lucide-check-check')).toHaveClass('text-primary');
    expect(within(card).getByRole('status')).toHaveClass('bg-bg-app');
    const tick = card.querySelector('.opencompany-draw')!;
    expect(tick).toHaveClass('text-bg-app');
    expect(tick.parentElement).toHaveClass('bg-fg-default', 'border-fg-default');
    expect(tinted(card)).toHaveLength(2);
  });

  it('a declined draft restores with a neutral outline button', () => {
    const card = renderCard({ kind: 'gate', run_id: null, tool_call_id: null, status: 'discarded', restore_until: inSeconds(60) });
    const restore = within(card).getByRole('button', { name: /^Restore · \d+s$/ });
    expect(restore).toHaveAttribute('data-variant', 'quiet');
    expect(restore).toHaveClass('bg-bg-panel', 'border-border-default');
    expect(tinted(card)).toHaveLength(0);
  });

  it('a send that did not go offers Try again on the panel colour', () => {
    const card = renderCard({ status: 'failed', outcome: { certainty: 'not_sent' } });
    const again = within(card).getByRole('button', { name: 'Try again' });
    expect(again).toHaveAttribute('data-variant', 'quiet');
    expect(again).toHaveClass('bg-bg-panel', 'border-border-default');
    expect(tinted(card)).toHaveLength(0);
  });

  it('sending again what may have gone is confirmed by the invert button', () => {
    const card = renderCard({ status: 'failed', outcome: { certainty: 'unknown' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Send again' }));
    expect(within(card).getByRole('button', { name: 'Send again' })).toHaveAttribute('data-variant', 'invert');
    expect(within(card).getByRole('button', { name: 'Keep it' })).toHaveClass('border-border-default', 'text-fg-default');
  });

  it('a call that broke off offers Dismiss on the panel colour', () => {
    const card = renderCard({
      kind: 'effect',
      status: 'failed',
      channel: 'Shell',
      action: 'Run command',
      recipient: '',
      recipient_label: '',
      body: '',
      editable: false,
      outcome: { certainty: 'unknown' },
    });
    expect(within(card).getByRole('button', { name: 'Dismiss' })).toHaveClass('bg-bg-panel', 'border-border-default');
  });
});
