/**
 * Drafts waiting for the owner in the chat, against a fake server: a card on
 * the reply that made it (and the employee's own drafts after the
 * conversation), Send with Undo while it can, an edit sent with Ctrl+Enter,
 * Ctrl+Enter anywhere for the newest draft, and the Ask first chip, which
 * asks before turning off. The card's copy says what really happened.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

import { resetChatRunStore } from '@/stores/chatRunStore';
import { approvalView } from '../approval/view';
import { ChatPane } from '../ChatPane';
import { isOpenApproval, optimistic, parseApproval, readApprovals, secondsLeft, type ChatApproval } from '../data/approvals';
import type { ChatHost } from '../host';
import { useComposerStore } from '../state/composerStore';
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;

const NOW = Date.parse('2026-10-04T09:00:00Z');

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

function approval(patch: Wire = {}): ChatApproval {
  return parseApproval(summary(patch))!;
}

describe('the card says what happened', () => {
  const view = (patch: Wire, options: { offsetMs?: number } = {}) =>
    approvalView(approval(patch), { name: 'Maya', nowMs: NOW, offsetMs: options.offsetMs ?? 0, now: new Date(NOW) });

  it('waits for the owner, then counts down the Undo window', () => {
    const waiting = view({});
    expect(waiting.title).toBe('Maya wants to send a WhatsApp message');
    expect(waiting.pill).toEqual({ label: 'Needs your OK', tone: 'waiting', pulse: true });
    expect(waiting.footer).toEqual({ kind: 'decide' });
    expect(view({ status: 'approved', undo_until: '2026-10-04T09:00:04.2Z' }).footer).toEqual({ kind: 'undo', seconds: 5 });
    // The server's clock is 3 s ahead: its window ends sooner here.
    expect(view({ status: 'approved', undo_until: '2026-10-04T09:00:04.2Z' }, { offsetMs: 3000 }).footer).toEqual({ kind: 'undo', seconds: 2 });
    expect(view({ status: 'approved', undo_until: '2026-10-04T08:59:59Z' }).footer).toEqual({ kind: 'sending' });
    expect(view({ status: 'sending' }).footer).toEqual({ kind: 'sending' });
  });

  it('a gate’s draft for a paused employee goes on Resume', () => {
    const paused = view({ kind: 'gate', status: 'approved', undo_until: '2026-10-04T08:59:59Z', deployment_state: 'paused' });
    expect(paused.footer).toEqual({ kind: 'waiting', text: 'Sends when you resume Maya.' });
  });

  it('says it went only once it went', () => {
    const sent = view({ status: 'sent', outcome: { certainty: 'sent', at: '2026-10-04T09:00:05Z' }, approved_by: 'auto' });
    expect(sent.title).toBe('Message sent');
    expect(sent.footer.kind === 'sent' && sent.footer.text).toMatch(/^Sent to Ana on WhatsApp at .+, without asking: Ask first was off\.$/);
    const failed = view({ status: 'failed', outcome: { certainty: 'unknown', error: 'TimeoutError' } });
    expect(failed.footer).toEqual({ kind: 'failed', text: 'It may have gone out: TimeoutError', unknown: true });
    expect(view({ status: 'failed', outcome: { certainty: 'not_sent' } }).footer).toEqual({ kind: 'failed', text: 'It didn’t go out.', unknown: false });
    expect(view({ status: 'expired' }).footer).toEqual({ kind: 'ended', text: 'It waited too long, so it was not sent.' });
    expect(view({ status: 'cancelled' }).title).toBe('Draft cancelled');
  });

  it('a discarded draft can come back while its window lasts', () => {
    const gate = view({ kind: 'gate', status: 'discarded', restore_until: '2026-10-04T09:00:03Z' });
    expect(gate.footer).toEqual({ kind: 'discarded', text: 'Discarded. Maya won’t send this.', restoreSeconds: 3, canRestore: true });
    const held = view({ status: 'discarded', restore_until: '2026-10-11T09:00:00Z' });
    expect(held.footer).toMatchObject({ restoreSeconds: null, canRestore: true });
    expect(view({ status: 'discarded', restore_until: '2026-10-04T08:00:00Z' }).footer).toMatchObject({ canRestore: false });
  });

  it('reads the list with the server’s clock, and moves at once on a decision', () => {
    const read = readApprovals({ success: true, approvals: [summary(), summary(), { nope: 1 }], server_time: '2026-10-04T09:00:02Z' }, NOW);
    expect(read.order).toEqual(['ap1']);
    expect(read.offsetMs).toBe(2000);
    expect(secondsLeft('2026-10-04T09:00:05Z', read.offsetMs, NOW)).toBe(3);
    expect(isOpenApproval(approval({ status: 'approved', consumed_at: '2026-10-04T09:00:00Z' }), NOW)).toBe(false);
    expect(optimistic(approval(), 'send', NOW, 0)).toMatchObject({ status: 'approved', undoUntil: '2026-10-04T09:00:05.000Z' });
    expect(optimistic(approval({ status: 'approved' }), 'undo', NOW, 0)).toMatchObject({ status: 'pending', undoUntil: null });
  });
});

// ----- in the chat -----

let server: { approvals: Wire[]; askFirst: boolean | null; decisions: Wire[]; rule: Wire[] };
let client: QueryClient;

function row(id: string, role: 'user' | 'assistant', text: string, patch: Wire = {}): Wire {
  return { id, role, text, message: text, timestamp: '2026-10-04T09:00:00Z', run_key: 'g1', ...patch };
}

const MESSAGES = [
  row('m1', 'user', 'Tell Ana I’m late', { run_id: 'r1' }),
  row('a_r1', 'assistant', 'I drafted a WhatsApp to Ana.', { run_id: 'r1', parts: { approvals: [{ approval_id: 'ap1', tool_call_id: 'call_1' }] } }),
];

function host(): ChatHost {
  return { kind: 'home', sessionId: 'w1', scope: 'all', persona: { name: 'Maya', colorRole: 'agent' }, composer: 'send', notify: vi.fn() };
}

function renderPane() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPane host={host()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  server = { approvals: [summary()], askFirst: true, decisions: [], rule: [] };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: [] };
      case 'get_chat_messages':
        return { success: true, messages: MESSAGES, thread: { active_leaf_id: null, revision: 1 }, active_runs: [] };
      case 'list_approvals':
        return { success: true, approvals: server.approvals, server_time: new Date().toISOString() };
      case 'get_ask_first':
        return { success: true, ask_first: server.askFirst, revision: 0 };
      case 'set_ask_first':
        server.rule.push(data);
        return { success: true, ask_first: data.ask_first, revision: 1, replies_gated: true, needs_apply: false };
      case 'decide_approval': {
        server.decisions.push(data);
        const current = server.approvals[0];
        const next =
          data.decision === 'send'
            ? { ...current, status: 'approved', undo_until: new Date(Date.now() + 5000).toISOString(), revision: 1, ...(data.text ? { body: data.text } : {}) }
            : data.decision === 'undo'
              ? { ...current, status: 'pending', revision: 2 }
              : current;
        server.approvals = [next];
        return { success: true, approval: next };
      }
      default:
        return { success: true };
    }
  });
});

describe('a draft in the chat', () => {
  it('shows on the reply that made it; Send, then Undo while it can', async () => {
    renderPane();
    const card = (await screen.findByText('Maya wants to send a WhatsApp message')).closest('[data-approval]') as HTMLElement;
    const reply = screen.getByText('I drafted a WhatsApp to Ana.').closest('[data-turn="assistant"]') as HTMLElement;
    expect(reply.contains(card)).toBe(true);
    expect(within(card).queryByText(/goes out until you send it/)).not.toBeInTheDocument();

    fireEvent.click(within(card).getByRole('button', { name: /^Send$/ }));
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ approval_id: 'ap1', decision: 'send' });
    expect(server.decisions[0]).not.toHaveProperty('text');
    const undo = await within(card).findByRole('button', { name: /Undo · \ds/ });
    fireEvent.click(undo);
    await waitFor(() => expect(server.decisions[1]).toMatchObject({ decision: 'undo' }));
    expect(await within(card).findByRole('button', { name: /^Send$/ })).toBeInTheDocument();
    // Every click carries its own key.
    expect(server.decisions[0].decision_key).not.toBe(server.decisions[1].decision_key);
  });

  it('sends an edit with Ctrl+Enter', async () => {
    renderPane();
    const card = (await screen.findByText('Maya wants to send a WhatsApp message')).closest('[data-approval]') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: 'Edit' }));
    const box = within(card).getByRole('textbox', { name: 'Edit the draft' });
    fireEvent.change(box, { target: { value: 'Running late, there at 10:15.' } });
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ decision: 'send', text: 'Running late, there at 10:15.' });
  });

  it('Ctrl+Enter anywhere in the chat sends the newest draft', async () => {
    renderPane();
    await screen.findByText('Maya wants to send a WhatsApp message');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message Maya' }), { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ approval_id: 'ap1', decision: 'send' });
  });

  it('shows the employee’s own drafts after the conversation', async () => {
    server.approvals = [summary({ approval_id: 'g1', kind: 'gate', run_id: null, tool_call_id: null, action: null, body: 'Yes, Saturday at 10 works.' })];
    renderPane();
    const own = await screen.findByRole('region', { name: 'Drafts waiting for you' });
    expect(within(own).getByText('Maya wants to send a message')).toBeInTheDocument();
    expect(within(own).getByText('Yes, Saturday at 10 works.')).toBeInTheDocument();
  });

  it('announces a discarded draft once, not its Restore countdown', async () => {
    const restore = new Date(Date.now() + 60_000).toISOString();
    server.approvals = [summary({ approval_id: 'g1', kind: 'gate', run_id: null, tool_call_id: null, status: 'discarded', restore_until: restore })];
    renderPane();
    const own = await screen.findByRole('region', { name: 'Drafts waiting for you' });
    expect(within(own).getByRole('status')).toHaveTextContent('Discarded. Maya won’t send this.');
    expect(within(own).getByRole('button', { name: /^Restore/ })).toHaveAttribute('aria-live', 'off');
  });

  it('announces a send that did not go', async () => {
    server.approvals = [summary({ status: 'failed', outcome: { certainty: 'not_sent' } })];
    renderPane();
    const card = (await screen.findByText('I drafted a WhatsApp to Ana.')).closest('[data-turn="assistant"]') as HTMLElement;
    expect(await within(card).findByText('It didn’t go out.')).toHaveAttribute('role', 'status');
  });

  it('asks before turning Ask first off', async () => {
    renderPane();
    const chip = await screen.findByRole('button', { name: 'Ask first' });
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(chip);
    const confirm = await screen.findByRole('button', { name: 'Turn off Ask first' });
    expect(server.rule).toEqual([]);
    await act(async () => {
      fireEvent.click(confirm);
    });
    await waitFor(() => expect(server.rule).toEqual([{ workflow_id: 'w1', ask_first: false, expected_revision: 0 }]));
  });
});
