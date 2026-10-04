/**
 * A chat run's events as the server sends them (docs-internal/chat_protocol.md).
 *
 * A `chat_run_event` frame's `data` is a CloudEvents envelope from
 * `opencompany://services/chat` whose type is
 * `com.opencompany.chat.run.<suffix>`, subject the run id and id
 * `<run id>:<seq>`; the scope (`session_id`, `run_id`, `seq`, `hub_epoch`,
 * `workflow_id`) is in `data`. `parseRunFrame` checks all of that and returns
 * a typed event with camelCase fields, a session's resync request, or null.
 *
 * Fields beyond the scope are read one at a time: a field that cannot be read
 * is left out, never the whole event. A suffix the server does not send is
 * null.
 */

import type { PatchOp } from './patch';

export const RUN_SOURCE = 'opencompany://services/chat';
export const RUN_TYPE_PREFIX = 'com.opencompany.chat.run.';

export type RunKind = 'message' | 'edit' | 'regenerate' | 'action';
export type RunState = 'queued' | 'pending' | 'running' | 'stopping' | 'finished' | 'error' | 'stopped';
export type StepState = 'running' | 'done' | 'failed' | 'skipped';

export interface RunOutcome {
  type: 'success' | 'stopped';
}

export interface RunResult {
  replyMessageId?: string;
  noReply?: boolean;
}

export interface RunError {
  message: string;
  code: string;
  hint?: string;
  requiresUserAction?: boolean;
}

export interface RunEventScope {
  runId: string;
  sessionId: string;
  workflowId: string | null;
  seq: number;
  hubEpoch: string;
  /** When the server published it (RFC 3339), or null. */
  time: string | null;
}

/** What an event says beyond its scope, by type. */
export type RunEventBody =
  | {
      type: 'started';
      kind: RunKind;
      parentRunId?: string;
      userMessageId?: string;
      replyMessageId?: string;
      startedAt?: string;
    }
  | { type: 'finished'; outcome: RunOutcome; result: RunResult; durationMs: number; stepCount: number }
  | { type: 'failed'; error: RunError }
  | { type: 'step.started'; stepId: string; stepName: string; icon?: string }
  | {
      type: 'step.finished';
      stepId: string;
      stepName?: string;
      state: Exclude<StepState, 'running'>;
      detail?: string;
      durationMs?: number;
      narration?: string;
    }
  | { type: 'text.started'; messageId: string }
  | { type: 'text.content'; messageId: string; delta: string }
  | { type: 'text.ended'; messageId: string; final: boolean; replyMessageId?: string }
  | { type: 'activity.snapshot'; messageId: string; activityType: string; content: unknown; replace: boolean }
  | { type: 'activity.delta'; messageId: string; activityType: string; patch: PatchOp[] }
  | { type: 'custom'; name: string; value: Record<string, unknown> };

export type RunEvent = RunEventScope & RunEventBody;

/** The server dropped frames this socket could not keep up with: take a
 *  fresh snapshot of the session. */
export interface ResyncRequest {
  type: 'resync';
  sessionId: string;
  hubEpoch: string;
}

export type RunFrame = RunEvent | ResyncRequest;

export const RUN_KINDS: readonly RunKind[] = ['message', 'edit', 'regenerate', 'action'];
const FINISHED_STEP_STATES = ['done', 'failed', 'skipped'] as const;
const OUTCOME_TYPES = ['success', 'stopped'] as const;
/** Patch operations kept per `activity.delta`. */
const MAX_PATCH_OPS = 64;

type Data = Record<string, unknown>;

