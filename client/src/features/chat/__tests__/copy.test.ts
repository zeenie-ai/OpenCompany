/**
 * What a turn says: when a message was written, why a run did not answer,
 * what a working run is doing and showing, and its steps.
 */

import { describe, expect, it } from 'vitest';
import { emptyRun, type RunSnapshot, type RunStep } from '@/lib/agui/reduceRun';
import { timeLabel } from '../thread/timeLabel';
import { parseApproval } from '../data/approvals';
import { approvalStep, failureLines, liveLabel, liveText, stepDetail, workLabel, writingRate } from '../turns/runCopy';

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
