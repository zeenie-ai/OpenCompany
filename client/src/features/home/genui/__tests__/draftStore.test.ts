/**
 * The draft's request rules: every request has its own token, a reply for
 * an old token changes nothing, a change request carries the job and the
 * earlier replies, Cancel hands the owner's words back, the screen never
 * keeps a trigger the resolved apps cannot start, and one hire is in flight
 * at a time with a key that repeats for the same payload until it hires.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { SPIKE, orbState } from '../../orb/orb';
import corpus from '../__fixtures__/replies.json';
import { STATE_PATHS } from '../catalog';
import {
  HISTORY_TURNS,
  SETUP_TIMEOUT_MS,
  beginHire,
  cancelDraft,
  discardDraft,
  endHire,
  resetDraftForTests,
  retryDraft,
  setRefining,
  submitDraft,
  triggerAppNames,
  useDraftStore,
} from '../draftStore';
import { getPath } from '../expressions';

const GOOD_REPLY = (corpus as unknown as { name: string; reply: string }[]).find((c) => c.name === 'clean minified reply')!.reply;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

type Send = (type: string, data?: Record<string, unknown>, timeoutMs?: number) => Promise<unknown>;

afterEach(() => resetDraftForTests());

describe('submitDraft', () => {
  it('keeps only plain responsibilities in the optional team preview', async () => {
    const send = vi.fn<Send>().mockResolvedValue({
      success: true, reply: GOOD_REPLY,
      team: [{ responsibility: 'Checks your calendar', node_type: 'productivity_agent' }, { responsibility: '' }, null],
    });
    await submitDraft(send, 'Book my appointments');
    expect(useDraftStore.getState().team).toEqual([{ responsibility: 'Checks your calendar' }]);
    discardDraft(send);
    expect(useDraftStore.getState().team).toEqual([]);
  });

  it('sends the job with a fresh draft token and shows the reply', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: GOOD_REPLY, provider: 'openai', model: 'gpt-x' });
    await submitDraft(send, '  Answer my WhatsApp  ');
    const [type, payload, timeout] = send.mock.calls[0];
    expect(type).toBe('generate_employee_setup');
    expect(payload).toMatchObject({ job: 'Answer my WhatsApp' });
    expect(typeof payload?.draft_token).toBe('string');
    expect(payload).not.toHaveProperty('request_id');
    expect(timeout).toBe(SETUP_TIMEOUT_MS);
    const state = useDraftStore.getState();
    expect(state.status).toBe('ready');
    expect(state.intro).toBe('Meet Maya, your new receptionist.');
    expect(state.spec).not.toBeNull();
    expect(state.uiState).toMatchObject({ rules: { askFirst: true } });
    expect(state.source).toEqual({ provider: 'openai', model: 'gpt-x' });
    expect(state.turns).toEqual([{ change: null, reply: GOOD_REPLY }]);
  });

  it('ignores empty text and a second send while one is in flight', async () => {
    const pending = deferred<unknown>();
    const send = vi.fn<Send>().mockReturnValue(pending.promise);
    expect(await submitDraft(send, '   ')).toBe(false);
    const first = submitDraft(send, 'job one');
    expect(await submitDraft(send, 'job two')).toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
    pending.resolve({ success: true, reply: GOOD_REPLY });
    await first;
  });

  it('drops a reply that arrives after Discard, and asks the server to cancel', async () => {
    const pending = deferred<unknown>();
    const send = vi.fn<Send>((type) => (type === 'generate_employee_setup' ? pending.promise : Promise.resolve({})));
    const running = submitDraft(send, 'a job');
    const token = useDraftStore.getState().token;
    discardDraft(send);
    expect(send).toHaveBeenCalledWith('cancel_employee_setup', { draft_token: token });
    pending.resolve({ success: true, reply: GOOD_REPLY });
    await running;
    expect(useDraftStore.getState().status).toBe('idle');
    expect(useDraftStore.getState().spec).toBeNull();
  });

  it('sends a change with the job and the latest replies, and keeps the draft on failure', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: GOOD_REPLY });
    await submitDraft(send, 'the job');
    for (let i = 0; i < 4; i++) {
      setRefining(true);
      await submitDraft(send, `change ${i}`);
    }
    expect(useDraftStore.getState().turns).toHaveLength(5);

    send.mockResolvedValueOnce({ success: false, error: 'timeout' });
    setRefining(true);
    await submitDraft(send, 'one more');
    const last = send.mock.calls.at(-1)![1]!;
    expect(last.job).toBe('the job');
    expect(last.refine).toBe('one more');
    expect(last.history).toHaveLength(HISTORY_TURNS);
    const state = useDraftStore.getState();
    expect(state.status).toBe('failed');
    expect(state.failure).toEqual({ code: 'timeout', text: 'one more', refine: true });
    expect(state.spec).not.toBeNull();
    expect(state.turns).toHaveLength(5);
  });

  it('retries a failed change as a change', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: GOOD_REPLY });
    await submitDraft(send, 'the job');
    send.mockRejectedValueOnce(new Error('WebSocket closed'));
    setRefining(true);
    await submitDraft(send, 'weekdays only');
    expect(useDraftStore.getState().failure?.code).toBe('connection');
    await retryDraft(send);
    expect(send.mock.calls.at(-1)![1]).toMatchObject({ refine: 'weekdays only' });
    expect(useDraftStore.getState().status).toBe('ready');
    expect(useDraftStore.getState().turns.at(-1)).toEqual({ change: 'weekdays only', reply: GOOD_REPLY });
  });

  it('counts an unreadable reply as a failure, and a timeout as a timeout', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: 'Sorry, no.' });
    await submitDraft(send, 'job');
    expect(useDraftStore.getState().failure?.code).toBe('unparseable');
    send.mockRejectedValueOnce(new Error('Request timeout: generate_employee_setup'));
    await submitDraft(send, 'job');
    expect(useDraftStore.getState().failure?.code).toBe('timeout');
  });

  it('maps unknown server errors to provider_error', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: false, error: 'mystery' });
    await submitDraft(send, 'job');
    expect(useDraftStore.getState().failure?.code).toBe('provider_error');
  });

  it('only edits a draft that exists', () => {
    setRefining(true);
    expect(useDraftStore.getState().refining).toBe(false);
  });

  it('spikes the orb when a setup is ready, and less when it fails', async () => {
    orbState.spike = 0;
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: GOOD_REPLY });
    await submitDraft(send, 'job');
    expect(orbState.spike).toBe(SPIKE.draftReady);
    orbState.spike = 0;
    send.mockResolvedValueOnce({ success: false, error: 'timeout' });
    await submitDraft(send, 'job');
    expect(orbState.spike).toBe(SPIKE.draftFailed);
    orbState.spike = 0;
  });
});

describe('cancelDraft', () => {
  it('discard keeps an outstanding hire owned until its response arrives', () => {
    const key = beginHire('job');
    const version = useDraftStore.getState().version;
    discardDraft(vi.fn().mockResolvedValue({}));
    expect(useDraftStore.getState().hiring).toBe(true);
    expect(useDraftStore.getState().hireKey?.key).toBe(key);
    expect(useDraftStore.getState().version).toBeGreaterThan(version);
    expect(beginHire('another job')).toBeNull();
    endHire(true);
    expect(beginHire('another job')).not.toBeNull();
  });
  it('stops a new job and puts its words back in the composer', async () => {
    const pending = deferred<unknown>();
    const send = vi.fn<Send>((type) => (type === 'generate_employee_setup' ? pending.promise : Promise.resolve({})));
    const running = submitDraft(send, 'Answer my WhatsApp');
    const token = useDraftStore.getState().token;
    cancelDraft(send);
    expect(send).toHaveBeenCalledWith('cancel_employee_setup', { draft_token: token });
    expect(useDraftStore.getState()).toMatchObject({ status: 'idle', input: 'Answer my WhatsApp', token: null });
    pending.resolve({ success: true, reply: GOOD_REPLY });
    await running;
    expect(useDraftStore.getState().spec).toBeNull();
  });

  it('stops a change and keeps the last version on screen', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: GOOD_REPLY });
    await submitDraft(send, 'the job');
    const pending = deferred<unknown>();
    send.mockReturnValueOnce(pending.promise);
    setRefining(true);
    const running = submitDraft(send, 'weekdays only');
    cancelDraft(send);
    expect(useDraftStore.getState()).toMatchObject({ status: 'ready', input: 'weekdays only', refining: true });
    pending.resolve({ success: false, error: 'cancelled' });
    await running;
    expect(useDraftStore.getState().status).toBe('ready');
    expect(useDraftStore.getState().spec).not.toBeNull();
  });
});

describe('the screen’s trigger', () => {
  const whatsapp = {
    app_id: 'whatsapp',
    provider_id: 'whatsapp',
    name: 'WhatsApp',
    icon_ref: null,
    connected: false,
    supported: true,
    can_trigger: true,
  };

  it('keeps an app that can start the work', async () => {
    const send = vi.fn<Send>().mockResolvedValue({ success: true, reply: GOOD_REPLY, apps: { whatsapp } });
    await submitDraft(send, 'job');
    const state = useDraftStore.getState();
    expect(getPath(state.uiState, STATE_PATHS.trigger)).toEqual({ kind: 'app_event', app: 'WhatsApp' });
    expect(triggerAppNames(state.apps)).toEqual({ whatsapp: 'WhatsApp' });
  });

  it('falls back to the owner messaging them when the app cannot', async () => {
    const send = vi
      .fn<Send>()
      .mockResolvedValue({ success: true, reply: GOOD_REPLY, apps: { whatsapp: { ...whatsapp, can_trigger: false } } });
    await submitDraft(send, 'job');
    const state = useDraftStore.getState();
    expect(getPath(state.uiState, STATE_PATHS.trigger)).toEqual({ kind: 'manual' });
    // What the model wrote stays the screen's starting point.
    expect(getPath(state.spec!.state, STATE_PATHS.trigger)).toEqual({ kind: 'app_event', app: 'WhatsApp' });
  });
});

describe('beginHire', () => {
  it('allows one hire at a time and repeats the key for the same payload', () => {
    const key = beginHire('payload-a');
    expect(key).toBeTruthy();
    expect(beginHire('payload-a')).toBeNull();
    endHire();
    expect(beginHire('payload-a')).toBe(key);
    endHire();
    expect(beginHire('payload-b')).not.toBe(key);
  });

  it('forgets the key once it hired someone, so the same setup hires anew', () => {
    const key = beginHire('payload-a');
    endHire(true);
    expect(beginHire('payload-a')).not.toBe(key);
  });

  it('blocks a new draft while a hire is in flight', async () => {
    beginHire('x');
    const send = vi.fn<Send>();
    expect(await submitDraft(send, 'another job')).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });
});
