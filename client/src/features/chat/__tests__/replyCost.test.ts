import { describe, expect, it } from 'vitest';
import { costLabel } from '../turns/runCopy';

describe('costLabel', () => {
  it('is the cost of the run that wrote the reply', () => {
    expect(costLabel({ r_1: 0.42, r_2: 3 }, 'r_1')).toBe('$0.42');
  });

  it('says a cost under a cent is under a cent', () => {
    expect(costLabel({ r_1: 0.004 }, 'r_1')).toBe('<$0.01');
  });

  it('is nothing while the costs have not loaded', () => {
    expect(costLabel(undefined, 'r_1')).toBeNull();
  });

  it('is nothing for a reply with no run', () => {
    expect(costLabel({ r_1: 0.42 }, null)).toBeNull();
  });

  it('is nothing for a run that booked no call', () => {
    expect(costLabel({ r_1: 0.42 }, 'r_9')).toBeNull();
  });
});
