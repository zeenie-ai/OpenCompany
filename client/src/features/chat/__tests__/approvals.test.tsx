/**
 * Drafts waiting for the owner in the chat, against a fake server: a card on
 * the reply that made it (and the employee's own drafts after the
 * conversation), Send with Undo while it can, an edit sent with Ctrl+Enter,
 * Ctrl+Enter anywhere for the newest draft, and the Ask first chip, which
 * asks before turning off; where the employee asks by mode, the Ask first
 * menu and its list of what is allowed without asking. The card's copy
 * says what really happened; a call that broke off midway offers Dismiss,
 * never Send again.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { resetChatRunStore } from '@/stores/chatRunStore';
import { useWorkflowControlStore } from '@/stores/workflowControlStore';
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
    expect(paused.footer).toEqual({ kind: 'waiting', text: 'Sends when you resume Maya.', undo: false });
  });

  it('a held draft waits for Resume too, and can still be undone', () => {
    for (const state of ['paused', 'pausing', 'resuming']) {
      const paused = view({ status: 'approved', undo_until: '2026-10-04T08:59:59Z', deployment_state: state });
      expect(paused.footer).toEqual({ kind: 'waiting', text: 'Sends when you resume Maya.', undo: true });
    }
    expect(view({ status: 'approved', undo_until: '2026-10-04T08:59:59Z', deployment_state: 'running' }).footer).toEqual({ kind: 'sending' });
  });

  it('says it went only once it went', () => {
    const sent = view({ status: 'sent', outcome: { certainty: 'sent', at: '2026-10-04T09:00:05Z' }, approved_by: 'auto' });
    expect(sent.title).toBe('Message sent');
    expect(sent.footer.kind === 'sent' && sent.footer.text).toMatch(/^Sent to Ana on WhatsApp at .+, without asking\.$/);
    const failed = view({ status: 'failed', outcome: { certainty: 'unknown', error: 'TimeoutError' } });
    expect(failed.footer).toEqual({ kind: 'failed', text: 'It may have gone out: TimeoutError', unknown: true });
    expect(view({ status: 'failed', outcome: { certainty: 'not_sent' } }).footer).toEqual({ kind: 'failed', text: 'It didn’t go out.', unknown: false });
    expect(view({ status: 'expired' }).footer).toEqual({ kind: 'ended', text: 'It waited too long, so it was not sent.' });
    expect(view({ status: 'cancelled' }).title).toBe('Draft cancelled');
  });

  it('words what happened as the server does', () => {
    const labels = { outcome_labels: { sent: 'Email sent', failed: 'Email not sent' } };
    expect(view({ ...labels, status: 'sent' }).title).toBe('Email sent');
    expect(view({ ...labels, status: 'failed' }).title).toBe('Email not sent');
  });

  it('a call that broke off says it may have happened, until dismissed', () => {
    const effect: Wire = { kind: 'effect', status: 'failed', channel: 'Shell', action: 'Run command', recipient: '', recipient_label: '', body: '', editable: false };
    expect(approval(effect).kind).toBe('effect');
    const broke = view({ ...effect, outcome: { certainty: 'unknown', error: 'WorkerShutdown' } });
    expect(broke.title).toBe('Maya tried to run command');
    expect(broke.sub).toMatch(/^Shell · at .+$/);
    expect(broke.pill).toEqual({ label: 'May have happened', tone: 'failed', pulse: false });
    expect(broke.footer).toEqual({ kind: 'effect', text: 'It broke off midway (WorkerShutdown), so it may have happened.' });
    expect(view({ ...effect, outcome: { certainty: 'unknown' } }).footer).toEqual({ kind: 'effect', text: 'It broke off midway, so it may have happened.' });
    const dismissed = view({ ...effect, status: 'cancelled' });
    expect(dismissed.title).toBe('Maya tried to run command');
    expect(dismissed.pill.label).toBe('Dismissed');
    expect(dismissed.footer).toEqual({ kind: 'ended', text: 'Dismissed.' });
    expect(optimistic(approval(effect), 'dismiss', NOW, 0)).toMatchObject({ status: 'cancelled' });
  });

  it('a declined draft can come back while its window lasts', () => {
    const gate = view({ kind: 'gate', status: 'discarded', restore_until: '2026-10-04T09:00:03Z' });
    expect(gate.footer).toEqual({ kind: 'discarded', text: 'Declined. Maya won’t send this.', note: null, restoreSeconds: 3, canRestore: true });
    const held = view({ status: 'discarded', restore_until: '2026-10-11T09:00:00Z', note: 'Ask Sam first.' });
    expect(held.footer).toMatchObject({ restoreSeconds: null, canRestore: true, note: 'Ask Sam first.' });
    expect(view({ status: 'discarded', restore_until: '2026-10-04T08:00:00Z' }).footer).toMatchObject({ canRestore: false });
  });

  it('a call with no message to show says what it does, not what it sends', () => {
    const shell: Wire = { channel: 'Shell', action: 'Use Shell', recipient: '', recipient_label: '', body: '', editable: false, generic: true };
    const asking = view({ ...shell, approval_class: 'send' });
    expect([asking.title, asking.preview]).toEqual(['Maya wants to use Shell', 'none']);
    expect(view({ ...shell, status: 'sending' }).pill.label).toBe('Running');
    const done = view({ ...shell, status: 'sent', outcome_labels: { sent: 'Done', failed: 'Not done' }, outcome: { certainty: 'sent' } });
    expect([done.title, done.pill.label, done.footer]).toEqual(['Done', 'Done', { kind: 'sent', text: 'Done.' }]);
    expect(view({ ...shell, status: 'failed', outcome: { certainty: 'unknown' } }).footer).toEqual({ kind: 'failed', text: 'It may have happened.', unknown: true });
    expect(view({ ...shell, status: 'expired' }).footer).toEqual({ kind: 'ended', text: 'It waited too long, so it did not run.' });
    expect(view({ ...shell, status: 'discarded' }).footer).toMatchObject({ text: 'Declined. Maya won’t do this.' });
    // A spec'd message keeps its preview.
    expect(view({}).preview).toBe('message');
  });

  it('a call its turn waits on gets no Undo or Restore, and runs on Resume', () => {
    const waits = approval({ waits: true });
    expect(optimistic(waits, 'send', NOW, 0)).toMatchObject({ status: 'approved', undoUntil: null });
    expect(optimistic(waits, 'discard', NOW, 0)).toMatchObject({ status: 'discarded', restoreUntil: null });
    // A retry makes it a draft again.
    expect(optimistic(approval({ waits: true, status: 'failed' }), 'retry', NOW, 0)).toMatchObject({ waits: false, undoUntil: '2026-10-04T09:00:05.000Z' });
    const paused = view({ waits: true, status: 'approved', deployment_state: 'paused' });
    expect(paused.footer).toEqual({ kind: 'waiting', text: 'Sends when you resume Maya.', undo: false });
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

let server: {
  messages: Wire[];
  activeRuns: Wire[];
  approvals: Wire[];
  /** Open rows older than the newest 100. */
  older: Wire[];
  askFirst: boolean | null;
  mode: string | null;
  revision: number;
  decisions: Wire[];
  rule: Wire[];
  allows: Wire[];
  money: Wire[];
};
let client: QueryClient;

