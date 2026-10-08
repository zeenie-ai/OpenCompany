/**
 * The presentation table: server state in, pill and primary button out,
 * and what the message box does. It only picks labels and colours; every
 * rule it reads is the server's.
 */

import { describe, expect, it } from 'vitest';
import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import {
  busyLabelFor,
  firstDayNote,
  firstDayPhase,
  initialOf,
  presentEmployee,
  primaryActionLabel,
  restartDraftsWarning,
  talkMode,
  talkNoticeText,
} from '../data/presentation';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';

function employee(patch: Record<string, unknown>, control: Record<string, unknown> = {}): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w',
    name: 'Maya',
    control: normalizeWorkflowControlStatus({ state: 'never_started', ...control }, 'w'),
    ...patch,
  })!;
}

const whatsapp = { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: false, supported: true };

describe('presentEmployee', () => {
  it.each([
    ['working', { state: 'running' }, true, 'Working', 'working', 'pause'],
    ['working', { state: 'running' }, false, 'Ready', 'ready', 'pause'],
    ['paused', { state: 'paused' }, false, 'Stopped', 'paused', 'resume'],
    ['attention', { state: 'paused' }, false, 'Needs attention', 'attention', 'resume'],
    ['attention', { state: 'failed', can_resume: false }, false, 'Needs attention', 'attention', 'start'],
    ['attention', { state: 'pausing', can_resume: false }, false, 'Needs attention', 'attention', 'open_workflow'],
    ['ready', { state: 'never_started' }, false, 'Not started', 'paused', 'start'],
  ])('%s (%o, answering: %s) shows %s and offers %s', (status, control, answering, label, tone, action) => {
    const view = presentEmployee(employee({ status }, control), undefined, answering);
    expect(view.pill).toEqual({ label, tone });
    expect(view.primary.kind).toBe(action);
    // Only Working pulses, and only while they answer.
    expect(view.pulse).toBe(tone === 'working');
  });

  it('says Starting… from the Start press until they run, and Resuming… on the way back', () => {
    expect(presentEmployee(employee({ status: 'ready' }), { action: 'start', state: 'starting' }).pill).toEqual({
      label: 'Starting…',
      tone: 'ready',
    });
    expect(presentEmployee(employee({ status: 'working' }, { state: 'starting' }), undefined, true).pill).toEqual({
      label: 'Starting…',
      tone: 'ready',
    });
    expect(presentEmployee(employee({ status: 'working' }, { state: 'resuming' })).pill).toEqual({
      label: 'Resuming…',
      tone: 'ready',
    });
  });

  // start_employee resets a failed employee before starting it again.
  it('offers Start again after a failure', () => {
    const view = presentEmployee(employee({ status: 'attention' }, { state: 'failed', can_resume: false }));
    expect(view.primary).toEqual({ kind: 'start', again: true });
    expect(primaryActionLabel(view.primary)).toBe('Start again');
  });

  it('asks for what a failed employee is missing before starting it again', () => {
    const view = presentEmployee(employee({ status: 'attention', missing_apps: [whatsapp] }, { state: 'failed', can_resume: false }));
    expect(view.primary.kind).toBe('connect_app');
  });

  it('asks to connect the first missing app before anything else', () => {
    const view = presentEmployee(employee({ status: 'ready', missing_apps: [whatsapp], needs_ai: true }));
    expect(view.primary).toEqual({ kind: 'connect_app', app: expect.objectContaining({ name: 'WhatsApp' }) });
    expect(primaryActionLabel(view.primary)).toBe('Connect WhatsApp');
  });

  it('then asks for an AI model', () => {
    const view = presentEmployee(employee({ status: 'ready', needs_ai: true }));
    expect(view.primary.kind).toBe('connect_ai');
    expect(primaryActionLabel(view.primary)).toBe('Connect an AI model');
  });

  it('says Needs you while drafts wait, whatever the status', () => {
    expect(presentEmployee(employee({ status: 'working', pending_approvals: 2 }, { state: 'running' })).pill).toEqual({
      label: 'Needs you',
      tone: 'waiting',
    });
  });

  it('says Needs you while the agent waits in the browser', () => {
    const request = { node_id: 'w:browser:1', reason: 'login', since: null };
    expect(presentEmployee(employee({ status: 'working', browser_request: request }, { state: 'running' })).pill).toEqual({
      label: 'Needs you',
      tone: 'waiting',
    });
    expect(employee({ status: 'working' }).browser_request).toBeNull();
  });

  it('labels the button while a change is in flight', () => {
    const view = presentEmployee(employee({ status: 'working' }, { state: 'running' }), { action: 'pause', state: 'pausing' });
    expect(view.busyLabel).toBe('Stopping…');
    expect(busyLabelFor('start')).toBe('Starting…');
  });

  it('never offers a button the control plane refuses', () => {
    const view = presentEmployee(employee({ status: 'working' }, { state: 'running', can_pause: false }));
    expect(view.primary.kind).toBe('open_workflow');
    expect(primaryActionLabel(view.primary)).toBe('Open in Dev mode');
  });
});

