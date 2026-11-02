/**
 * Telling the employee what the owner set in an interface it showed
 * (`chat_ui_state`, docs-internal/chat_protocol.md). Changes to one
 * interface coalesce per state path (the last value wins) and go out 300 ms
 * after the owner stops, when the page hides, when the chat closes, and
 * before a press of one of its buttons goes. The server keeps them on the
 * reply, so a reload shows them, and tells the employee on its next turn.
 *
 * Best effort: a change that does not reach the server is sent again with
 * the next one.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

/** One value the owner set, as json-render reports it (its `onStateChange`). */
export interface UiStateChange {
  path: string;
  value: unknown;
}

/** How long the owner pauses before changes go. */
export const UI_STATE_IDLE_MS = 300;

export interface UiStateSync {
  /** Record changes to one interface's state. */
  change: (partId: string, changes: UiStateChange[]) => void;
  /** Send what is waiting now: for one interface, or for all. */
  flush: (partId?: string) => void;
}

export function useUiStateSync(sessionId: string): UiStateSync {
  const { sendRequest } = useWebSocketActions();
  const pending = useRef(new Map<string, Map<string, unknown>>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const send = useCallback(
    (partId: string) => {
      const waiting = pending.current.get(partId);
      if (!waiting || waiting.size === 0) return;
      pending.current.delete(partId);
      const changes = [...waiting].map(([path, value]) => ({ path, value }));
      sendRequest('chat_ui_state', { session_id: sessionId, part_id: partId, changes }).catch(() => {
        // Not lost: what failed goes again with the next change, unless a
        // newer value for the same path is already waiting.
        const again = pending.current.get(partId) ?? new Map<string, unknown>();
        for (const { path, value } of changes) if (!again.has(path)) again.set(path, value);
        pending.current.set(partId, again);
      });
    },
    [sessionId, sendRequest],
  );

  const flush = useCallback(
    (partId?: string) => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      if (partId !== undefined) send(partId);
      for (const id of [...pending.current.keys()]) send(id);
    },
    [send],
  );

  const change = useCallback(
    (partId: string, changes: UiStateChange[]) => {
      if (changes.length === 0) return;
      const waiting = pending.current.get(partId) ?? new Map<string, unknown>();
      for (const { path, value } of changes) {
        waiting.delete(path);
        waiting.set(path, value);
      }
      pending.current.set(partId, waiting);
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        flush();
      }, UI_STATE_IDLE_MS);
    },
    [flush],
  );

  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden') flush();
    };
    document.addEventListener('visibilitychange', onHide);
    return () => {
      document.removeEventListener('visibilitychange', onHide);
      flush();
    };
  }, [flush]);

  return useMemo(() => ({ change, flush }), [change, flush]);
}