/** The employee's generation asks by mode (RFC-0007). */
function asksByMode() {
  const control = { state: 'running', revision: 1, generation: 1, execution_control_version: 1, approval_wait_version: 1, root_execution_id: 'g1' };
  useWorkflowControlStore.getState().replaceStatuses({ w1: normalizeWorkflowControlStatus(control, 'w1') });
}

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
  useWorkflowControlStore.setState({ statuses: {}, pending: {} });
  server = {
    messages: MESSAGES,
    activeRuns: [],
    approvals: [summary()],
    older: [],
    askFirst: true,
    mode: 'outside',
    revision: 0,
    decisions: [],
    rule: [],
    allows: [],
    money: [],
  };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: server.activeRuns };
      case 'get_chat_messages':
        return { success: true, messages: server.messages, thread: { active_leaf_id: null, revision: 1 }, active_runs: server.activeRuns };
      case 'list_approvals': {
        const open = server.approvals.filter((approval) => ['pending', 'approved', 'sending', 'failed'].includes(String(approval.status)));
        const approvals = data.status === 'open' ? [...open, ...server.older] : server.approvals;
        return { success: true, approvals, server_time: new Date().toISOString() };
      }
      case 'get_ask_first':
        return { success: true, ask_first: server.askFirst, mode: server.mode, revision: server.revision };
      case 'set_ask_first':
        server.rule.push(data);
        server.mode = typeof data.mode === 'string' ? data.mode : data.ask_first ? 'outside' : 'money_only';
        server.askFirst = server.mode !== 'money_only';
        server.revision += 1;
        return { success: true, ask_first: server.askFirst, mode: server.mode, revision: server.revision, replies_gated: true, needs_apply: false };
      case 'send_chat_message':
        // While the run waits on a card, a message answers it (RFC-0007 D3).
        return { success: true, message_id: null, run_id: null, declined: { run_id: 'r1', approval_ids: ['ap1'] }, delivery: 'now' };
      case 'list_approval_allows':
        return { success: true, workflow_id: 'w1', allows: server.allows, money: server.money };
      case 'add_approval_allow': {
        const added = { id: `a${server.allows.length + 10}`, label: 'Money switch', created_at: '2026-10-04T09:00:00Z' };
        server.allows = [...server.allows, added];
        server.money = server.money.map((option) =>
          option.node_type === data.node_type && option.operation === data.operation ? { ...option, allow_id: added.id } : option,
        );
        return { success: true, allow: added };
      }
      case 'remove_approval_allow':
        server.allows = server.allows.filter((rule) => rule.id !== data.allow_id);
        server.money = server.money.map((option) => (option.allow_id === data.allow_id ? { ...option, allow_id: null } : option));
        return { success: true };
      case 'decide_approval': {
        server.decisions.push(data);
        const current = server.approvals[0];
        const next =
          data.decision === 'send'
            ? { ...current, status: 'approved', undo_until: new Date(Date.now() + 5000).toISOString(), revision: 1, ...(data.text ? { body: data.text } : {}) }
            : data.decision === 'undo'
              ? { ...current, status: 'pending', revision: 2 }
              : data.decision === 'dismiss'
                ? { ...current, status: 'cancelled', revision: 3 }
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

  it('a draft waiting for Resume can be undone', async () => {
    server.approvals = [summary({ status: 'approved', undo_until: '2026-01-01T00:00:00Z', deployment_state: 'paused', revision: 1 })];
    renderPane();
    const card = (await screen.findByText('Sends when you resume Maya.')).closest('[data-approval]') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: /^Undo$/ }));
    await waitFor(() => expect(server.decisions[0]).toMatchObject({ approval_id: 'ap1', decision: 'undo' }));
    expect(await within(card).findByRole('button', { name: /^Send$/ })).toBeInTheDocument();
  });

  it('allows a call that has no message as it is, showing what it runs with', async () => {
    server.approvals = [
      summary({
        channel: 'Custom connector',
        action: 'Use a connector tool',
        recipient: '',
        recipient_label: '',
        body: '',
        editable: false,
        details: [{ label: 'query', value: 'pricing for the Pro plan' }],
        approval_class: 'send',
      }),
    ];
    renderPane();
    const card = (await screen.findByText('Maya wants to use a connector tool')).closest('[data-approval]') as HTMLElement;
    expect(card).not.toHaveTextContent('To them');
    expect(card).not.toHaveTextContent('(no message)');
    expect(within(card).getByText('pricing for the Pro plan')).toBeInTheDocument();
    expect(within(card).getByText('Reaches out')).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: /^Allow$/ }));
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ approval_id: 'ap1', decision: 'send' });
    expect(server.decisions[0]).not.toHaveProperty('always');
  });

  it('Always allow sends the call and saves the rule the card offers', async () => {
    server.approvals = [summary({ waits: true, always: { label: 'Send a WhatsApp message to 447700900123', scope: 'recipient' } })];
    const pane = host();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ChatPane host={pane} />
      </QueryClientProvider>,
    );
    const card = (await screen.findByText('Maya wants to send a WhatsApp message')).closest('[data-approval]') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: 'Always allow Send a WhatsApp message to 447700900123' }));
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ decision: 'send', always: true });
    await waitFor(() => expect(pane.notify).toHaveBeenCalledWith('Always allowed: Send a WhatsApp message to 447700900123', 'success'));
  });

  it('declines with a note for the employee; Ctrl+Enter in the note declines', async () => {
    renderPane();
    const card = (await screen.findByText('Maya wants to send a WhatsApp message')).closest('[data-approval]') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: 'Decline' }));
    const note = within(card).getByRole('textbox', { name: 'Tell Maya what to do instead' });
    fireEvent.change(note, { target: { value: 'Call Ana instead.' } });
    fireEvent.keyDown(note, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ decision: 'discard', note: 'Call Ana instead.' });
  });

  it('a money request names itself, and Ctrl+Enter never allows it', async () => {
    server.approvals = [summary({ channel: 'Stripe', action: 'Run a Stripe command', body: '', editable: false, approval_class: 'money' })];
    asksByMode();
    renderPane();
    const card = (await screen.findByText('Maya wants to run a Stripe command')).closest('[data-approval]') as HTMLElement;
    expect(within(card).getByText('Money or deletes')).toBeInTheDocument();
    expect(within(card).getByText(/Money and deletes always ask/)).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message Maya' }), { key: 'Enter', ctrlKey: true });
    await act(async () => {});
    expect(server.decisions).toEqual([]);
  });

  it('names no list of allowed calls where the employee has none', async () => {
    server.approvals = [summary({ channel: 'Google Calendar', action: 'Delete an event', body: '', editable: false, approval_class: 'money' })];
    renderPane();
    const card = (await screen.findByText('Maya wants to delete an event')).closest('[data-approval]') as HTMLElement;
    expect(within(card).getByText('Money or deletes')).toBeInTheDocument();
    expect(within(card).queryByText(/Allowed without asking/)).not.toBeInTheDocument();
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

  it('announces a declined draft once, not its Restore countdown', async () => {
    const restore = new Date(Date.now() + 60_000).toISOString();
    server.approvals = [summary({ approval_id: 'g1', kind: 'gate', run_id: null, tool_call_id: null, status: 'discarded', restore_until: restore })];
    renderPane();
    const own = await screen.findByRole('region', { name: 'Drafts waiting for you' });
    expect(within(own).getByRole('status')).toHaveTextContent('Declined. Maya won’t send this.');
    expect(within(own).getByRole('button', { name: /^Restore/ })).toHaveAttribute('aria-live', 'off');
  });

  it('announces a send that did not go', async () => {
    server.approvals = [summary({ status: 'failed', outcome: { certainty: 'not_sent' } })];
    renderPane();
    const card = (await screen.findByText('I drafted a WhatsApp to Ana.')).closest('[data-turn="assistant"]') as HTMLElement;
    expect(await within(card).findByText('It didn’t go out.')).toHaveAttribute('role', 'status');
  });

  it('a call that broke off offers Dismiss, never Send again', async () => {
    server.approvals = [
      summary({
        kind: 'effect',
        status: 'failed',
        channel: 'Shell',
        action: 'Run command',
        recipient: '',
        recipient_label: '',
        body: '',
        editable: false,
        outcome: { certainty: 'unknown' },
      }),
    ];
    renderPane();
    const card = (await screen.findByText('Maya tried to run command')).closest('[data-approval]') as HTMLElement;
    expect(card).not.toHaveTextContent('To them');
    expect(card).not.toHaveTextContent('(no message)');
    expect(within(card).queryByRole('button', { name: /again/ })).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(server.decisions).toHaveLength(1));
    expect(server.decisions[0]).toMatchObject({ approval_id: 'ap1', decision: 'dismiss' });
    expect(await within(card).findByText('Dismissed')).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
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

describe('a call its turn waits on', () => {
  // The run waits on the owner; a restart lost what it announced live, so
  // only the card's run links them.
  const waitingRun: Wire = {
    run_id: 'r1',
    workflow_id: 'w1',
    session_id: 'w1',
    state: 'running',
    seq: 3,
    hub_epoch: 'e1',
    steps: [{ step_id: 'call_1', name: 'Send a WhatsApp message', state: 'done', detail: 'Waiting for your OK' }],
  };

  it('stays on its turn, which says it waits for the owner', async () => {
    server.messages = [row('m1', 'user', 'Tell Ana I’m late', { run_id: 'r1' })];
    server.activeRuns = [waitingRun];
    server.approvals = [summary({ waits: true })];
    renderPane();
    const card = (await screen.findByText('Maya wants to send a WhatsApp message')).closest('[data-approval]') as HTMLElement;
    const turn = card.closest('[data-turn="assistant"]') as HTMLElement;
    expect(turn).toHaveAttribute('data-run', 'r1');
    expect(screen.queryByRole('region', { name: 'Drafts waiting for you' })).not.toBeInTheDocument();
    expect(within(turn).getAllByRole('status').some((node) => node.textContent === 'Waiting for your OK')).toBe(true);
    expect(turn.querySelector('[data-thinking]')).toBeNull();
    // Its step waits too, until the card is decided.
    const step = turn.querySelector('[data-step="call_1"]') as HTMLElement;
    expect(step).toHaveAttribute('data-state', 'running');
    expect(step).toHaveTextContent('Waiting for your OK');
  });

  it('a message meanwhile answers the call instead, on its card', async () => {
    server.messages = [row('m1', 'user', 'Tell Ana I’m late', { run_id: 'r1' })];
    server.activeRuns = [waitingRun];
    server.approvals = [summary({ waits: true })];
    renderPane();
    await screen.findByText('Maya wants to send a WhatsApp message');
    const box = screen.getByRole('textbox', { name: 'Message Maya' });
    expect(box).toHaveAttribute('placeholder', 'Tell Maya what to do instead…');
    // Stop while the box is empty, Send once something is written.
    const composer = box.closest('.chat-composer') as HTMLElement;
    expect(within(composer).getByRole('button', { name: 'Stop reply' })).toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'Call her instead.' } });
    const send = within(composer).getByRole('button', { name: 'Send' });
    await waitFor(() => expect(send).toBeEnabled());
    fireEvent.click(send);
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('send_chat_message', expect.objectContaining({ message: 'Call her instead.' })));
    await act(async () => {});
    expect(screen.queryByText('Call her instead.')).not.toBeInTheDocument();
    expect(box).toHaveValue('');
  });

  it('a draft the turn does not wait on leaves the turn as it was', async () => {
    server.messages = [row('m1', 'user', 'Tell Ana I’m late', { run_id: 'r1' })];
    server.activeRuns = [waitingRun];
    renderPane();
    const card = (await screen.findByText('Maya wants to send a WhatsApp message')).closest('[data-approval]') as HTMLElement;
    const turn = card.closest('[data-turn="assistant"]') as HTMLElement;
    expect(within(turn).getAllByRole('status').some((node) => node.textContent === 'Thinking')).toBe(true);
    expect(turn.querySelector('[data-step="call_1"]')).toHaveAttribute('data-state', 'done');
    // Nothing goes while it answers.
    const box = screen.getByRole('textbox', { name: 'Message Maya' });
    const composer = box.closest('.chat-composer') as HTMLElement;
    fireEvent.change(box, { target: { value: 'Hello?' } });
    expect(within(composer).queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
    expect(within(composer).getByRole('button', { name: 'Stop reply' })).toBeInTheDocument();
  });

  it('is read however many newer cards there are', async () => {
    server.approvals = [];
    server.older = [summary({ approval_id: 'g1', kind: 'gate', run_id: null, tool_call_id: null, action: null, body: 'Yes, Saturday at 10 works.' })];
    renderPane();
    const own = await screen.findByRole('region', { name: 'Drafts waiting for you' });
    expect(within(own).getByText('Yes, Saturday at 10 works.')).toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith('list_approvals', { workflow_id: 'w1', status: 'open', limit: 100 });
  });
});

