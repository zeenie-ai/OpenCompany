/**
 * A chat run's state, folded from its events (lib/agui/events.ts), the same
 * way server/services/chat/reducer.py folds them, so a snapshot from
 * `chat_subscribe` and a stream of events agree.
 *
 * `applyRunEvent` is pure and returns the run unchanged (the same object)
 * for an event whose `seq` it has already seen. Gaps are the caller's to
 * detect (stores/chatRunStore.ts asks for a fresh snapshot).
 */

import {
  RUN_KINDS,
  parseError,
  parseOutcome,
  parseResult,
  type RunError,
  type RunEvent,
  type RunKind,
  type RunOutcome,
  type RunResult,
  type RunState,
  type StepState,
} from './events';
import { applyPatch } from './patch';
import type { WorkflowControlStatus } from '@/contexts/WebSocketContext';

export interface RunStep {
  stepId: string;
  name: string;
  state: StepState;
  icon?: string;
  detail?: string;
  durationMs?: number;
  narration?: string;
}

export interface RunSegment {
  messageId: string;
  text: string;
  /** True for the reply, false for narration beside a tool call, null while
   *  it streams. */
  final: boolean | null;
}

/** A part the run is building (generated UI, sources, ...), as its
 *  `activity.*` events have it so far. `messageId` is the part's id. */
export interface RunActivity {
  messageId: string;
  activityType: string;
  content: unknown;
  /** Patches applied since the snapshot (development builds show the count). */
  patches: number;
}

export interface RunSnapshot {
  runId: string;
  sessionId: string;
  workflowId: string | null;
  /** Derived from the run's owning generation, never a chat lifecycle state. */
  workflowControl?: WorkflowControlStatus;
  kind: RunKind;
  state: RunState;
  /** The newest event folded in; 0 before any. */
  seq: number;
  hubEpoch: string | null;
  userMessageId: string | null;
  replyMessageId: string | null;
  parentRunId: string | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  /** How long it worked, once it has ended and the server said. */
  durationMs: number | null;
  steps: RunStep[];
  segments: RunSegment[];
  activities: RunActivity[];
  outcome: RunOutcome | null;
  result: RunResult;
  error: RunError | null;
}

export const LIVE_RUN_STATES: ReadonlySet<RunState> = new Set(['queued', 'pending', 'running', 'stopping']);

export function isLiveRun(run: Pick<RunSnapshot, 'state'>): boolean {
  return LIVE_RUN_STATES.has(run.state);
}

export function emptyRun(runId: string, sessionId: string): RunSnapshot {
  return {
    runId,
    sessionId,
    workflowId: null,
    kind: 'message',
    state: 'pending',
    seq: 0,
    hubEpoch: null,
    userMessageId: null,
    replyMessageId: null,
    parentRunId: null,
    createdAt: null,
    startedAt: null,
    finishedAt: null,
    durationMs: null,
    steps: [],
    segments: [],
    activities: [],
    outcome: null,
    result: {},
    error: null,
  };
}

/** How long a run worked: what the server said, else from its start and
 *  end; null while either is unknown. */
export function workedMs(run: Pick<RunSnapshot, 'durationMs' | 'startedAt' | 'finishedAt'>): number | null {
  if (run.durationMs !== null) return run.durationMs;
  if (!run.startedAt || !run.finishedAt) return null;
  const span = Date.parse(run.finishedAt) - Date.parse(run.startedAt);
  return Number.isFinite(span) && span >= 0 ? span : null;
}

const RUN_STATES: readonly RunState[] = ['queued', 'pending', 'running', 'stopping', 'finished', 'error', 'stopped'];
const STEP_STATES: readonly StepState[] = ['running', 'done', 'failed', 'skipped'];

type Data = Record<string, unknown>;

