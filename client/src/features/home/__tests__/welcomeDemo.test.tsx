/**
 * The Welcome step's demo plays on its own clock (80 ms a tick): the job
 * types itself, then each beat follows the last and the demo goes round;
 * clicking a beat starts it; under reduced motion it holds the
 * conversation still, with no clock and no progress bar, and a click shows
 * another beat.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { setReducedMotion } from '@/test/waapi';
import { WelcomeDemo } from '../onboarding/steps/WelcomeDemo';

const JOB = 'Answer WhatsApp messages and take bookings';

const beat = (name: string) => screen.getByRole('button', { name: new RegExp(name) });
const playing = () => screen.getAllByRole('button').find((button) => button.getAttribute('aria-current') === 'step')?.textContent;

function ticks(count: number) {
  act(() => {
    vi.advanceTimersByTime(count * 80);
  });
}

let restoreMotion: () => void = () => {};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
});

afterEach(() => {
  restoreMotion();
  restoreMotion = () => {};
  vi.useRealTimers();
});

describe('the Welcome demo', () => {
  it('types the job a character a tick, then moves on to the setup and the conversation, and round again', () => {
    const { container } = render(<WelcomeDemo name="Jordan" />);
    expect(playing()).toContain('Describe the job');
    ticks(6);
    expect(screen.getByText('Answer')).toBeInTheDocument();
    ticks(JOB.length - 6);
    expect(screen.getByText(JOB)).toBeInTheDocument();
    // The job is written, then a pause before the setup.
    ticks(16);
    expect(playing()).toContain('Check and hire');
    expect(screen.getByText('Receptionist · WhatsApp')).toBeInTheDocument();
    ticks(48);
    expect(playing()).toContain('Talk to them');
    expect(screen.getByText(/Hi Jordan! Here’s a reply for Priya/)).toBeInTheDocument();
    ticks(56);
    expect(playing()).toContain('Describe the job');
    expect(container.querySelector('[data-beat-progress]')).not.toBeNull();
  });

  it('starts a beat when it is clicked', () => {
    render(<WelcomeDemo name="" />);
    ticks(10);
    fireEvent.click(beat('Talk to them'));
    expect(playing()).toContain('Talk to them');
    expect(screen.getByText('Hi Maya!')).toBeInTheDocument();
    // From its start: the whole beat still to play.
    ticks(55);
    expect(playing()).toContain('Talk to them');
    ticks(1);
    expect(playing()).toContain('Describe the job');
  });

  it('holds the conversation still under reduced motion, and shows another beat on a click', () => {
    restoreMotion = setReducedMotion(true);
    const { container } = render(<WelcomeDemo name="Jordan" />);
    expect(playing()).toContain('Talk to them');
    ticks(200);
    expect(playing()).toContain('Talk to them');
    expect(container.querySelector('[data-beat-progress]')).toBeNull();
    fireEvent.click(beat('Describe the job'));
    expect(screen.getByText(JOB)).toBeInTheDocument();
  });
});