describe('Ask first by mode', () => {
  function renderByMode() {
    asksByMode();
    const pane = host();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ChatPane host={pane} />
      </QueryClientProvider>,
    );
    return pane;
  }

  it('picks the mode from a menu, and asks before asking less', async () => {
    const user = userEvent.setup();
    const pane = renderByMode();
    const menu = await screen.findByRole('button', { name: 'Ask first' });
    expect(menu).not.toHaveAttribute('aria-pressed');
    expect(menu).toHaveAttribute('title', 'Maya asks before anything outside');

    await user.click(menu);
    expect(await screen.findByRole('menuitemradio', { name: /^Before anything outside/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menuitemradio', { name: /^Before anything outside/ })).toHaveTextContent(
      'Maya asks before it messages, posts, books, deploys or changes things in your apps.',
    );
    await user.click(screen.getByRole('menuitemradio', { name: /^Before everything/ }));
    await waitFor(() => expect(server.rule).toEqual([{ workflow_id: 'w1', mode: 'everything', expected_revision: 0 }]));
    await waitFor(() => expect(pane.notify).toHaveBeenCalledWith('Maya will ask before everything', 'success'));

    // Asking less asks first.
    await user.click(screen.getByRole('button', { name: 'Ask first' }));
    await user.click(await screen.findByRole('menuitemradio', { name: /^Only for money and deletes/ }));
    const confirm = await screen.findByRole('button', { name: 'Ask only for money and deletes' });
    expect(screen.getByText('Let Maya work without asking?')).toBeInTheDocument();
    expect(server.rule).toHaveLength(1);
    await user.click(confirm);
    await waitFor(() => expect(server.rule[1]).toEqual({ workflow_id: 'w1', mode: 'money_only', expected_revision: 1 }));
    await waitFor(() => expect(pane.notify).toHaveBeenCalledWith('Maya will ask only before money and deletes', 'success'));
  });

  it('keeps asking when the owner thinks again', async () => {
    const user = userEvent.setup();
    renderByMode();
    await user.click(await screen.findByRole('button', { name: 'Ask first' }));
    await user.click(await screen.findByRole('menuitemradio', { name: /^Only for money and deletes/ }));
    await user.click(await screen.findByRole('button', { name: 'Keep asking' }));
    await act(async () => {});
    expect(server.rule).toEqual([]);
  });

  it('lists what the employee does without asking, and switches money on and off', async () => {
    server.allows = [
      { id: 'a1', label: 'Shell', created_at: '2026-10-02T10:00:00Z' },
      { id: 'a2', label: 'Stripe', created_at: '2026-10-03T10:00:00Z' },
    ];
    server.money = [
      { node_type: 'stripeAction', operation: '', label: 'Stripe', allow_id: 'a2' },
      { node_type: 'googleCalendar', operation: 'delete', label: 'Google Calendar: delete', allow_id: null },
    ];
    const user = userEvent.setup();
    renderByMode();
    await user.click(await screen.findByRole('button', { name: 'Ask first' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Allowed without asking…' }));
    const list = await screen.findByRole('dialog', { name: 'Allowed without asking' });
    // A money switch's own rule shows as its switch, not as a line.
    expect(await within(list).findByText('Shell')).toBeInTheDocument();
    expect(within(list).getByText(/added/)).toBeInTheDocument();
    expect(within(list).queryByRole('button', { name: 'Remove Stripe' })).not.toBeInTheDocument();
    expect(within(list).getByRole('switch', { name: 'Allow Stripe without asking' })).toBeChecked();
    expect(within(list).getByRole('switch', { name: 'Allow Google Calendar: delete without asking' })).not.toBeChecked();

    await user.click(within(list).getByRole('button', { name: 'Remove Shell' }));
    expect(await within(list).findByText('Nothing yet. When Maya asks, choose Always allow to add it here.')).toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith('remove_approval_allow', { workflow_id: 'w1', allow_id: 'a1' });

    await user.click(within(list).getByRole('switch', { name: 'Allow Google Calendar: delete without asking' }));
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('add_approval_allow', { workflow_id: 'w1', node_type: 'googleCalendar', operation: 'delete' }),
    );
    await waitFor(() => expect(within(list).getByRole('switch', { name: 'Allow Google Calendar: delete without asking' })).toBeChecked());

    await user.click(within(list).getByRole('switch', { name: 'Allow Stripe without asking' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('remove_approval_allow', { workflow_id: 'w1', allow_id: 'a2' }));
  });
});