function isRecord(value: unknown): value is Data {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function optionalText(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function oneOf<T extends string>(value: unknown, options: readonly T[], fallback: T): T {
  return typeof value === 'string' && (options as readonly string[]).includes(value) ? (value as T) : fallback;
}

/** A run's steps as the server stores and sends them; unreadable ones are
 *  left out. */
export function stepsFromWire(raw: unknown): RunStep[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!isRecord(item)) return [];
    const stepId = optionalText(item.step_id);
    if (!stepId) return [];
    const step: RunStep = { stepId, name: optionalText(item.name) ?? '', state: oneOf(item.state, STEP_STATES, 'done') };
    const icon = optionalText(item.icon);
    const detail = optionalText(item.detail);
    const narration = optionalText(item.narration);
    if (icon) step.icon = icon;
    if (detail) step.detail = detail;
    if (typeof item.duration_ms === 'number') step.durationMs = item.duration_ms;
    if (narration) step.narration = narration;
    return [step];
  });
}

function wireSegments(raw: unknown): RunSegment[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!isRecord(item)) return [];
    const messageId = optionalText(item.message_id);
    if (!messageId) return [];
    return [{ messageId, text: typeof item.text === 'string' ? item.text : '', final: typeof item.final === 'boolean' ? item.final : null }];
  });
}

function wireActivities(raw: unknown): RunActivity[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!isRecord(item)) return [];
    const messageId = optionalText(item.message_id);
    const activityType = optionalText(item.activity_type);
    if (!messageId || !activityType) return [];
    const patches = typeof item.patches === 'number' && item.patches >= 0 ? item.patches : 0;
    return [{ messageId, activityType, content: item.content ?? null, patches }];
  });
}

/** A run as the server sends it (`chat_subscribe`'s `active_runs`,
 *  `get_chat_run`'s `run`); null when it names no run. */
export function snapshotFromWire(raw: unknown): RunSnapshot | null {
  if (!isRecord(raw)) return null;
  const runId = optionalText(raw.run_id);
  const sessionId = optionalText(raw.session_id);
  if (!runId || !sessionId) return null;
  const outcome = parseOutcome(raw.outcome);
  return {
    runId,
    sessionId,
    workflowId: textOrNull(raw.workflow_id),
    ...(isRecord(raw.workflow_control) && typeof raw.workflow_control.revision === 'number'
      && typeof raw.workflow_control.state === 'string'
      ? { workflowControl: raw.workflow_control as unknown as WorkflowControlStatus }
      : {}),
    kind: oneOf(raw.kind, RUN_KINDS, 'message'),
    state: oneOf(raw.state, RUN_STATES, 'pending'),
    seq: typeof raw.seq === 'number' && Number.isInteger(raw.seq) && raw.seq >= 0 ? raw.seq : 0,
    hubEpoch: textOrNull(raw.hub_epoch),
    userMessageId: textOrNull(raw.user_message_id),
    replyMessageId: textOrNull(raw.reply_message_id),
    parentRunId: textOrNull(raw.parent_run_id),
    createdAt: textOrNull(raw.created_at),
    startedAt: textOrNull(raw.started_at),
    finishedAt: textOrNull(raw.finished_at),
    durationMs: typeof raw.duration_ms === 'number' && raw.duration_ms >= 0 ? raw.duration_ms : null,
    steps: stepsFromWire(raw.steps),
    segments: wireSegments(raw.segments),
    activities: wireActivities(raw.activities),
    outcome,
    result: parseResult(raw.result),
    // A failed run keeps its hint beside the error, in `result`.
    error: optionalText(raw.error)
      ? parseError({ ...(isRecord(raw.result) ? raw.result : {}), message: raw.error, code: raw.error_code })
      : null,
  };
}

function withStep(steps: RunStep[], stepId: string, update: (step: RunStep) => RunStep): RunStep[] {
  const index = steps.findIndex((step) => step.stepId === stepId);
  if (index === -1) return [...steps, update({ stepId, name: '', state: 'running' })];
  return steps.map((step, i) => (i === index ? update(step) : step));
}

function withSegment(segments: RunSegment[], messageId: string, update: (segment: RunSegment) => RunSegment): RunSegment[] {
  const index = segments.findIndex((segment) => segment.messageId === messageId);
  if (index === -1) return [...segments, update({ messageId, text: '', final: null })];
  return segments.map((segment, i) => (i === index ? update(segment) : segment));
}

