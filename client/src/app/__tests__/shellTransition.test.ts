/**
 * The mode switch fades the current screen out, then swaps. Requests during
 * a running switch coalesce; returning to where it started restores the
 * screen without swapping.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';

vi.mock('../../services/workflowApi', () => ({
  workflowApi: { getAllWorkflows: vi.fn(), getWorkflow: vi.fn(), saveWorkflow: vi.fn(), deleteWorkflow: vi.fn() },
}));

import { useAppStore } from '../../store/useAppStore';
import { SHELL_SCREEN_ATTR, consumeShellEntry, transitionShell } from '../shellTransition';
import { installWaapiStub, setReducedMotion } from '../../test/waapi';

let waapi: ReturnType<typeof installWaapiStub>;
let restoreMotion: () => void;
let screen: HTMLElement;

beforeEach(() => {
  waapi = installWaapiStub();
  restoreMotion = setReducedMotion(false);
  screen = document.createElement('div');
  screen.setAttribute(SHELL_SCREEN_ATTR, 'normal');
  document.body.appendChild(screen);
  useAppStore.setState({ shellMode: 'normal' });
  consumeShellEntry();
});

afterEach(() => {
  waapi.restore();
  restoreMotion();
  screen.remove();
  localStorage.clear();
});

/** Let the pending exit animation's promise callbacks run. */
const flush = () => act(async () => {
  await Promise.resolve();
});

describe('transitionShell', () => {
  it('fades the current screen out, then swaps and marks the entry due', async () => {
    const done = transitionShell('dev');
    expect(waapi.calls).toHaveLength(1);
    expect(waapi.calls[0].target).toBe(screen);
    expect(waapi.calls[0].options).toMatchObject({ duration: 240, fill: 'forwards' });
    expect(useAppStore.getState().shellMode).toBe('normal');

    waapi.calls[0].animation.finish();
    await act(async () => {
      await done;
    });
    expect(useAppStore.getState().shellMode).toBe('dev');
    expect(consumeShellEntry()).toBe(true);
    expect(consumeShellEntry()).toBe(false);
  });

  it('restores the screen when toggled back before the swap', async () => {
    const first = transitionShell('dev');
    const second = transitionShell('normal');
    expect(second).toBe(first);
    const exit = waapi.calls[0].animation;
    exit.finish();
    await act(async () => {
      await first;
    });
    expect(useAppStore.getState().shellMode).toBe('normal');
    expect(exit.playState).toBe('idle');
    expect(consumeShellEntry()).toBe(false);
  });

  it('does nothing when already on the requested screen', async () => {
    await act(async () => {
      await transitionShell('normal');
    });
    expect(waapi.calls).toHaveLength(0);
  });

  it('a request with nothing to do never blocks the next switch', async () => {
    await act(async () => {
      await transitionShell('normal');
    });
    const done = transitionShell('dev');
    expect(waapi.calls).toHaveLength(1);
    waapi.calls[0].animation.finish();
    await act(async () => {
      await done;
    });
    expect(useAppStore.getState().shellMode).toBe('dev');
  });

  it('honours the latest of several requests', async () => {
    const done = transitionShell('dev');
    transitionShell('normal');
    transitionShell('dev');
    waapi.calls[0].animation.finish();
    await flush();
    await act(async () => {
      await done;
    });
    expect(useAppStore.getState().shellMode).toBe('dev');
  });
});
