/**
 * The rows a chat thread draws, from the saved messages and the session's
 * runs (docs-internal/chat_protocol.md). Pure, so every rule here is tested
 * without rendering.
 *
 * - **Runs.** `knownRuns` picks, per run, the freshest reading: the run store
 *   (live events) over what a message said when the thread was read. Until
 *   the session's subscription has answered, the thread's own `active_runs`
 *   stand in for the store; after that the store alone says which runs are
 *   live, and a message that still calls its run live is not believed (the
 *   run ended before the subscription; `useThreadRunReconcile` reads it).
 * - **Turns.** One per message, in order, with a divider wherever the
 *   employee restarted between two messages (their generation, `runKey`,
 *   changed). A run that is going, failed or was stopped (or has finished
 *   and its answer is on the way) shows on its last answer, or, before it
 *   has answered, on a turn of its own right after its last message (a live
 *   run whose messages are out of view goes last).
 * - **Keys.** A message sent from this tab keeps its key when the saved row
 *   replaces it (both carry its `clientMessageId`), and a run's first answer
 *   has the key the run's own turn had, so the text it streamed and the
 *   saved reply are one element.
 * - **Work.** What the employee did on the way (the run's steps, how long it
 *   took) goes on that same first turn, so it stays above the answer once the
 *   run has ended.
 */

import { emptyRun, isLiveRun, workedMs, type RunSnapshot, type RunStep } from '@/lib/agui/reduceRun';
import { liveUiParts, savedSources, type SourceItem, type UiPart } from '../data/parts';
import type { ChatMessage, MessageRun } from '../data/schemas';

/** A run's working steps as its turn shows them. */
export interface TurnWork {
  steps: RunStep[];
  /** Still working: the steps are still coming. */
  live: boolean;
  durationMs: number | null;
}

export type ChatTurn =
  | { kind: 'divider'; key: string }
  | { kind: 'user'; key: string; message: ChatMessage }
  | {
      kind: 'assistant';
      key: string;
      /** The saved answer; null while the run has not posted one. */
      message: ChatMessage | null;
      /** The run whose state this turn shows (working, failed, stopped):
       *  set on the run's last turn only. */
      run: RunSnapshot | null;
      /** The run's steps, on its first turn; null when it took none. */
      work: TurnWork | null;
      /** The interfaces the run streamed, on its first turn: shown until the
       *  saved reply carries them. */
      liveUi: UiPart[];
      /** The conversation's sources as of this answer, by number: its own and
       *  every earlier answer's, since an answer may cite an older one. */
      sources: ReadonlyMap<number, SourceItem>;
    };

const NO_SOURCES: ReadonlyMap<number, SourceItem> = new Map();

/** Run failures that are not news: the conversation itself went away. */
const SILENT_FAILURES: ReadonlySet<string> = new Set(['reset', 'cleared']);
/** Failures that only say no answer came; an answer that landed after all
 *  makes them wrong. */
const NO_ANSWER_FAILURES: ReadonlySet<string> = new Set(['not_delivered', 'timed_out', 'interrupted']);

function fromMessage(message: ChatMessage, run: MessageRun, sessionId: string): RunSnapshot {
  return {
    ...emptyRun(run.runId, sessionId),
    state: run.state,
    userMessageId: message.role === 'user' ? message.id : null,
    outcome: run.outcome === 'success' || run.outcome === 'stopped' ? { type: run.outcome } : null,
    error: run.error,
    steps: run.steps,
    durationMs: run.durationMs,
  };
}

function workOf(run: RunSnapshot | undefined): TurnWork | null {
  if (!run || run.steps.length === 0) return null;
  return { steps: run.steps, live: isLiveRun(run), durationMs: workedMs(run) };
}

/**
 * Every run the thread refers to, by id, at its freshest.
 * `synced`: the session's subscription has answered, so `store` knows every
 * live run. `threadActive`: the live runs the thread read reported.
 */
export function knownRuns(
  sessionId: string,
  messages: readonly ChatMessage[],
  store: Readonly<Record<string, RunSnapshot>>,
  synced: boolean,
  threadActive: readonly RunSnapshot[] = [],
): Record<string, RunSnapshot> {
  const runs: Record<string, RunSnapshot> = {};
  if (!synced) for (const run of threadActive) runs[run.runId] = run;
  for (const message of messages) {
    const run = message.run;
    if (!run || runs[run.runId] || store[run.runId]) continue;
    // Live by the thread's reading, but the subscription does not have it:
    // it ended in between, and how is not known yet.
    if (synced && isLiveRun(run)) continue;
    runs[run.runId] = fromMessage(message, run, sessionId);
  }
  return { ...runs, ...store };
}

