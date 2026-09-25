/**
 * The hire draft: the setup screen an AI model writes from the owner's job
 * description, and the changes the owner asks for on it. One draft at a
 * time, driven by the Home composer.
 *
 * Status: idle -> working -> ready | failed. From ready, a change request
 * goes back to working with the same job and the replies so far.
 *
 * Every request carries a fresh `draft_token` (never `request_id`, which is
 * the WebSocket correlation id). A reply whose token is not the current one
 * is dropped, so a slow reply never lands after a newer request or after
 * Discard. Discard also asks the server to cancel the call; the server
 * cancels an older call on its own when a newer token from the same owner
 * arrives.
 *
 * The server builds the model's messages (the catalogue prompt, the owner's
 * context, the "The job:" / "Change the setup:" prefixes); the client sends
 * the owner's words and the earlier replies, then reads the reply it gets
 * back (parse) and makes it safe to render (normalize) before showing it.
 */

import { useMemo } from 'react';
import { create } from 'zustand';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { appRefSchema, type AppRef } from '../data/schemas';
import { setPath, type UiState } from './expressions';
import { normalizeSpec, type NormalizedSpec } from './normalize';
import { parseReply } from './parse';

export type DraftStatus = 'idle' | 'working' | 'ready' | 'failed';

/** Server error codes, plus `connection` for a socket that closed first. */
export type SetupErrorCode =
  | 'no_ai_provider'
  | 'timeout'
  | 'provider_error'
  | 'unparseable'
  | 'cancelled'
  | 'busy'
  | 'invalid_request'
  | 'connection';

const SERVER_CODES: ReadonlySet<string> = new Set([
  'no_ai_provider',
  'timeout',
  'provider_error',
  'unparseable',
  'cancelled',
  'busy',
  'invalid_request',
]);

/** One accepted reply. `change` is what the owner asked to change, null for
 *  the reply to the job itself. */
export interface DraftTurn {
  change: string | null;
  reply: string;
}

export interface DraftFailure {
  code: SetupErrorCode;
  /** What Retry sends again, and whether it was a change request. */
  text: string;
  refine: boolean;
}

export interface DraftSource {
  provider: string | null;
  model: string | null;
}

export interface DraftState {
  status: DraftStatus;
  /** The composer is editing the current draft ("Editing the draft"). */
  refining: boolean;
  /** The composer's text. */
  input: string;
  /** The job as the owner first described it; the draft header shows it. */
  job: string;
  turns: DraftTurn[];
  /** Token of the request in flight, if any. */
  token: string | null;
  failure: DraftFailure | null;
  source: DraftSource | null;
  /** The latest reply, read and made safe to render. */
  spec: NormalizedSpec | null;
  /** The model's one-sentence introduction of the new employee. */
  intro: string;
  /** What the owner has set on the setup screen (toggles, choices, inputs). */
  uiState: UiState;
  /** Apps the reply names, as the server resolved them: lower-cased name -> ref. */
  apps: Record<string, AppRef>;
  /** Bumped on every accepted reply, so the renderer restarts its reveal. */
  version: number;
  /** A hire request is in flight. */
  hiring: boolean;
  /** The idempotency key of the last hire attempt, reused while the payload
   *  is unchanged: a retry after a lost response must not hire twice. */
  hireKey: { fingerprint: string; key: string } | null;
}

const INITIAL: DraftState = {
  status: 'idle',
  refining: false,
  input: '',
  job: '',
  turns: [],
  token: null,
  failure: null,
  source: null,
  spec: null,
  intro: '',
  uiState: {},
  apps: {},
  version: 0,
  hiring: false,
  hireKey: null,
};

/** The server allows 90 s for cloud models and 240 s for local ones, plus
 *  one retry; this waits a little longer than the longest of those. */
export const SETUP_TIMEOUT_MS = 300_000;

/** Earlier replies sent with a change request. The latest reply already
 *  carries every earlier change; the server trims further to fit. */
export const HISTORY_TURNS = 3;

/** Longest job or change the composer sends (the server's cap). */
export const MAX_JOB_LENGTH = 2000;

export const useDraftStore = create<DraftState>(() => ({ ...INITIAL }));

