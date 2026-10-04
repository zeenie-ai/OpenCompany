/**
 * Following a chat session's runs.
 *
 * `useChatRunSubscription(sessionId)` subscribes the socket to the session
 * (`chat_subscribe`) whenever it is ready, so after every reconnect, and
 * again whenever the run store asks for a fresh snapshot (a gap, a new
 * server process, the hub's resync). Runs that ended while nobody watched
 * are read once more (`get_chat_run`). The last component following a
 * session unsubscribes it. A failed subscribe is retried with a growing
 * delay.
 *
 * `useSessionRuns` / `useLaneRun` / `useRunsSubscribed` read the run store
 * (stores/chatRunStore.ts). `useThreadRunReconcile` reads the runs a thread
 * still calls live that ended before the subscription answered, and a run
 * this tab admitted on its own (a resent message is answered with the run
 * it started before) once the thread says it has ended.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { isLiveRun, snapshotFromWire, type RunSnapshot } from '@/lib/agui/reduceRun';
import { useChatRunStore } from '@/stores/chatRunStore';
import type { ChatMessage } from './schemas';

/** Delays before retrying a failed subscribe; the last one repeats. */
const RETRY_DELAYS_MS = [1_000, 3_000, 10_000];

const EMPTY_RUNS: Record<string, RunSnapshot> = Object.freeze({}) as Record<string, RunSnapshot>;

/** How many mounted components follow each session. */
const following = new Map<string, number>();

type Reply = { success?: boolean; hub_epoch?: unknown; active_runs?: unknown; run?: unknown };

export function useChatRunSubscription(sessionId: string | null): void {
  const { sendRequest, isReady } = useWebSocketActions();
  const resync = useChatRunStore((state) => (sessionId ? state.sessions[sessionId]?.resync ?? 0 : 0));
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!sessionId) return;
    following.set(sessionId, (following.get(sessionId) ?? 0) + 1);
    return () => {
      const left = (following.get(sessionId) ?? 1) - 1;
      if (left > 0) {
        following.set(sessionId, left);
        return;
      }
      following.delete(sessionId);
      sendRequest('chat_unsubscribe', { session_id: sessionId }).catch(() => undefined);
    };
  }, [sessionId, sendRequest]);

  useEffect(() => {
    if (!sessionId || !isReady) return;
    let current = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const failed = () => {
      if (!current) return;
      const delay = RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)];
      retry = setTimeout(() => setAttempt((n) => n + 1), delay);
    };
    void (async () => {
      try {
        const reply = await sendRequest<Reply>('chat_subscribe', { session_id: sessionId });
        if (!current) return;
        if (reply?.success === false || typeof reply?.hub_epoch !== 'string') {
          failed();
          return;
        }
        const snapshots = Array.isArray(reply.active_runs)
          ? reply.active_runs.flatMap((raw) => {
              const run = snapshotFromWire(raw);
              return run ? [run] : [];
            })
          : [];
        const store = useChatRunStore.getState();
        store.applySubscription(sessionId, reply.hub_epoch, snapshots);
        for (const runId of useChatRunStore.getState().sessions[sessionId]?.stale ?? []) {
          const one = await sendRequest<Reply>('get_chat_run', { run_id: runId });
          const run = one?.success === false ? null : snapshotFromWire(one?.run);
          if (current && run) useChatRunStore.getState().upsertRun(run);
        }
      } catch {
        failed();
      }
    })();
    return () => {
      current = false;
      if (retry !== undefined) clearTimeout(retry);
    };
  }, [sessionId, isReady, resync, attempt, sendRequest]);
}

/** A session's runs by id (live and recently ended). */
export function useSessionRuns(sessionId: string | null): Record<string, RunSnapshot> {
  return useChatRunStore((state) => (sessionId ? state.sessions[sessionId]?.runs ?? EMPTY_RUNS : EMPTY_RUNS));
}

/** The run holding the session's lane: the live run the owner's last
 *  message started (approved sends running as `resume` runs never hold it). */
export function laneRun(runs: Record<string, RunSnapshot>): RunSnapshot | null {
  let lane: RunSnapshot | null = null;
  for (const run of Object.values(runs)) {
    if (!isLiveRun(run) || run.kind === 'resume') continue;
    if (!lane || (run.createdAt ?? run.startedAt ?? '') > (lane.createdAt ?? lane.startedAt ?? '')) lane = run;
  }
  return lane;
}

export function useLaneRun(sessionId: string | null): RunSnapshot | null {
  const runs = useSessionRuns(sessionId);
  return useMemo(() => laneRun(runs), [runs]);
}

/** Whether the session's subscription has answered (its runs are known). */
export function useRunsSubscribed(sessionId: string | null): boolean {
  return useChatRunStore((state) => (sessionId ? state.sessions[sessionId]?.subscribed ?? false : false));
}

/**
 * Once the subscription has answered, a run the thread read as live but the
 * store does not hold ended in between: read it once, so the thread shows
 * how it ended (a failure keeps its line) instead of guessing.
 */
export function useThreadRunReconcile(sessionId: string | null, messages: readonly ChatMessage[] | undefined): void {
  const { sendRequest } = useWebSocketActions();
  const subscribed = useRunsSubscribed(sessionId);
  const runs = useSessionRuns(sessionId);
  const asked = useRef(new Set<string>());

  useEffect(() => {
    if (!sessionId || !subscribed || !messages) return;
    for (const message of messages) {
      const run = message.run;
      if (!run || asked.current.has(run.runId)) continue;
      const held = runs[run.runId];
      // Live by the thread and never seen here; or held live only because
      // this tab admitted it (no event or snapshot since) while the thread
      // says it ended: no event will come for it.
      const unseen = isLiveRun(run) && !held;
      const endedUnseen = !isLiveRun(run) && held !== undefined && isLiveRun(held) && held.hubEpoch === null && held.seq === 0;
      if (!unseen && !endedUnseen) continue;
      asked.current.add(run.runId);
      sendRequest<Reply>('get_chat_run', { run_id: run.runId })
        .then((one) => {
          const snapshot = one?.success === false ? null : snapshotFromWire(one?.run);
          if (snapshot) useChatRunStore.getState().upsertRun(snapshot);
        })
        .catch(() => {
          // The next thread read says how it ended.
          asked.current.delete(run.runId);
        });
    }
  }, [sessionId, subscribed, messages, runs, sendRequest]);
}
