/**
 * A generated interface arriving in a reply (genui/ChatUi.tsx): while the run
 * streams it, a dashed slot below the elements shown so far stands for the
 * ones still to come, and goes once the last has risen in; one read back
 * later, or under reduced motion, shows whole, with no slot.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { REVEAL_STEP_MS } from '@/lib/jsonRender';
import { setReducedMotion } from '@/test/waapi';
import ChatUi from '../genui/ChatUi';

const SPEC = JSON.parse(readFileSync(join(__dirname, '..', '__fixtures__', 'saturday-booking.spec.json'), 'utf-8'));
const ACTIONS = { ask: vi.fn(), event: vi.fn() };

function slot(container: HTMLElement) {
  return container.querySelector('[data-slot="next"]');
}

describe('ChatUi', () => {
  let restoreMotion: () => void;

  beforeEach(() => {
    restoreMotion = setReducedMotion(false);
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
  });

  afterEach(() => {
    vi.useRealTimers();
    restoreMotion();
  });

  it('keeps a slot for what is still arriving', () => {
    const { container } = render(<ChatUi partId="ui_1" spec={SPEC} live actions={ACTIONS} />);
    expect(slot(container)).not.toBeNull();
    expect(slot(container)?.getAttribute('aria-hidden')).toBe('true');
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 60);
    });
    expect(slot(container)).toBeNull();
  });

  it('shows a reply read back later whole', () => {
    const { container } = render(<ChatUi partId="ui_1" spec={SPEC} live={false} actions={ACTIONS} />);
    expect(slot(container)).toBeNull();
  });

  it('shows everything at once under reduced motion', () => {
    restoreMotion();
    restoreMotion = setReducedMotion(true);
    const { container } = render(<ChatUi partId="ui_1" spec={SPEC} live actions={ACTIONS} />);
    expect(slot(container)).toBeNull();
  });
});
