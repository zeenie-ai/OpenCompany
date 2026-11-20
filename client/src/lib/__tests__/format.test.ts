import { describe, expect, it } from 'vitest';
import { usd } from '../format';

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
