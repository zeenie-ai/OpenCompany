/**
 * What a turn says: when a message was written and the day a divider names,
 * why a run did not answer, what a working run is doing and showing, its
 * steps, and what the live work pill above the box says meanwhile.
 */

import { describe, expect, it } from 'vitest';
import { emptyRun, type RunSnapshot, type RunStep } from '@/lib/agui/reduceRun';
import { dayKey, dayLabel, timeLabel } from '../thread/timeLabel';
import { NO_APPROVALS, parseApproval, type ChatApproval, type ChatApprovals } from '../data/approvals';
import {
  approvalStep,
  failureLines,
  liveLabel,
  liveText,
  liveWorkLabel,
  liveWorkMeta,
  stepDetail,
  withApprovalSteps,
  workLabel,
  writingRate,
} from '../turns/runCopy';

describe('timeLabel', () => {
  const now = new Date(2026, 8, 28, 15, 30);
  const time = (date: Date) => date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

  it('gives the time today and says yesterday', () => {
    const morning = new Date(2026, 8, 28, 9, 5);
    expect(timeLabel(morning.toISOString(), now)).toBe(time(morning));
    const lastNight = new Date(2026, 8, 27, 22, 40);
    expect(timeLabel(lastNight.toISOString(), now)).toBe(`Yesterday, ${time(lastNight)}`);
  });

  it('names the weekday within a week, else the date', () => {
    const monday = new Date(2026, 8, 22, 10, 0);
    expect(timeLabel(monday.toISOString(), now)).toBe(`${monday.toLocaleDateString(undefined, { weekday: 'long' })}, ${time(monday)}`);
    const earlier = new Date(2026, 7, 3, 10, 0);
    expect(timeLabel(earlier.toISOString(), now)).toBe(
      `${earlier.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${time(earlier)}`,
    );
    const lastYear = new Date(2025, 11, 31, 10, 0);
    expect(timeLabel(lastYear.toISOString(), now)).toContain(lastYear.getFullYear().toString());
  });

  it('says nothing for a time it cannot read', () => {
    expect(timeLabel(null, now)).toBe('');
    expect(timeLabel('yesterday-ish', now)).toBe('');
  });
});

describe('day dividers', () => {
  const now = new Date(2026, 8, 28, 15, 30);

  it('name today, yesterday, the weekday within a week, else the date', () => {
    expect(dayLabel(new Date(2026, 8, 28, 9, 5).toISOString(), now)).toBe('Today');
    expect(dayLabel(new Date(2026, 8, 27, 22, 40).toISOString(), now)).toBe('Yesterday');
    const monday = new Date(2026, 8, 22, 10, 0);
    expect(dayLabel(monday.toISOString(), now)).toBe(monday.toLocaleDateString(undefined, { weekday: 'long' }));
    const earlier = new Date(2026, 7, 3, 10, 0);
    expect(dayLabel(earlier.toISOString(), now)).toBe(earlier.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
    const lastYear = new Date(2025, 11, 31, 10, 0);
    expect(dayLabel(lastYear.toISOString(), now)).toBe(
      lastYear.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }),
    );
  });

  it('say nothing for a time they cannot read', () => {
    expect(dayLabel(null, now)).toBe('');
    expect(dayLabel('yesterday-ish', now)).toBe('');
  });

  it('go over the first message of each day', () => {
    expect(dayKey(new Date(2026, 8, 28, 0, 5).toISOString())).toBe(dayKey(new Date(2026, 8, 28, 23, 55).toISOString()));
    expect(dayKey(new Date(2026, 8, 28, 0, 5).toISOString())).not.toBe(dayKey(new Date(2026, 8, 27, 23, 55).toISOString()));
    expect(dayKey(null)).toBeNull();
    expect(dayKey('yesterday-ish')).toBeNull();
  });
});

