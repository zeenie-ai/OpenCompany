/**
 * Lock-in tests for `lib/connectionConfig.ts`.
 *
 * The tuning constants here drive the sign-in gate's visible retries and
 * PartySocket's reconnect envelope. Drift in either (a first check minutes
 * away, or WS_CLOSE.NORMAL_CLOSURE off RFC 6455's 1000) would silently
 * regress how fast the app comes back or break the "intentional close"
 * contract.
 */

import { describe, it, expect } from 'vitest';
import { CONNECT_RETRY, WS_CLOSE, WS_RECONNECT } from '../connectionConfig';

describe('CONNECT_RETRY', () => {
  it('checks again after 2, 3, 5 and then every 8 seconds', () => {
    expect(CONNECT_RETRY.DELAYS_S).toEqual([2, 3, 5, 8, 8]);
  });

  it('never waits less than before', () => {
    const delays = CONNECT_RETRY.DELAYS_S;
    for (let i = 1; i < delays.length; i += 1) expect(delays[i]).toBeGreaterThanOrEqual(delays[i - 1]);
  });

  it('holds Connected long enough to read, and offers help from the third attempt', () => {
    expect(CONNECT_RETRY.CONNECTED_HOLD_MS).toBe(1_500);
    expect(CONNECT_RETRY.HELP_FROM_ATTEMPT).toBe(3);
  });
});

describe('WS_RECONNECT', () => {
  it('first reconnect attempt is sub-second', () => {
    // A transient drop reconnects before the overlay has much to say.
    expect(WS_RECONNECT.MIN_DELAY_MS).toBeGreaterThan(0);
    expect(WS_RECONNECT.MIN_DELAY_MS).toBeLessThan(1_000);
  });

  it('reconnect cap is bounded so dev-iteration restarts are visible', () => {
    expect(WS_RECONNECT.MAX_DELAY_MS).toBeGreaterThan(WS_RECONNECT.MIN_DELAY_MS);
    expect(WS_RECONNECT.MAX_DELAY_MS).toBeLessThanOrEqual(15_000);
  });

  it('grow factor is between 1.0 and 2.0 (gentle ramp, not aggressive)', () => {
    expect(WS_RECONNECT.GROW_FACTOR).toBeGreaterThan(1.0);
    expect(WS_RECONNECT.GROW_FACTOR).toBeLessThan(2.0);
  });

  it('enqueue cap is positive and finite', () => {
    expect(WS_RECONNECT.MAX_ENQUEUED_MESSAGES).toBeGreaterThan(0);
    expect(Number.isFinite(WS_RECONNECT.MAX_ENQUEUED_MESSAGES)).toBe(true);
  });
});

describe('WS_CLOSE', () => {
  it('NORMAL_CLOSURE matches RFC 6455 §7.4.1', () => {
    // The code identifies normal closure on the wire. Explicit close(),
    // rather than this numeric value, tells PartySocket to stop retrying.
    expect(WS_CLOSE.NORMAL_CLOSURE).toBe(1000);
  });

  it('NORMAL_CLOSURE is in the 1000-1014 reserved-server range', () => {
    expect(WS_CLOSE.NORMAL_CLOSURE).toBeGreaterThanOrEqual(1000);
    expect(WS_CLOSE.NORMAL_CLOSURE).toBeLessThanOrEqual(1014);
  });
});