/** Whether a run's state is worth a line in the thread. */
function shows(run: RunSnapshot, answered: boolean): boolean {
  if (isLiveRun(run)) return true;
  if (run.state === 'error') {
    const code = run.error?.code ?? '';
    return !SILENT_FAILURES.has(code) && !(answered && NO_ANSWER_FAILURES.has(code));
  }
  if (run.state === 'stopped') return true;
  // Finished: only while its answer is still on the way to the thread, with
  // something to show meanwhile (what it streamed, the steps it took).
  if (answered || run.result.noReply) return false;
  return (
    run.segments.some((segment) => segment.final !== false && segment.text.length > 0) ||
    (Boolean(run.result.replyMessageId) && (run.steps.length > 0 || run.activities.length > 0))
  );
}

function runOrder(run: RunSnapshot): string {
  return run.createdAt ?? run.startedAt ?? '';
}

export function buildTurns(messages: readonly ChatMessage[], runs: Readonly<Record<string, RunSnapshot>>): ChatTurn[] {
  // A message sent from this tab, once its saved row is in the thread.
  const saved = new Set(messages.flatMap((message) => (!message.pending && message.clientMessageId ? [message.clientMessageId] : [])));
  const visible = messages.filter((message) => !(message.pending && message.clientMessageId && saved.has(message.clientMessageId)));

  const lastIndex = new Map<string, number>();
  const lastAnswer = new Map<string, number>();
  visible.forEach((message, index) => {
    if (!message.runId) return;
    lastIndex.set(message.runId, index);
    if (message.role === 'assistant') lastAnswer.set(message.runId, index);
  });

  const showing = new Set(
    Object.values(runs).flatMap((run) => (shows(run, lastAnswer.has(run.runId)) ? [run.runId] : [])),
  );
  const placed = new Set<string>();
  const keyed = new Set<string>();
  const turns: ChatTurn[] = [];
  let generation: string | null = null;
  // The conversation's sources so far; a new map only when an answer adds some.
  let sources = NO_SOURCES;

  visible.forEach((message, index) => {
    if (generation && message.runKey && message.runKey !== generation) {
      turns.push({ kind: 'divider', key: `divider:${message.id}` });
    }
    if (message.runKey) generation = message.runKey;
    const runId = message.runId;

    if (message.role === 'user') {
      turns.push({ kind: 'user', key: `user:${message.clientMessageId ?? message.id}`, message });
      if (runId && showing.has(runId) && !lastAnswer.has(runId) && lastIndex.get(runId) === index) {
        const run = runs[runId];
        turns.push({ kind: 'assistant', key: `run:${runId}`, message: null, run, work: workOf(run), liveUi: liveUiParts(run.activities), sources });
        placed.add(runId);
        keyed.add(runId);
      }
      return;
    }

    // A run's first answer takes the key its own turn had, its work and
    // what it streamed.
    let key = `reply:${message.id}`;
    let work: TurnWork | null = null;
    let liveUi: UiPart[] = [];
    if (runId && !keyed.has(runId)) {
      key = `run:${runId}`;
      work = workOf(runs[runId]);
      liveUi = liveUiParts(runs[runId]?.activities);
      keyed.add(runId);
    }
    const last = Boolean(runId && lastAnswer.get(runId) === index);
    const run = runId && last && showing.has(runId) ? runs[runId] : null;
    if (run) placed.add(run.runId);
    const own = savedSources(message.parts);
    if (own.length > 0) {
      const next = new Map(sources);
      for (const source of own) next.set(source.n, source);
      sources = next;
    }
    turns.push({ kind: 'assistant', key, message, run, work, liveUi, sources });
  });

  const unplaced = [...showing]
    .map((runId) => runs[runId])
    .filter((run) => !placed.has(run.runId) && isLiveRun(run))
    .sort((a, b) => runOrder(a).localeCompare(runOrder(b)));
  for (const run of unplaced) {
    turns.push({ kind: 'assistant', key: `run:${run.runId}`, message: null, run, work: workOf(run), liveUi: liveUiParts(run.activities), sources });
  }
  return turns;
}
