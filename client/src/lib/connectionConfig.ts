/**
 * Centralised tuning constants for the connection layer: when the sign-in
 * gate checks the server again, PartySocket's reconnect envelope, the app
 * heartbeat and the close codes. A tuning pass is a single-file edit, and
 * tests reference the same values.
 *
 * Pattern mirrors `client/src/lib/queryConfig.ts`'s `STALE_TIME` /
 * `GC_TIME` buckets: named, frozen, JSDoc-explained.
 *
 * Pre-existing constants (REQUEST_TIMEOUT, ping interval) stay in
 * `WebSocketContext.tsx` for now — moving them is out of scope for
 * this change.
 */

/**
 * The Connecting screen (components/auth/ConnectingPanel, onboarding
 * handoff R4). The auth check itself never retries in secret; while the
 * server can't be reached, the gate checks it again after each of these
 * waits in turn, then after the last one every time, counting down where
 * the owner can see it. "Connected" shows for CONNECTED_HOLD_MS before
 * sign-in or the app, and from attempt HELP_FROM_ATTEMPT a line says to
 * make sure OpenCompany is running.
 */
export const CONNECT_RETRY = {
  /** Seconds before each check. */
  DELAYS_S: [2, 3, 5, 8, 8],
  CONNECTED_HOLD_MS: 1_500,
  HELP_FROM_ATTEMPT: 3,
} as const;

/**
 * WebSocket reconnect envelope, consumed by `WebSocketContext`'s
 * `new ReconnectingWebSocket(url, [], {...})` constructor.
 *
 * PartySocket waits MIN_DELAY_MS · GROW_FACTOR^(n-1) before attempt n,
 * capped at MAX_DELAY_MS. There is no jitter: PartySocket randomises only
 * its own default minimum, which MIN_DELAY_MS replaces.
 *
 * Sample reconnect sequence (MIN_DELAY_MS=250, GROW_FACTOR=1.3,
 * MAX_DELAY_MS=8000):
 *
 *     attempt 1 → ~250ms
 *     attempt 2 → ~325ms
 *     attempt 3 → ~422ms
 *     ...
 *     attempt N → capped at 8s
 *
 * Refs:
 *   https://docs.partykit.io/reference/partysocket-api/
 *   https://github.com/cloudflare/partykit/tree/main/packages/partysocket
 */
export const WS_RECONNECT = {
  /** Delay before the first reconnect attempt (ms). */
  MIN_DELAY_MS: 250,
  /** Cap on any single reconnect delay (ms). */
  MAX_DELAY_MS: 8_000,
  /** Multiplier applied to the previous delay each attempt. */
  GROW_FACTOR: 1.3,
  /**
   * Bound on the send-while-disconnected buffer. PartySocket replays
   * these calls on the next OPEN. Mirrors the previous
   * `pendingSendQueueRef` cap intent.
   */
  MAX_ENQUEUED_MESSAGES: 200,
} as const;

/** Application heartbeat, independent of the server's protocol-level ping. */
export const WS_HEARTBEAT = {
  INTERVAL_MS: 30_000,
  TIMEOUT_MS: 10_000,
} as const;

/**
 * WebSocket close codes per RFC 6455 §7.4.1.
 * https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1
 *
 * A remote close (including 1000) may reconnect. Calling the wrapper's
 * close() explicitly stops retries; the event's code does not express
 * whether the application deliberately disposed the connection.
 *
 * Only the subset this app actively sends or branches on is listed.
 * Add new entries here rather than inlining the numeric literal.
 */
export const WS_CLOSE = {
  /**
   * 1000 — Normal Closure. The connection successfully completed the
   * purpose for which it was created. Used for logout + unmount
   * teardown so PartySocket does not reconnect.
   */
  NORMAL_CLOSURE: 1000,
  /** Application-private code used to replace a nonresponsive connection. */
  HEARTBEAT_TIMEOUT: 4000,
} as const;
