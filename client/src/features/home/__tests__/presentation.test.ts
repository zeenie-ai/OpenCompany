/**
 * The presentation table: server state in, pill and primary button out.
 * It only picks labels and colours; every rule it reads is the server's.
 */

import { describe, expect, it } from 'vitest';
import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { busyLabelFor, initialOf, presentEmployee, primaryActionLabel } from '../data/presentation';
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
    ['working', { state: 'running' }, 'Working', 'working', 'pause'],
    ['paused', { state: 'paused' }, 'Paused', 'paused', 'resume'],
    ['attention', { state: 'paused' }, 'Needs attention', 'attention', 'resume'],
    ['attention', { state: 'failed', can_resume: false }, 'Needs attention', 'attention', 'open_workflow'],
    ['ready', { state: 'never_started' }, 'Ready', 'ready', 'start'],
  ])('%s (%o) shows %s and offers %s', (status, control, label, tone, action) => {
    const view = presentEmployee(employee({ status }, control));
    expect(view.pill).toEqual({ label, tone });
    expect(view.primary.kind).toBe(action);
    expect(view.pulse).toBe(status === 'working');
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
    expect(view.busyLabel).toBe('Pausing…');
    expect(busyLabelFor('start')).toBe('Starting…');
  });

  it('never offers a button the control plane refuses', () => {
    const view = presentEmployee(employee({ status: 'working' }, { state: 'running', can_pause: false }));
    expect(view.primary.kind).toBe('open_workflow');
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

  it('takes the first letter of a name for the avatar', () => {
    expect(initialOf('  maya')).toBe('M');
    expect(initialOf('')).toBe('?');
    expect(initialOf('Émile')).toBe('É');
  });
});
