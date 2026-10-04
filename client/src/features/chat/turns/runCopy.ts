/**
 * What a turn says about its run, kept apart from the component so the
 * component file exports components only (react-refresh) and the wording is
 * testable on its own.
 */

import type { RunError } from '@/lib/agui/events';
import type { RunSnapshot, RunStep } from '@/lib/agui/reduceRun';

/** The text a run that has not posted its answer shows. */
export interface LiveText {
  text: string;
  /** Still arriving: the caret follows it. */
  streaming: boolean;
  /** Written beside tool calls, not the answer (shown muted until the
   *  answer replaces it). */
  narration: boolean;
}

/**
 * The run's answer as it streamed; before any, the latest thing it wrote,
 * which may turn out to be narration beside its tool calls ("Let me check
 * the calendar.") and gives way to the next.
 */
export function liveText(run: RunSnapshot | null): LiveText {
  const none: LiveText = { text: '', streaming: false, narration: false };
  if (!run || run.segments.length === 0) return none;
  const answer = run.segments.filter((segment) => segment.final === true);
  const last = run.segments.at(-1)!;
  if (answer.length > 0 && last.final === true) {
    return { text: answer.map((segment) => segment.text).join('\n\n').trim(), streaming: false, narration: false };
  }
  const text = last.text.trim();
  if (!text) return none;
  return { text, streaming: last.final === null, narration: last.final === false };
}

/** A failed run in the owner's words: the server's codes for a message
 *  nobody answered get a sentence naming the employee; any other failure
 *  keeps the server's own message as the detail. */
export function failureLines(error: RunError | null, name: string): { headline: string; detail: string | null } {
  switch (error?.code) {
    case 'not_delivered':
      return { headline: `${name} didn’t pick up this message.`, detail: null };
    case 'timed_out':
      return { headline: `${name} took too long to answer.`, detail: null };
    case 'interrupted':
      return { headline: `${name} stopped before answering.`, detail: null };
    default:
      return { headline: `${name} couldn’t answer.`, detail: error?.message || null };
  }
}

/** The status line's label while a run works (design handoff: "Thinking"
 *  until text comes, then "Writing"). */
export function liveLabel(run: RunSnapshot, writing: boolean): string {
  if (run.state === 'stopping') return 'Stopping…';
  return writing ? 'Writing' : 'Thinking';
}

/** "· 42 tok/s": how fast the answer is coming, a token counted as four
 *  characters; null before a second has passed. */
export function writingRate(characters: number, elapsedMs: number): string | null {
  if (characters <= 0 || elapsedMs < 1000) return null;
  return `· ${Math.round(characters / 4 / (elapsedMs / 1000))} tok/s`;
}

function duration(ms: number | null): string {
  const seconds = Math.max(1, Math.round((ms ?? 0) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/** The steps disclosure's label: "Working…" while the steps come, then
 *  "Worked for 12s · 3 steps" (skipped steps not counted). */
export function workLabel(steps: readonly RunStep[], live: boolean, durationMs: number | null): string {
  if (live) return 'Working…';
  const count = steps.filter((step) => step.state !== 'skipped').length;
  return `Worked for ${duration(durationMs)} · ${count} ${count === 1 ? 'step' : 'steps'}`;
}

/** What a step says under its name. */
export function stepDetail(step: RunStep): string | null {
  if (step.state === 'skipped') return 'Skipped';
  if (step.state === 'failed') return step.detail ? `Failed · ${step.detail}` : 'Failed';
  return step.detail ?? null;
}

/** Why a change to the conversation (an edit, Try again, another version)
 *  did not go through, by the server's code. */
export function branchRefusalText(code: string, name: string): string {
  switch (code) {
    case 'revision_conflict':
      return 'The conversation changed meanwhile. Try again.';
    case 'run_in_progress':
      return `${name} is still answering. Try again once they finish.`;
    case 'cannot_rewind':
      return `${name} has summed up the conversation since, so it can’t go back to that point.`;
    case 'older_generation':
      return `That’s from before ${name} restarted, so it can’t be changed.`;
    case 'branch_unavailable':
      return 'That version can’t be brought back any more.';
    case 'not_editable':
    case 'not_found':
      return 'That can’t be changed.';
    default:
      return 'That didn’t go through. Try again.';
  }
}

/** What the owner hears after rating an answer: where the rating goes. */
export function feedbackThanks(name: string): string {
  return `Thanks — ${name} will see this next time.`;
}