describe('failureLines', () => {
  it('names the employee when no answer came', () => {
    expect(failureLines({ message: 'The employee did not pick up this message.', code: 'not_delivered' }, 'Maya')).toEqual({
      headline: 'Maya didn’t pick up this message.',
      detail: null,
    });
    expect(failureLines({ message: 'x', code: 'timed_out' }, 'Maya').headline).toBe('Maya took too long to answer.');
    expect(failureLines({ message: 'x', code: 'interrupted' }, 'Maya').headline).toBe('Maya stopped before answering.');
  });

  it('keeps the server’s message for anything else', () => {
    expect(failureLines({ message: 'Spending cap reached.', code: 'run_failed' }, 'Maya')).toEqual({
      headline: 'Maya couldn’t answer.',
      detail: 'Spending cap reached.',
    });
    expect(failureLines(null, 'Maya')).toEqual({ headline: 'Maya couldn’t answer.', detail: null });
  });
});

describe('a working run', () => {
  it('says what it is doing', () => {
    const working = { ...emptyRun('r1', 'w1'), state: 'running' as const };
    expect(liveLabel(working, false)).toBe('Thinking');
    expect(liveLabel(working, true)).toBe('Writing');
    expect(liveLabel({ ...working, state: 'stopping' }, true)).toBe('Stopping…');
    expect(liveLabel(working, false, true)).toBe('Waiting for your OK');
  });

  it('says how fast the answer comes, once a second has passed', () => {
    expect(writingRate(400, 2_000)).toBe('· 50 tok/s');
    expect(writingRate(400, 500)).toBeNull();
    expect(writingRate(0, 5_000)).toBeNull();
  });

  it('shows the latest text until the answer comes, then the answer', () => {
    const run = (segments: RunSnapshot['segments']) => ({ ...emptyRun('r1', 'w1'), segments });
    const narration = { messageId: 'n', text: 'Let me look.', final: false };
    expect(liveText(run([narration]))).toEqual({ text: 'Let me look.', streaming: false, narration: true });
    expect(liveText(run([narration, { messageId: 'a', text: 'Two bookings', final: null }]))).toEqual({
      text: 'Two bookings',
      streaming: true,
      narration: false,
    });
    expect(liveText(run([narration, { messageId: 'a', text: 'Two bookings.', final: true }]))).toEqual({
      text: 'Two bookings.',
      streaming: false,
      narration: false,
    });
    expect(liveText(null)).toEqual({ text: '', streaming: false, narration: false });
    expect(liveText(run([{ messageId: 'a', text: '  ', final: null }])).text).toBe('');
  });
});

describe('working steps', () => {
  const step = (stepId: string, state: RunStep['state'], detail?: string): RunStep => ({ stepId, name: stepId, state, ...(detail ? { detail } : {}) });

  it('say “Working…” while they come, then how long and how many', () => {
    const steps = [step('a', 'done'), step('b', 'failed'), step('c', 'skipped')];
    expect(workLabel(steps, true, null)).toBe('Working…');
    expect(workLabel(steps, false, 12_400)).toBe('Worked for 12s · 2 steps');
    expect(workLabel([step('a', 'done')], false, 200)).toBe('Worked for 1s · 1 step');
    expect(workLabel(steps, false, 125_000)).toBe('Worked for 2m 5s · 2 steps');
    expect(workLabel(steps, false, null)).toBe('Worked for 1s · 2 steps');
  });

  it('say what each step found', () => {
    expect(stepDetail(step('a', 'done', '3 events on Saturday'))).toBe('3 events on Saturday');
    expect(stepDetail(step('a', 'done'))).toBeNull();
    expect(stepDetail(step('a', 'failed', 'quota'))).toBe('Failed · quota');
    expect(stepDetail(step('a', 'skipped'))).toBe('Skipped');
    expect(stepDetail(step('a', 'skipped', 'You declined'))).toBe('You declined');
  });

  it('a call its turn waits on follows its card', () => {
    const held = step('call_1', 'done', 'Waiting for your OK');
    const card = (patch: Record<string, unknown>) =>
      parseApproval({ approval_id: 'ap1', status: 'pending', waits: true, outcome_labels: { sent: 'Done', failed: 'Not done' }, ...patch })!;
    expect(approvalStep(held, card({}))).toMatchObject({ state: 'running', detail: 'Waiting for your OK' });
    expect(approvalStep(held, card({ status: 'approved' }))).toMatchObject({ state: 'running', detail: 'Allowed' });
    expect(approvalStep(held, card({ status: 'sent' }))).toMatchObject({ state: 'done', detail: 'Done' });
    expect(approvalStep(held, card({ status: 'failed', outcome: { certainty: 'not_sent', error: 'quota' } }))).toMatchObject({
      state: 'failed',
      detail: 'quota',
    });
    expect(approvalStep(held, card({ status: 'discarded' }))).toMatchObject({ state: 'skipped', detail: 'You declined' });
    expect(approvalStep(held, card({ status: 'expired' }))).toMatchObject({ state: 'skipped', detail: 'No answer in time' });
  });
});

