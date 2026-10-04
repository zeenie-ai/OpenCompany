/**
 * Chat runs as they happen, per chat session (docs-internal/chat_protocol.md).
 *
 * WebSocketContext hands every `chat_run_event` frame to `receive`; the hook
 * that subscribes a session (features/chat/data/runs.ts) applies the
 * server's snapshots. Frames are folded in once per animation frame, so a
 * burst of streamed text costs one store update.
 *
 * Ordering is the server's `seq`, per run:
 * - a frame at or before a run's `seq` is a duplicate and changes nothing;
 * - a gap, an unknown run that is already under way, a new `hub_epoch` (the
 *   server restarted) or the hub's resync frame mark the session `syncing`
 *   and bump `resync`: the subscription takes a fresh snapshot, and the
 *   frames that arrived meanwhile are held and folded in after it, skipping
 *   what the snapshot already covers. One that still cannot fold after it is
 *   dropped, never asked for again: the snapshot was the server's best;
 * - a run's end (`finished`, `failed`) folds in even across a gap or for a
 *   run not seen before, since its saved reply holds what was missed, and an
 *   ended run takes nothing more. An ended reading of a run beats a live one
 *   whatever their `seq`: the hub forgets a run's `seq` once it ends.
 *
 * Nothing here talks to the socket.
 */

import { create } from 'zustand';
import { parseRunFrame, type RunEvent, type RunFrame } from '@/lib/agui/events';
import { applyRunEvent, emptyRun, isLiveRun, type RunSnapshot } from '@/lib/agui/reduceRun';

export interface SessionRuns {
  /** The server process these runs' `seq` belongs to. */
  hubEpoch: string | null;
  runs: Record<string, RunSnapshot>;
  /** Frames held while a fresh snapshot is on its way. */
  held: RunEvent[];
  /** Bumped to ask the subscription for a fresh snapshot. */
  resync: number;
  syncing: boolean;
  /** A subscription snapshot has arrived: from then on these runs are the
   *  session's live runs, whatever an older thread read says. */
  subscribed: boolean;
  /** Runs live here but absent from the last snapshot: they ended unseen and
   *  need reading again (`get_chat_run`). */
  stale: string[];
}

export interface AdmittedRun {
  runId: string;
  userMessageId: string;
  state: 'pending' | 'queued';
}

interface ChatRunState {
  sessions: Record<string, SessionRuns>;
  /** Queue one `chat_run_event` frame's data; folded in on the next frame. */
  receive: (raw: unknown) => void;
  /** Fold queued frames in now. */
  flush: () => void;
  /** A session's snapshot from `chat_subscribe`. */
  applySubscription: (sessionId: string, hubEpoch: string, snapshots: readonly RunSnapshot[]) => void;
  /** One run read on its own (`get_chat_run`). */
  upsertRun: (snapshot: RunSnapshot) => void;
  /** The run a send just admitted, before its first event arrives. */
  admit: (sessionId: string, run: AdmittedRun) => void;
}

const EMPTY_SESSION: SessionRuns = { hubEpoch: null, runs: {}, held: [], resync: 0, syncing: false, subscribed: false, stale: [] };

function requestResync(session: SessionRuns, held?: RunEvent): SessionRuns {
  return {
    ...session,
    held: held ? [...session.held, held] : session.held,
    syncing: true,
    resync: session.syncing ? session.resync : session.resync + 1,
  };
}

/** Fold one event in. `settling`: a frame held through a resync, folded in
 *  after the fresh snapshot; if it still cannot fold it is dropped. */
function foldEvent(session: SessionRuns, event: RunEvent, settling = false): SessionRuns {
  if (session.syncing) return { ...session, held: [...session.held, event] };
  const ask = () => (settling ? session : requestResync(session, event));
  if (session.hubEpoch !== null && session.hubEpoch !== event.hubEpoch) return ask();
  const known = session.runs[event.runId];
  if (known && !isLiveRun(known)) return session;
  const ends = event.type === 'finished' || event.type === 'failed';
  const base = known ?? (event.seq === 1 || ends ? emptyRun(event.runId, event.sessionId) : null);
  if (!base) return ask();
  if (event.seq <= base.seq) return session;
  if (event.seq > base.seq + 1 && !ends) return ask();
  return {
    ...session,
    hubEpoch: session.hubEpoch ?? event.hubEpoch,
    runs: { ...session.runs, [event.runId]: applyRunEvent(base, event) },
    stale: session.stale.filter((runId) => runId !== event.runId),
  };
}

