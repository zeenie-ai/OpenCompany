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
 * is dropped, so a slow reply never lands after a newer request, Cancel or
 * Discard. Cancel and Discard also ask the server to stop the call; the
 * server cancels an older call on its own when a newer token from the same
 * owner arrives. Cancel hands the owner's words back to the composer.
 *
 * The server builds the model's messages (the catalogue prompt, the owner's
 * context, the "The job:" / "Change the setup:" prefixes); the client sends
 * the owner's words and the earlier replies, then reads the reply it gets
 * back (parse) and makes it safe to render (normalize) before showing it.
 * The screen's trigger is then checked against the apps the server
 * resolved for that reply: one naming an app that cannot start the work
 * falls back to the owner messaging them, so the screen never shows a
 * trigger Hire could not build.
 */

import { useMemo } from 'react';
import { z } from 'zod';
import { create } from 'zustand';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { appRefSchema } from '../data/schemas';
import { SPIKE, spikeOrb } from '../orb/orb';
import { STATE_PATHS } from './catalog';
import { getPath, isForbiddenKey, setPath, type UiState } from './expressions';
import { snapTrigger } from './hirePayload';
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

interface PendingRequest {
  text: string;
  refine: boolean;
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

/** An app a reply names, as the server resolved it, and whether it can
 *  start the work (a new message or email in it). */
const draftAppSchema = appRefSchema.extend({ can_trigger: z.boolean().catch(false) });
export type DraftApp = z.infer<typeof draftAppSchema>;

export interface DraftState {
  status: DraftStatus;
  /** The composer is editing the current draft ("Editing the draft"). */
  refining: boolean;
  /** The composer's text. */
  input: string;
  /** The job as the owner first described it; the draft header shows it. */
  job: string;
  turns: DraftTurn[];
  /** Token of the request in flight, if any, and what it asked. */
  token: string | null;
  request: PendingRequest | null;
  failure: DraftFailure | null;
  source: DraftSource | null;
  /** The latest reply, read and made safe to render. */
  spec: NormalizedSpec | null;
  /** The model's one-sentence introduction of the new employee. */
  intro: string;
  /** What the owner has set on the setup screen (toggles, choices, inputs). */
  uiState: UiState;
  /** Apps the reply names, as the server resolved them: lower-cased name -> ref. */
  apps: Record<string, DraftApp>;
  /** Optional, ordinary-language description of the suggested helpers. */
  team: { responsibility: string }[];
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
  request: null,
  failure: null,
  source: null,
  spec: null,
  intro: '',
  uiState: {},
  apps: {},
  team: [],
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
  team?: unknown;
}

function errorCode(value: unknown): SetupErrorCode {
  return typeof value === 'string' && SERVER_CODES.has(value) ? (value as SetupErrorCode) : 'provider_error';
}

function isTimeout(error: unknown): boolean {
  return error instanceof Error && /timeout/i.test(error.message);
}

function parseApps(raw: unknown): Record<string, DraftApp> {
  const out: Record<string, DraftApp> = {};
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
  for (const [name, value] of Object.entries(raw)) {
    const key = name.trim().toLowerCase();
    const parsed = draftAppSchema.safeParse(value);
    if (parsed.success && !isForbiddenKey(key)) out[key] = parsed.data;
  }
  return out;
}

function parseTeam(raw: unknown): { responsibility: string }[] {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 3).flatMap((member) => {
    if (!member || typeof member !== 'object' || typeof member.responsibility !== 'string') return [];
    const responsibility = member.responsibility.trim().slice(0, 160);
    return responsibility ? [{ responsibility }] : [];
  });
}

/** The apps that can start the work, by every name the reply used for them
 *  (lower-cased) and by their own: name -> the app's own name. */
export function triggerAppNames(apps: Record<string, DraftApp>): Record<string, string> {
  // No prototype: a reply's name for an app is looked up here as a key.
  const names: Record<string, string> = Object.create(null);
  for (const [key, app] of Object.entries(apps)) {
    if (!app.supported || !app.can_trigger) continue;
    names[key] = app.name;
    names[app.name.toLowerCase()] = app.name;
  }
  return names;
}

/** The screen's trigger, unless it names an app that cannot start the
 *  work: then the owner messaging them, which is what Hire would build. */
function fitTrigger(state: UiState, apps: Record<string, DraftApp>): UiState {
  const trigger = snapTrigger(getPath(state, STATE_PATHS.trigger));
  if (trigger.kind !== 'app_event' || (trigger.app && triggerAppNames(apps)[trigger.app.toLowerCase()])) return state;
  return setPath(state, STATE_PATHS.trigger, { kind: 'manual' });
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
  useDraftStore.setState({ status: 'failed', token: null, request: null, failure: { code, ...request } });
  spikeOrb(SPIKE.draftFailed);
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
  const apps = parseApps(response.apps);
  useDraftStore.setState({
    status: 'ready',
    refining: false,
    turns: request.refine ? [...state.turns, turn] : [turn],
    token: null,
    request: null,
    failure: null,
    source: { provider: response.provider ?? null, model: response.model ?? null },
    spec,
    intro: parsed.text.trim(),
    uiState: fitTrigger(spec.state, apps),
    apps,
    team: parseTeam(response.team),
    version: state.version + 1,
  });
  spikeOrb(SPIKE.draftReady);
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
      ? { status: 'working', refining: false, input: '', token, request, failure: null }
      : { ...INITIAL, status: 'working', job, token, request, version: state.version },
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

/** Stop the setup being written and hand the owner's words back to the
 *  composer: a new job, or a change with the last version still showing. */
export function cancelDraft(send: SendRequest): void {
  const { token, request, turns, version } = useDraftStore.getState();
  if (!token || !request) return;
  if (request.refine && turns.length > 0) {
    useDraftStore.setState({ status: 'ready', token: null, request: null, input: request.text, refining: true });
  } else {
    useDraftStore.setState({ ...INITIAL, input: request.text, version });
  }
  cancelOnServer(send, token);
}

/** Throw the draft away, cancelling a request in flight. The composer
 *  keeps whatever the owner was typing. */
export function discardDraft(send: SendRequest): void {
  const { token, input, hiring, hireKey, version } = useDraftStore.getState();
  // Hiding a draft does not cancel its committed hire. Keep that request's
  // slot and retry identity until its response arrives.
  useDraftStore.setState({ ...INITIAL, input, hiring, hireKey, version: version + 1 });
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

/** Free the hire slot. After a hire that made someone its key goes, so the
 *  same setup hired again is a new employee; after a failure it stays, so a
 *  retry finds whoever the lost attempt made. */
export function endHire(hired = false): void {
  useDraftStore.setState(hired ? { hiring: false, hireKey: null } : { hiring: false });
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
      cancel: () => cancelDraft(send),
      discard: () => discardDraft(send),
      setRefining,
      setInput: setComposerInput,
      setValue: setDraftValue,
    };
  }, [sendRequest]);
}
