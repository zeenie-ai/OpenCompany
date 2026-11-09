/**
 * A generated interface arriving in a reply (genui/ChatUi.tsx): while the run
 * streams it, a dashed slot below the elements shown so far stands for the
 * ones still to come, and goes once the last has risen in; one read back
 * later, or under reduced motion, shows whole, with no slot. The slot picker
 * sits in a panel, and a toggle's whole row is its label. Colour marks status
 * only (chat V2 colour pass, 4 Nov): a pick, the switch and the primary
 * button are neutral, and a callout colours only its icon.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { REVEAL_STEP_MS } from '@/lib/jsonRender';
import { writePath } from '@/lib/jsonRender/paths';
import { setReducedMotion } from '@/test/waapi';
import ChatUi from '../genui/ChatUi';
import { prepareChatState } from '../genui/prepare';

// The real one, watched: what the owner set reaches json-render through it.
vi.mock('../genui/prepare', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../genui/prepare')>();
  return { ...actual, prepareChatState: vi.fn(actual.prepareChatState) };
});

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

  it('puts the slot picker in a panel', () => {
    render(<ChatUi partId="ui_1" spec={SPEC} live={false} actions={ACTIONS} />);
    const picker = screen.getByRole('radiogroup', { name: 'Saturday 28 Sep' });
    expect(picker).toHaveClass('bg-bg-panel', 'border-border-default', 'p-3.5');
  });

  it('shows what the owner picked without an action colour', () => {
    render(<ChatUi partId="ui_1" spec={SPEC} live={false} actions={ACTIONS} />);
    const picked = screen.getByRole('radio', { name: /14:30/ });
    expect(picked).toHaveAttribute('aria-checked', 'true');
    expect(picked).toHaveClass('bg-bg-active', 'border-fg-default', 'text-fg-default');
    expect(picked.className).not.toMatch(/action-tools/);
    expect(within(picked).getByText('Ana')).toHaveClass('text-fg-default');
    expect(within(picked).getByText('Best')).toHaveClass('bg-bg-active', 'border-border-default', 'text-fg-muted');

    const service = screen.getByRole('radio', { name: 'Colour' });
    expect(service).toHaveAttribute('data-state', 'on');
    expect(service).toHaveClass('data-[state=on]:border-border-strong', 'data-[state=on]:bg-bg-active', 'data-[state=on]:text-fg-default');
    expect(service.className).not.toMatch(/node-agent/);

    const remind = screen.getByRole('switch', { name: 'Remind her the day before' });
    expect(remind).toHaveClass('data-checked:border-fg-default', 'data-checked:bg-fg-default');
    expect(remind.className).not.toMatch(/action-run/);
  });

  it('inverts the primary button, and leaves the other quiet', () => {
    render(<ChatUi partId="ui_1" spec={SPEC} live={false} actions={ACTIONS} />);
    const hold = screen.getByRole('button', { name: 'Hold this slot for 15 min' });
    expect(hold).toHaveAttribute('data-variant', 'invert');
    expect(hold).not.toHaveClass('action-btn');
    expect(screen.getByRole('button', { name: 'Show Sunday instead' })).toHaveAttribute('data-variant', 'quiet');
  });

  it('shows a callout on a neutral panel, with only its icon in the tone', async () => {
    render(<ChatUi partId="ui_1" spec={SPEC} live={false} actions={ACTIONS} />);
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Highlights' }));
    const note = await screen.findByRole('note');
    expect(note).toHaveClass('bg-bg-panel', 'border-border-default');
    expect(note.className).not.toMatch(/action-/);
    expect(note.querySelector('svg')).toHaveClass('text-action-config-ink');
  });

  it('gives a danger callout the destructive icon, and an info or success one a muted icon', () => {
    const spec = {
      root: 'root',
      state: {},
      elements: {
        root: { type: 'Stack', props: { gap: 'md' }, children: ['danger', 'info', 'success'] },
        danger: { type: 'Callout', props: { tone: 'danger', text: 'The card was declined.' } },
        info: { type: 'Callout', props: { tone: 'info', text: 'Ana works on Saturdays.' } },
        success: { type: 'Callout', props: { tone: 'success', text: 'Booked for 14:30.' } },
      },
    };
    render(<ChatUi partId="ui_2" spec={spec} live={false} actions={ACTIONS} />);
    for (const [text, ink] of [
      ['The card was declined.', 'text-destructive'],
      ['Ana works on Saturdays.', 'text-fg-muted'],
      ['Booked for 14:30.', 'text-fg-muted'],
    ]) {
      const note = screen.getByText(text).closest('[role="note"]')!;
      expect(note).toHaveClass('bg-bg-panel', 'border-border-default');
      expect(note.querySelector('svg')).toHaveClass(ink);
    }
  });

  it('toggles a switch from anywhere on its row', () => {
    render(<ChatUi partId="ui_1" spec={SPEC} live={false} actions={ACTIONS} />);
    const toggle = screen.getByRole('switch', { name: 'Remind her the day before' });
    expect(toggle).toHaveAttribute('data-size', 'md');
    expect(toggle).toBeChecked();
    fireEvent.click(screen.getByText('Remind her the day before'));
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
  });

  /** Where the fixture's reminder switch keeps its value. */
  const REMIND_PATH = (
    Object.values(SPEC.elements) as { type: string; props: { label?: string; checked?: { $bindState: string } } }[]
  ).find((element) => element.type === 'Toggle' && element.props.label === 'Remind her the day before')!.props.checked!.$bindState;

  it('reports what the owner sets, and starts from what they set before', () => {
    const onStateChange = vi.fn();
    const saved = writePath({}, REMIND_PATH, false);
    render(<ChatUi partId="ui_1" spec={SPEC} state={saved} live={false} actions={ACTIONS} onStateChange={onStateChange} />);
    // Made safe the way a spec's own state is.
    expect(prepareChatState).toHaveBeenCalledWith(saved);
    const toggle = screen.getByRole('switch', { name: 'Remind her the day before' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(onStateChange).toHaveBeenCalledWith([{ path: REMIND_PATH, value: true }]);
  });
});
