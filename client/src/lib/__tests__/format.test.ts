import { describe, expect, it } from 'vitest';
import { monthDay, usd } from '../format';

describe('usd', () => {
  it('shows two decimals', () => {
    expect(usd(0.42)).toBe('$0.42');
    expect(usd(12)).toBe('$12.00');
  });

  it('rounds to the cent', () => {
    expect(usd(1.006)).toBe('$1.01');
  });

  it('says an amount under a cent is under a cent', () => {
    expect(usd(0.004)).toBe('<$0.01');
  });

  it('shows zero as zero', () => {
    expect(usd(0)).toBe('$0.00');
  });
});

describe('monthDay', () => {
  it('shows a server date as that month and day', () => {
    // The reader's locale decides the order ("Nov 1" or "1 Nov").
    const november1 = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(Date.UTC(2026, 10, 1));

    expect(monthDay('2026-11-01')).toBe(november1);
  });
});