describe('the message box', () => {
  it.each([
    ['running', 'send'],
    ['starting', 'send'],
    ['resuming', 'send'],
    ['paused', 'queue'],
    ['pausing', 'queue'],
    ['never_started', 'start'],
    ['ready', 'start'],
    ['resetting', 'start'],
    ['failed', 'start'],
  ])('while %s a message is %s', (state, mode) => {
    expect(talkMode(normalizeWorkflowControlStatus({ state }, 'w'))).toBe(mode);
  });

  it('explains why a message waits or cannot go', () => {
    expect(talkNoticeText('send', 'Maya')).toBeNull();
    expect(talkNoticeText('queue', 'Maya')).toBe('Maya is paused. They’ll read your message when you resume them.');
    expect(talkNoticeText('queue', 'Maya', true)).toBe('Your message is waiting. Maya will read it when you resume them.');
    expect(talkNoticeText('start', 'Maya')).toBe('Maya isn’t running, so they can’t read messages right now.');
  });

  it('warns that a restart throws the waiting drafts away', () => {
    expect(restartDraftsWarning('Maya', 1)).toBe('Maya has 1 draft waiting for you. Restarting throws it away, so check it first.');
    expect(restartDraftsWarning('Maya', 3)).toBe('Maya has 3 drafts waiting for you. Restarting throws them away, so check them first.');
  });
});

describe('a new hire’s first day', () => {
  it.each([
    [{ state: 'running' }, {}, true, 'ready'],
    [{ state: 'starting' }, {}, true, 'starting'],
    // Hired and started by the hire: no control row yet.
    [{ state: 'never_started' }, { activation_state: 'saved' }, true, 'starting'],
    // The start was held for something to connect, or failed.
    [{ state: 'never_started' }, { activation_state: 'blocked' }, true, 'stopped'],
    [{ state: 'never_started' }, { activation_state: 'failed' }, true, 'stopped'],
    // The hire did not start them (something was missing).
    [{ state: 'never_started' }, { activation_state: 'saved' }, false, 'stopped'],
    [{ state: 'failed' }, {}, true, 'stopped'],
  ])('control %o with %o (started: %s) is %s', (control, patch, started, phase) => {
    expect(firstDayPhase(employee(patch, control), started)).toBe(phase);
  });

  it('says why they are not running', () => {
    expect(firstDayNote(employee({ missing_apps: [whatsapp] }))).toBe('WhatsApp isn’t connected yet.');
    expect(firstDayNote(employee({ needs_ai: true }))).toBe('Connect an AI model first.');
    expect(firstDayNote(employee({ activation_state: 'failed' }))).toBe('Maya couldn’t start.');
    expect(firstDayNote(employee({}, { state: 'failed' }))).toBe('Maya couldn’t start.');
    expect(firstDayNote(employee({}))).toBe('Maya isn’t running.');
  });
});

describe('parseEmployee', () => {
  it('drops an entry without its identity and repairs odd fields', () => {
    expect(parseEmployee({ name: 'No id' })).toBeNull();
    const parsed = parseEmployee({ workflow_id: 'w', name: 'X', status: 'exploded', done_today: -3, apps: 'nope' })!;
    expect(parsed.status).toBe('ready');
    expect(parsed.done_today).toBe(0);
    expect(parsed.apps).toEqual([]);
  });

  it('reads talk, ask-first and waiting changes, and repairs them on their own', () => {
    const parsed = parseEmployee({
      workflow_id: 'w',
      name: 'X',
      talk: { state: 'on', agent_node_id: 'w:talk' },
      asks_first: true,
      pending_changes: true,
    })!;
    expect(parsed.talk).toEqual({ state: 'on', agent_node_id: 'w:talk' });
    expect(parsed.asks_first).toBe(true);
    expect(parsed.pending_changes).toBe(true);

    const odd = parseEmployee({ workflow_id: 'w', name: 'X', talk: { state: 'chatty', agent_node_id: 7 }, pending_changes: 'yes' })!;
    expect(odd.talk).toEqual({ state: 'unsupported', agent_node_id: null });
    expect(odd.pending_changes).toBe(false);
    // An older server sends none of them.
    expect(parseEmployee({ workflow_id: 'w', name: 'X' })).toMatchObject({
      talk: { state: 'unsupported', agent_node_id: null },
      asks_first: false,
      pending_changes: false,
    });
  });

  it('takes the first letter of a name for the avatar', () => {
    expect(initialOf('  maya')).toBe('M');
    expect(initialOf('')).toBe('?');
    expect(initialOf('Émile')).toBe('É');
  });
});