function withActivity(
  activities: RunActivity[],
  messageId: string,
  activityType: string,
  update: (activity: RunActivity) => RunActivity,
): RunActivity[] {
  const index = activities.findIndex((activity) => activity.messageId === messageId);
  if (index === -1) return [...activities, update({ messageId, activityType, content: null, patches: 0 })];
  return activities.map((activity, i) => (i === index ? update(activity) : activity));
}

/** `run` with `event` folded in. An event at or before the run's `seq` is a
 *  duplicate: the same object comes back. */
export function applyRunEvent(run: RunSnapshot, event: RunEvent): RunSnapshot {
  if (event.seq <= run.seq) return run;
  const next: RunSnapshot = {
    ...run,
    seq: event.seq,
    hubEpoch: event.hubEpoch,
    sessionId: event.sessionId,
    workflowId: event.workflowId ?? run.workflowId,
  };
  switch (event.type) {
    case 'started':
      return {
        ...next,
        state: run.state === 'stopping' ? 'stopping' : 'running',
        kind: event.kind,
        parentRunId: event.parentRunId ?? run.parentRunId,
        userMessageId: event.userMessageId ?? run.userMessageId,
        replyMessageId: event.replyMessageId ?? run.replyMessageId,
        startedAt: event.startedAt ?? run.startedAt ?? event.time,
      };
    case 'finished':
      return {
        ...next,
        state: event.outcome.type === 'stopped' ? 'stopped' : 'finished',
        outcome: event.outcome,
        result: event.result,
        finishedAt: event.time ?? run.finishedAt,
        durationMs: event.durationMs,
      };
    case 'failed':
      return { ...next, state: 'error', error: event.error, finishedAt: event.time ?? run.finishedAt };
    case 'step.started':
      return {
        ...next,
        steps: withStep(run.steps, event.stepId, (step) => ({
          ...step,
          name: event.stepName || step.name,
          state: 'running',
          ...(event.icon ? { icon: event.icon } : {}),
        })),
      };
    case 'step.finished':
      return {
        ...next,
        steps: withStep(run.steps, event.stepId, (step) => ({
          ...step,
          name: event.stepName || step.name,
          state: event.state,
          ...(event.detail !== undefined ? { detail: event.detail } : {}),
          ...(event.durationMs !== undefined ? { durationMs: event.durationMs } : {}),
          ...(event.narration !== undefined ? { narration: event.narration } : {}),
        })),
      };
    case 'text.started':
      return { ...next, segments: withSegment(run.segments, event.messageId, (segment) => segment) };
    case 'text.content':
      return { ...next, segments: withSegment(run.segments, event.messageId, (segment) => ({ ...segment, text: segment.text + event.delta })) };
    case 'text.ended':
      return {
        ...next,
        segments: withSegment(run.segments, event.messageId, (segment) => ({ ...segment, final: event.final })),
        replyMessageId: event.replyMessageId ?? run.replyMessageId,
      };
    case 'activity.snapshot':
      return {
        ...next,
        activities: withActivity(run.activities, event.messageId, event.activityType, (activity) =>
          event.replace || activity.content === null
            ? { ...activity, activityType: event.activityType, content: event.content, patches: 0 }
            : activity,
        ),
      };
    case 'activity.delta':
      return {
        ...next,
        activities: withActivity(run.activities, event.messageId, event.activityType, (activity) => ({
          ...activity,
          content: applyPatch(activity.content, event.patch),
          patches: activity.patches + event.patch.length,
        })),
      };
    case 'custom':
      if (event.name === 'opencompany.segment_discarded') {
        const discarded = event.value.message_id;
        return { ...next, segments: run.segments.filter((segment) => segment.messageId !== discarded) };
      }
      if (event.name === 'opencompany.stopping') return { ...next, state: 'stopping' };
      return next;
  }
}

/** Fold a run's events, in order, from a run that has seen none. */
export function replayRun(runId: string, sessionId: string, events: readonly RunEvent[]): RunSnapshot {
  return events.reduce(applyRunEvent, emptyRun(runId, sessionId));
}