function foldFrame(sessions: Record<string, SessionRuns>, frame: RunFrame): Record<string, SessionRuns> {
  if (frame.type === 'resync') {
    const session = sessions[frame.sessionId];
    return session ? { ...sessions, [frame.sessionId]: requestResync(session) } : sessions;
  }
  const session = sessions[frame.sessionId] ?? EMPTY_SESSION;
  const next = foldEvent(session, frame);
  return next === session ? sessions : { ...sessions, [frame.sessionId]: next };
}

/** The newer of two readings of one run: an ended one over a live one (a
 *  run never goes live again), else a higher `seq` in the same epoch. */
function newer(local: RunSnapshot | undefined, incoming: RunSnapshot): RunSnapshot {
  if (!local) return incoming;
  if (isLiveRun(local) !== isLiveRun(incoming)) return isLiveRun(incoming) ? local : incoming;
  return local.hubEpoch === incoming.hubEpoch && local.seq > incoming.seq ? local : incoming;
}

// Frames wait here for the next animation frame (a hidden page gets a
// timer instead: browsers do not paint it).
let queue: RunFrame[] = [];
let scheduled = false;

function schedule(flush: () => void): void {
  if (scheduled) return;
  scheduled = true;
  const run = () => {
    scheduled = false;
    flush();
  };
  const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
  if (!hidden && typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
  else setTimeout(run, 0);
}

export const useChatRunStore = create<ChatRunState>((set, get) => ({
  sessions: {},

  receive: (raw) => {
    const frame = parseRunFrame(raw);
    if (!frame) return;
    queue.push(frame);
    schedule(get().flush);
  },

  flush: () => {
    if (queue.length === 0) return;
    const frames = queue;
    queue = [];
    set((state) => {
      let sessions = state.sessions;
      for (const frame of frames) sessions = foldFrame(sessions, frame);
      return sessions === state.sessions ? state : { sessions };
    });
  },

  applySubscription: (sessionId, hubEpoch, snapshots) => {
    get().flush();
    set((state) => {
      const previous = state.sessions[sessionId] ?? EMPTY_SESSION;
      const runs: Record<string, RunSnapshot> = {};
      for (const snapshot of snapshots) {
        const incoming = { ...snapshot, hubEpoch };
        runs[snapshot.runId] = newer(previous.runs[snapshot.runId], incoming);
      }
      const stale: string[] = [];
      for (const [runId, local] of Object.entries(previous.runs)) {
        if (runId in runs) continue;
        runs[runId] = local;
        if (isLiveRun(local)) stale.push(runId);
      }
      let session: SessionRuns = { hubEpoch, runs, held: [], resync: previous.resync, syncing: false, subscribed: true, stale };
      for (const event of previous.held) session = foldEvent(session, event, true);
      return { sessions: { ...state.sessions, [sessionId]: session } };
    });
  },

  upsertRun: (snapshot) =>
    set((state) => {
      const session = state.sessions[snapshot.sessionId] ?? EMPTY_SESSION;
      const run = newer(session.runs[snapshot.runId], snapshot);
      return {
        sessions: {
          ...state.sessions,
          [snapshot.sessionId]: {
            ...session,
            runs: { ...session.runs, [snapshot.runId]: run },
            stale: session.stale.filter((runId) => runId !== snapshot.runId),
          },
        },
      };
    }),

  admit: (sessionId, admitted) =>
    set((state) => {
      const session = state.sessions[sessionId] ?? EMPTY_SESSION;
      if (session.runs[admitted.runId]) return state;
      const run: RunSnapshot = {
        ...emptyRun(admitted.runId, sessionId),
        state: admitted.state,
        userMessageId: admitted.userMessageId,
      };
      return { sessions: { ...state.sessions, [sessionId]: { ...session, runs: { ...session.runs, [admitted.runId]: run } } } };
    }),
}));

/** Drop queued frames and every session (tests). */
export function resetChatRunStore(): void {
  queue = [];
  scheduled = false;
  useChatRunStore.setState({ sessions: {} });
}