describe('the live work pill', () => {
  const step = (stepId: string, state: RunStep['state'], name = stepId): RunStep => ({ stepId, name, state });
  const quiet = { writing: false, waiting: false, stopping: false };

  it('says what the run is doing', () => {
    expect(liveWorkLabel([], quiet)).toBe('Getting started');
    expect(liveWorkLabel([step('c1', 'running', 'Checked Google Calendar')], quiet)).toBe('Checked Google Calendar');
    expect(liveWorkLabel([step('a', 'running', 'Searched the web'), step('b', 'running', 'Read the inbox')], quiet)).toBe('Read the inbox');
    expect(liveWorkLabel([step('a', 'done'), step('b', 'failed')], quiet)).toBe('Thinking');
    expect(liveWorkLabel([step('a', 'done')], { ...quiet, writing: true })).toBe('Writing the reply');
    // The step under way wins over text written beside it.
    expect(liveWorkLabel([step('a', 'running', 'Searched the web')], { ...quiet, writing: true })).toBe('Searched the web');
    expect(liveWorkLabel([step('a', 'running')], { ...quiet, waiting: true })).toBe('Waiting for your OK');
    expect(liveWorkLabel([step('a', 'running')], { ...quiet, waiting: true, stopping: true })).toBe('Stopping…');
    // A step with no name says nothing about it.
    expect(liveWorkLabel([step('a', 'running', '')], quiet)).toBe('Thinking');
  });

  it('counts the steps finished of those begun, then how long it has worked', () => {
    expect(liveWorkMeta([], 4_000)).toBe('4s');
    expect(liveWorkMeta([step('a', 'done'), step('b', 'failed'), step('c', 'running')], 4_000)).toBe('2/3 · 4s');
    expect(liveWorkMeta([step('a', 'done'), step('b', 'skipped')], 125_000)).toBe('2/2 · 2m 5s');
    expect(liveWorkMeta([step('a', 'running')], null)).toBe('0/1');
    expect(liveWorkMeta([], null)).toBe('');
  });

  it('reads the steps the turn reads: a call the run waits on follows its card', () => {
    const card = (patch: Record<string, unknown> = {}) =>
      parseApproval({ approval_id: 'ap1', status: 'pending', waits: true, run_id: 'r1', tool_call_id: 'call_1', ...patch })!;
    const known = (approval: ChatApproval): ChatApprovals => ({ byId: new Map([[approval.id, approval]]), order: [approval.id], offsetMs: 0, receivedAt: 0 });
    const steps = [step('call_1', 'done', 'Send a WhatsApp message'), step('c2', 'done')];
    expect(withApprovalSteps(steps, [], NO_APPROVALS)).toBe(steps);
    const waiting = withApprovalSteps(steps, ['ap1'], known(card()));
    expect(waiting[0]).toMatchObject({ stepId: 'call_1', state: 'running', detail: 'Waiting for your OK' });
    expect(waiting[1]).toBe(steps[1]);
    expect(withApprovalSteps(steps, ['ap1'], known(card({ waits: false })))).toBe(steps);
    // Only the drafts named count.
    expect(withApprovalSteps(steps, [], known(card()))).toBe(steps);
  });
});
