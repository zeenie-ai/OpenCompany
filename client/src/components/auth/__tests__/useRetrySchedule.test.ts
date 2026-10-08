/**
 * The Connecting screen's schedule: a countdown to each check (2, 3, 5,
 * then every 8 seconds), Try now checking at once, a failed or rejected
 * check moving to the next attempt, and an answer stopping it all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRetrySchedule } from '../useRetrySchedule';

let answers: boolean[];
const check = vi.fn(async () => answers.shift() ?? false);

/** Let `seconds` pass, then the check they started settle. */
async function seconds(count: number) {
  await act(async () => {
    vi.advanceTimersByTime(count * 1000);
  });
  await act(async () => {});
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  answers = [];
  check.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useRetrySchedule', () => {
  it('counts down to each check, waiting 2, 3, 5 and then 8 seconds', async () => {
    const { result } = renderHook(() => useRetrySchedule(check));
    expect(result.current).toMatchObject({ attempt: 1, wait: 2, checking: false });
    await seconds(1);
    expect(result.current.wait).toBe(1);
    await seconds(1);
    expect(check).toHaveBeenCalledTimes(1);
    expect(result.current).toMatchObject({ attempt: 2, wait: 3, checking: false });
    await seconds(3);
    expect(result.current).toMatchObject({ attempt: 3, wait: 5 });
    await seconds(5);
    expect(result.current).toMatchObject({ attempt: 4, wait: 8 });
    await seconds(8);
    await seconds(8);
    expect(result.current).toMatchObject({ attempt: 6, wait: 8 });
    expect(check).toHaveBeenCalledTimes(5);
  });

  it('checks at once on Try now', async () => {
    const { result } = renderHook(() => useRetrySchedule(check));
    await act(async () => {
      result.current.tryNow();
    });
    expect(check).toHaveBeenCalledTimes(1);
    expect(result.current).toMatchObject({ attempt: 2, wait: 3 });
  });

  it('stops once the server answers', async () => {
    answers = [false, true];
    const { result } = renderHook(() => useRetrySchedule(check));
    await seconds(2);
    await seconds(3);
    expect(result.current).toMatchObject({ found: true, checking: false, attempt: 2 });
    await seconds(30);
    expect(check).toHaveBeenCalledTimes(2);
  });

  it('counts a check that fails outright as a failed attempt', async () => {
    check.mockRejectedValueOnce(new Error('network'));
    const { result } = renderHook(() => useRetrySchedule(check));
    await seconds(2);
    expect(result.current).toMatchObject({ attempt: 2, checking: false, found: false });
  });
});