function isRecord(value: unknown): value is Data {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function oneOf<T extends string>(value: unknown, options: readonly T[]): T | undefined {
  return typeof value === 'string' && (options as readonly string[]).includes(value) ? (value as T) : undefined;
}

/** Copies only the keys whose values are set. */
function defined<T extends object>(fields: T): T {
  return Object.fromEntries(Object.entries(fields).filter(([, value]) => value !== undefined)) as T;
}

export function parseOutcome(raw: unknown): RunOutcome | null {
  if (!isRecord(raw)) return null;
  const type = oneOf(raw.type, OUTCOME_TYPES);
  return type ? { type } : null;
}

export function parseResult(raw: unknown): RunResult {
  if (!isRecord(raw)) return {};
  return defined({
    replyMessageId: text(raw.reply_message_id),
    noReply: raw.no_reply === true ? true : undefined,
  });
}

export function parseError(raw: Data): RunError {
  return defined({
    message: text(raw.message) ?? 'The run failed.',
    code: text(raw.code) ?? 'run_failed',
    hint: text(raw.hint),
    requiresUserAction: raw.requires_user_action === true ? true : undefined,
  });
}

function eventFields(suffix: string, data: Data): RunEventBody | null {
  switch (suffix) {
    case 'started':
      return defined({
        type: 'started' as const,
        kind: oneOf(data.kind, RUN_KINDS) ?? 'message',
        parentRunId: text(data.parent_run_id),
        userMessageId: text(data.user_message_id),
        replyMessageId: text(data.reply_message_id),
        startedAt: text(data.started_at),
      });
    case 'finished':
      return {
        type: 'finished',
        outcome: parseOutcome(data.outcome) ?? { type: 'success' },
        result: parseResult(data.result),
        durationMs: count(data.duration_ms) ?? 0,
        stepCount: count(data.step_count) ?? 0,
      };
    case 'failed':
      return { type: 'failed', error: parseError(data) };
    case 'step.started': {
      const stepId = text(data.step_id);
      return stepId ? defined({ type: 'step.started' as const, stepId, stepName: text(data.step_name) ?? '', icon: text(data.icon) }) : null;
    }
    case 'step.finished': {
      const stepId = text(data.step_id);
      if (!stepId) return null;
      return defined({
        type: 'step.finished' as const,
        stepId,
        stepName: text(data.step_name),
        state: oneOf(data.state, FINISHED_STEP_STATES) ?? 'done',
        detail: text(data.detail),
        durationMs: count(data.duration_ms),
        narration: text(data.narration),
      });
    }
    case 'text.started':
    case 'text.content':
    case 'text.ended': {
      const messageId = text(data.message_id);
      if (!messageId) return null;
      if (suffix === 'text.started') return { type: 'text.started', messageId };
      if (suffix === 'text.content') {
        const delta = typeof data.delta === 'string' ? data.delta : '';
        return delta ? { type: 'text.content', messageId, delta } : null;
      }
      return defined({
        type: 'text.ended' as const,
        messageId,
        final: data.final === true,
        replyMessageId: text(data.reply_message_id),
      });
    }
    case 'activity.snapshot':
    case 'activity.delta': {
      const messageId = text(data.message_id);
      const activityType = text(data.activity_type);
      if (!messageId || !activityType) return null;
      if (suffix === 'activity.snapshot') {
        return { type: 'activity.snapshot', messageId, activityType, content: data.content ?? null, replace: data.replace !== false };
      }
      if (!Array.isArray(data.patch)) return null;
      const patch = data.patch
        .slice(0, MAX_PATCH_OPS)
        .filter((op): op is PatchOp => isRecord(op) && typeof op.op === 'string' && typeof op.path === 'string');
      return { type: 'activity.delta', messageId, activityType, patch };
    }
    case 'custom': {
      const name = text(data.name);
      return name ? { type: 'custom', name, value: isRecord(data.value) ? data.value : {} } : null;
    }
    default:
      return null;
  }
}

/** One `chat_run_event` frame's `data`, checked and typed; null when it is
 *  not a run event this client can trust. */
export function parseRunFrame(raw: unknown): RunFrame | null {
  if (!isRecord(raw) || raw.specversion !== '1.0' || raw.source !== RUN_SOURCE) return null;
  const type = typeof raw.type === 'string' ? raw.type : '';
  if (!type.startsWith(RUN_TYPE_PREFIX) || !isRecord(raw.data)) return null;
  const suffix = type.slice(RUN_TYPE_PREFIX.length);
  const data = raw.data;
  const sessionId = text(data.session_id);
  const hubEpoch = text(data.hub_epoch);
  if (!sessionId || !hubEpoch) return null;

  const runId = text(data.run_id);
  if (!runId) {
    // The only event without a run: the hub asking a lagging socket to resync.
    return suffix === 'custom' && data.name === 'opencompany.resync' ? { type: 'resync', sessionId, hubEpoch } : null;
  }
  const seq = data.seq;
  if (typeof seq !== 'number' || !Number.isInteger(seq) || seq < 1) return null;
  if (raw.subject !== runId || raw.id !== `${runId}:${seq}`) return null;

  const fields = eventFields(suffix, data);
  if (!fields) return null;
  const scope: RunEventScope = {
    runId,
    sessionId,
    workflowId: typeof data.workflow_id === 'string' ? data.workflow_id : null,
    seq,
    hubEpoch,
    time: text(raw.time) ?? null,
  };
  return { ...scope, ...fields } as RunEvent;
}
