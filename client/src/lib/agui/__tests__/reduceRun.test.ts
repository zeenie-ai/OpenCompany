/**
 * A chat run folded from its events: the handoff's Saturday booking run
 * (steps, streamed text, a generated interface patch by patch, and the card
 * of a draft waiting for the owner), duplicates, and the snapshots the
 * server sends.
 */

import { describe, expect, it } from 'vitest';
import fixture from '@/features/chat/__fixtures__/saturday-booking.events.json';
import booking from '@/features/chat/__fixtures__/saturday-booking.spec.json';
import { parseRunFrame, type RunEvent } from '../events';
import { applyRunEvent, emptyRun, isLiveRun, replayRun, snapshotFromWire } from '../reduceRun';

const frames = (fixture as { data: unknown }[]).map((item) => parseRunFrame(item.data));

function eventsOf(runId: string): RunEvent[] {
  return frames.filter((frame): frame is RunEvent => frame !== null && frame.type !== 'resync' && frame.runId === runId);
}

function envelope(suffix: string, data: Record<string, unknown>, seq = 1) {
  return {
    specversion: '1.0',
    id: `r1:${seq}`,
    source: 'opencompany://services/chat',
    type: `com.opencompany.chat.run.${suffix}`,
    subject: 'r1',
    data: { workflow_id: 'w1', session_id: 'w1', run_id: 'r1', seq, hub_epoch: 'e1', ...data },
  };
}

describe('parseRunFrame', () => {
  it('reads every event of the handoff run', () => {
    expect(frames.every((frame) => frame !== null)).toBe(true);
    expect(frames[0]).toMatchObject({ type: 'started', runId: 'r_8f2a1c', sessionId: 'wf_salon', seq: 1, hubEpoch: 'e_fixture' });
  });

  it('refuses frames it cannot trust', () => {
    expect(parseRunFrame(envelope('started', {}))).not.toBeNull();
    expect(parseRunFrame({ ...envelope('started', {}), source: 'opencompany://elsewhere' })).toBeNull();
    expect(parseRunFrame({ ...envelope('started', {}), type: 'com.opencompany.chat.updated' })).toBeNull();
    expect(parseRunFrame({ ...envelope('started', {}), specversion: '0.3' })).toBeNull();
    expect(parseRunFrame(envelope('started', { hub_epoch: undefined }))).toBeNull();
    expect(parseRunFrame(envelope('started', { seq: 0 }, 0))).toBeNull();
    expect(parseRunFrame(envelope('nonsense', {}))).toBeNull();
  });
});

describe('applyRunEvent', () => {
  it('folds the handoff run: steps, the reply text, and the card of its draft', () => {
    const run = replayRun('r_8f2a1c', 'wf_salon', eventsOf('r_8f2a1c'));
    expect(run).toMatchObject({
      state: 'finished',
      kind: 'message',
      userMessageId: 'm_owner_1',
      replyMessageId: 'a_r_8f2a1c',
      seq: 26,
      outcome: { type: 'success' },
    });
    expect(run.activities.map((activity) => activity.activityType)).toEqual(['json_render', 'approval']);
    expect(run.steps.map((step) => [step.name, step.state, step.durationMs])).toEqual([
      ['Checked Google Calendar', 'done', 640],
      ['Read the WhatsApp thread with Priya', 'done', 640],
      ['Compared stylist availability', 'done', 640],
    ]);
    expect(run.segments).toEqual([
      { messageId: 'r_8f2a1c.1.1', text: 'Saturday is fairly full, but there’s a clean 2h 15m gap with Ana from 2:30pm.', final: true },
    ]);
    expect(isLiveRun(run)).toBe(false);
  });

  it('builds the interface the run streamed, patch by patch', () => {
    const run = replayRun('r_8f2a1c', 'wf_salon', eventsOf('r_8f2a1c'));
    const ui = run.activities.find((activity) => activity.activityType === 'json_render');
    expect(ui).toMatchObject({ messageId: 'p_ui_1', patches: 13 });
    expect(ui?.content).toEqual({
      root: booking.root,
      state: booking.state,
      elements: booking.elements,
    });
  });

  it('keeps an activity a snapshot does not replace', () => {
    const base = applyRunEvent(
      emptyRun('r1', 'w1'),
      parseRunFrame(envelope('activity.snapshot', { message_id: 'p', activity_type: 'json_render', content: { root: 'a' } })) as RunEvent,
    );
    const kept = applyRunEvent(
      base,
      parseRunFrame(envelope('activity.snapshot', { message_id: 'p', activity_type: 'json_render', content: {}, replace: false }, 2)) as RunEvent,
    );
    expect(kept.activities).toEqual([{ messageId: 'p', activityType: 'json_render', content: { root: 'a' }, patches: 0 }]);
  });

  it('reads activity events and refuses malformed ones', () => {
    expect(parseRunFrame(envelope('activity.delta', { message_id: 'p', activity_type: 'json_render', patch: [{ op: 'add', path: '/root', value: 'r' }] }))).toMatchObject({
      type: 'activity.delta',
      messageId: 'p',
      patch: [{ op: 'add', path: '/root', value: 'r' }],
    });
    expect(parseRunFrame(envelope('activity.delta', { message_id: 'p', activity_type: 'json_render', patch: 'nope' }))).toBeNull();
    expect(parseRunFrame(envelope('activity.snapshot', { activity_type: 'json_render', content: {} }))).toBeNull();
  });

  it('changes nothing for an event it has already seen', () => {
    const started = parseRunFrame(envelope('started', { kind: 'message' })) as RunEvent;
    const once = applyRunEvent(emptyRun('r1', 'w1'), started);
    expect(applyRunEvent(once, started)).toBe(once);
  });

  it('ends stopped when the run was stopped, and failed with the error', () => {
    const base = applyRunEvent(emptyRun('r1', 'w1'), parseRunFrame(envelope('started', {})) as RunEvent);
    const stopped = applyRunEvent(base, parseRunFrame(envelope('finished', { outcome: { type: 'stopped' } }, 2)) as RunEvent);
    expect(stopped.state).toBe('stopped');
    const failed = applyRunEvent(base, parseRunFrame(envelope('failed', { message: 'Calendar said no', code: 'run_failed', hint: 'Reconnect Google' }, 2)) as RunEvent);
    expect(failed).toMatchObject({ state: 'error', error: { message: 'Calendar said no', code: 'run_failed', hint: 'Reconnect Google' } });
  });
});

describe('snapshotFromWire', () => {
  it('reads a run as the server sends it, the hint beside its error', () => {
    const run = snapshotFromWire({
      run_id: 'r1',
      session_id: 'w1',
      state: 'error',
      seq: 4,
      hub_epoch: 'e1',
      error: 'Calendar said no',
      error_code: 'run_failed',
      result: { hint: 'Reconnect Google' },
      steps: [{ step_id: 's1', name: 'Checked Google Calendar', state: 'failed' }],
    });
    expect(run).toMatchObject({
      runId: 'r1',
      state: 'error',
      seq: 4,
      error: { message: 'Calendar said no', code: 'run_failed', hint: 'Reconnect Google' },
      steps: [{ stepId: 's1', name: 'Checked Google Calendar', state: 'failed' }],
    });
  });

  it('names no run without an id and a session', () => {
    expect(snapshotFromWire({ run_id: 'r1' })).toBeNull();
    expect(snapshotFromWire('r1')).toBeNull();
  });
});
