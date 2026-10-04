/**
 * The run store's ordering rules: frames fold in once per animation frame,
 * by each run's `seq`; a duplicate changes nothing; a gap, an unknown run
 * already under way or a new server epoch ask for a fresh snapshot and hold
 * what arrives meanwhile; a snapshot names the runs that ended unseen; a
 * run's end folds in across a gap and wins over any live reading; and a
 * resync never asks again for what its snapshot could not settle.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { snapshotFromWire, type RunSnapshot } from '@/lib/agui/reduceRun';
import { resetChatRunStore, useChatRunStore } from '../chatRunStore';

function frame(seq: number, suffix: string, data: Record<string, unknown> = {}, runId = 'r1', hubEpoch = 'e1') {
  return {
    specversion: '1.0',
    id: `${runId}:${seq}`,
    source: 'opencompany://services/chat',
    type: `com.opencompany.chat.run.${suffix}`,
    subject: runId,
    data: { workflow_id: 'w1', session_id: 'w1', run_id: runId, seq, hub_epoch: hubEpoch, ...data },
  };
}

function receive(...frames: unknown[]) {
  const store = useChatRunStore.getState();
  for (const item of frames) store.receive(item);
  store.flush();
}

function session() {
  return useChatRunStore.getState().sessions.w1;
}

function snapshot(runId: string, state: string, seq: number, hubEpoch = 'e1'): RunSnapshot {
  return snapshotFromWire({ run_id: runId, session_id: 'w1', state, seq, hub_epoch: hubEpoch })!;
}

beforeEach(() => resetChatRunStore());

describe('chatRunStore', () => {
  it('folds a run in order and ignores a frame it has seen', () => {
    receive(frame(1, 'started', { kind: 'message' }), frame(2, 'text.content', { message_id: 's1', delta: 'Hi' }));
    receive(frame(2, 'text.content', { message_id: 's1', delta: 'Hi' }));
    expect(session().runs.r1).toMatchObject({ state: 'running', seq: 2, segments: [{ messageId: 's1', text: 'Hi' }] });
    expect(session().syncing).toBe(false);
  });

  it('asks for a snapshot on a gap and folds the held frames after it', () => {
    receive(frame(1, 'started'));
    const before = session().resync;
    receive(frame(3, 'text.content', { message_id: 's1', delta: ' there' }));
    expect(session()).toMatchObject({ syncing: true, resync: before + 1 });
    // More frames while the snapshot is on its way are held, not asked for again.
    receive(frame(4, 'text.ended', { message_id: 's1', final: true }));
    expect(session().resync).toBe(before + 1);

    const fresh = { ...snapshot('r1', 'running', 2), segments: [{ messageId: 's1', text: 'Hi', final: null }] };
    useChatRunStore.getState().applySubscription('w1', 'e1', [fresh]);
    expect(session()).toMatchObject({ syncing: false, subscribed: true });
    expect(session().runs.r1).toMatchObject({ seq: 4, segments: [{ messageId: 's1', text: 'Hi there', final: true }] });
  });

  it('asks for a snapshot for a run it joins half way, and for a new server', () => {
    receive(frame(5, 'step.started', { step_id: 'a', step_name: 'Checked' }));
    expect(session()).toMatchObject({ syncing: true, resync: 1 });

    resetChatRunStore();
    useChatRunStore.getState().applySubscription('w1', 'e1', []);
    receive(frame(1, 'started', {}, 'r2', 'e2'));
    expect(session()).toMatchObject({ syncing: true, resync: 1 });
  });

  it('names the live runs a snapshot no longer has', () => {
    useChatRunStore.getState().admit('w1', { runId: 'r1', userMessageId: 'm1', state: 'pending' });
    useChatRunStore.getState().applySubscription('w1', 'e1', [snapshot('r2', 'running', 1)]);
    expect(session().stale).toEqual(['r1']);
    useChatRunStore.getState().upsertRun({ ...snapshot('r1', 'finished', 6) });
    expect(session().stale).toEqual([]);
    expect(session().runs.r1.state).toBe('finished');
  });

  it('keeps the newer of two readings of one run', () => {
    receive(frame(1, 'started'), frame(2, 'step.started', { step_id: 'a', step_name: 'Checked' }));
    useChatRunStore.getState().applySubscription('w1', 'e1', [snapshot('r1', 'running', 1)]);
    expect(session().runs.r1.seq).toBe(2);
  });

  it('registers the run a send admitted, once', () => {
    const store = useChatRunStore.getState();
    store.admit('w1', { runId: 'r1', userMessageId: 'm1', state: 'queued' });
    expect(session().runs.r1).toMatchObject({ state: 'queued', userMessageId: 'm1' });
    receive(frame(1, 'started'));
    store.admit('w1', { runId: 'r1', userMessageId: 'm1', state: 'pending' });
    expect(session().runs.r1.state).toBe('running');
  });

  it('folds the end of a run in across a gap, and nothing after it', () => {
    receive(frame(1, 'started'), frame(7, 'finished', { outcome: { type: 'success' } }));
    expect(session().runs.r1).toMatchObject({ state: 'finished', seq: 7 });
    expect(session().syncing).toBe(false);
    receive(frame(8, 'text.content', { message_id: 's1', delta: 'late' }));
    expect(session().runs.r1.segments).toEqual([]);
    // A run never seen before that ends is recorded as ended.
    receive(frame(4, 'failed', { message: 'Calendar said no', code: 'run_failed' }, 'r2'));
    expect(session().runs.r2).toMatchObject({ state: 'error', seq: 4 });
  });

  it('takes an end the server stored over a live reading, whatever its seq', () => {
    receive(frame(1, 'started'));
    // The hub forgets a run's seq once it ends: its stored end reads 0.
    useChatRunStore.getState().upsertRun(snapshot('r1', 'finished', 0));
    expect(session().runs.r1.state).toBe('finished');
    useChatRunStore.getState().applySubscription('w1', 'e1', [snapshot('r1', 'running', 3)]);
    expect(session().runs.r1.state).toBe('finished');
  });

  it('drops a held frame its snapshot cannot settle instead of asking again', () => {
    receive(frame(1, 'started'), frame(3, 'text.content', { message_id: 's1', delta: 'x' }));
    const asked = session().resync;
    // The run ended meanwhile, so the snapshot no longer lists it: it is read
    // on its own (stale), and the held frame is not asked for again.
    useChatRunStore.getState().applySubscription('w1', 'e1', []);
    expect(session()).toMatchObject({ syncing: false, resync: asked, stale: ['r1'] });
    receive(frame(4, 'text.content', { message_id: 's1', delta: 'y' }, 'r9'));
    expect(session().resync).toBe(asked + 1);
  });

  it('asks for a snapshot when the hub dropped frames', () => {
    receive(frame(1, 'started'));
    receive({
      specversion: '1.0',
      id: 'resync',
      source: 'opencompany://services/chat',
      type: 'com.opencompany.chat.run.custom',
      data: { session_id: 'w1', hub_epoch: 'e1', name: 'opencompany.resync' },
    });
    expect(session().syncing).toBe(true);
  });
});