export function newDraftToken(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  } catch {
    // Not a secure context: fall through.
  }
  return `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** The WebSocket request function (WebSocketActions.sendRequest), or a test double. */
export type SendRequest = (type: string, data?: Record<string, unknown>, timeoutMs?: number) => Promise<any>;

export interface SetupResponse {
  success?: boolean;
  error?: string;
  draft_token?: string;
  reply?: unknown;
  provider?: string | null;
  model?: string | null;
  apps?: unknown;
}

interface PendingRequest {
  text: string;
  refine: boolean;
}

function errorCode(value: unknown): SetupErrorCode {
  return typeof value === 'string' && SERVER_CODES.has(value) ? (value as SetupErrorCode) : 'provider_error';
}

function isTimeout(error: unknown): boolean {
  return error instanceof Error && /timeout/i.test(error.message);
}

function parseApps(raw: unknown): Record<string, AppRef> {
  const out: Record<string, AppRef> = {};
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
  for (const [name, value] of Object.entries(raw)) {
    const parsed = appRefSchema.safeParse(value);
    if (parsed.success) out[name.trim().toLowerCase()] = parsed.data;
  }
  return out;
}

function cancelOnServer(send: SendRequest, token: string | null): void {
  if (!token) return;
  void send('cancel_employee_setup', { draft_token: token }).catch(() => {
    // Best effort: the server also cancels it when a newer token arrives.
  });
}

/** Record a failure if it belongs to the request still in flight. A failed
 *  change keeps the last good version on screen, so Retry can send it
 *  again and Hire still works. */
export function failRequest(token: string, code: SetupErrorCode, request: PendingRequest): boolean {
  if (useDraftStore.getState().token !== token) return false;
  // Cancelled by a newer request or by Discard; whoever did that owns the state.
  if (code === 'cancelled') return false;
  useDraftStore.setState({ status: 'failed', token: null, failure: { code, ...request } });
  return true;
}

/** Apply a reply if it belongs to the request still in flight. A reply
 *  with nothing renderable counts as a failure (`unparseable`). */
export function acceptReply(token: string, response: SetupResponse, request: PendingRequest): boolean {
  const state = useDraftStore.getState();
  if (state.token !== token) return false;
  const reply = typeof response.reply === 'string' ? response.reply : '';
  const parsed = parseReply(reply);
  const spec = parsed.failed ? null : normalizeSpec(parsed.spec);
  if (!spec) return failRequest(token, 'unparseable', request);
  const turn: DraftTurn = { change: request.refine ? request.text : null, reply };
  useDraftStore.setState({
    status: 'ready',
    refining: false,
    turns: request.refine ? [...state.turns, turn] : [turn],
    token: null,
    failure: null,
    source: { provider: response.provider ?? null, model: response.model ?? null },
    spec,
    intro: parsed.text.trim(),
    uiState: spec.state,
    apps: parseApps(response.apps),
    version: state.version + 1,
  });
  return true;
}

/**
 * Send the composer's text: a new job, or a change to the current draft
 * when the composer is refining one. Resolves false when nothing was sent
 * (empty text, or a request already in flight).
 */
export async function submitDraft(send: SendRequest, raw: string, options: { refine?: boolean } = {}): Promise<boolean> {
  const text = raw.trim().slice(0, MAX_JOB_LENGTH);
  const state = useDraftStore.getState();
  if (!text || state.status === 'working' || state.hiring) return false;
  const refine = (options.refine ?? state.refining) && state.turns.length > 0;
  const token = newDraftToken();
  const job = refine ? state.job : text;
  const request: PendingRequest = { text, refine };

  useDraftStore.setState(
    refine
      ? { status: 'working', refining: false, input: '', token, failure: null }
      : { ...INITIAL, status: 'working', job, token, version: state.version },
  );

  const payload: Record<string, unknown> = refine
    ? { job, refine: text, history: state.turns.slice(-HISTORY_TURNS), draft_token: token }
    : { job, draft_token: token };
  try {
    const response: SetupResponse | undefined = await send('generate_employee_setup', payload, SETUP_TIMEOUT_MS);
    if (response?.success === false) failRequest(token, errorCode(response.error), request);
    else acceptReply(token, response ?? {}, request);
  } catch (error) {
    failRequest(token, isTimeout(error) ? 'timeout' : 'connection', request);
  }
  return true;
}

/** Send the failed request again. */
export function retryDraft(send: SendRequest): Promise<boolean> {
  const { failure, turns } = useDraftStore.getState();
  if (!failure) return Promise.resolve(false);
  useDraftStore.setState({ status: failure.refine && turns.length > 0 ? 'ready' : 'idle', failure: null });
  return submitDraft(send, failure.text, { refine: failure.refine });
}

/** Throw the draft away, cancelling a request in flight. The composer
 *  keeps whatever the owner was typing. */
export function discardDraft(send: SendRequest): void {
  const { token, input } = useDraftStore.getState();
  useDraftStore.setState({ ...INITIAL, input, version: useDraftStore.getState().version });
  cancelOnServer(send, token);
}

/** Start or stop editing the current draft from the composer. */
export function setRefining(on: boolean): void {
  const state = useDraftStore.getState();
  if (on && (!state.spec || state.status === 'working')) return;
  if (state.refining !== on) useDraftStore.setState({ refining: on });
}

export function setComposerInput(input: string): void {
  useDraftStore.setState({ input });
}

/** A control on the setup screen changed a value. */
export function setDraftValue(path: string, value: unknown): void {
  useDraftStore.setState((state) => ({ uiState: setPath(state.uiState, path, value) }));
}

/**
 * Claim the one hire slot. Returns the idempotency key to send, or null when
 * a hire is already in flight. The same payload gets the same key.
 */
export function beginHire(fingerprint: string): string | null {
  const state = useDraftStore.getState();
  if (state.hiring) return null;
  const key = state.hireKey?.fingerprint === fingerprint ? state.hireKey.key : newDraftToken();
  useDraftStore.setState({ hiring: true, hireKey: { fingerprint, key } });
  return key;
}

export function endHire(): void {
  useDraftStore.setState({ hiring: false });
}

/** Clear the draft after a hire (the composer's text stays). */
export function clearHiredDraft(): void {
  const { input, version } = useDraftStore.getState();
  useDraftStore.setState({ ...INITIAL, input, version });
}

/** Test-only. */
export function resetDraftForTests(): void {
  useDraftStore.setState({ ...INITIAL });
}

/** The draft actions, bound to the WebSocket. Stable across renders. */
export function useDraftActions() {
  const { sendRequest } = useWebSocketActions();
  return useMemo(() => {
    const send = sendRequest as SendRequest;
    return {
      submit: (text: string, options?: { refine?: boolean }) => submitDraft(send, text, options),
      retry: () => retryDraft(send),
      discard: () => discardDraft(send),
      setRefining,
      setInput: setComposerInput,
      setValue: setDraftValue,
    };
  }, [sendRequest]);
}
