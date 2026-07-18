/**
 * Tests for the ConsolePanel chat-focus effect.
 *
 * Locks the handoff contract: when useAppStore.chatFocusRequest increments
 * while the panel is open, the chat input is focused on the next animation
 * frame. A chatFocusRequest of 0 (initial) or a closed panel never focuses.
 */

import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { render, screen, act } from '@testing-library/react';

// Full-module replace of the WS context (importActual+spread is broken under
// React 19 — see CredentialsModal.test.tsx). ConsolePanel destructures:
// consoleLogs, clearConsoleLogs, terminalLogs, clearTerminalLogs,
// sendChatMessage, chatMessages, clearChatMessages. lib/nodeSpec (imported
// transitively) also pulls useWebSocket but only calls it inside hooks that
// this test never mounts.
const wsMock = {
  consoleLogs: [] as unknown[],
  terminalLogs: [] as unknown[],
  chatMessages: [] as unknown[],
  sendChatMessage: vi.fn().mockResolvedValue(undefined),
  clearConsoleLogs: vi.fn(),
  clearTerminalLogs: vi.fn(),
  clearChatMessages: vi.fn(),
};

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
}));

import ConsolePanel from '../ui/ConsolePanel';
import { useAppStore } from '../../store/useAppStore';

// --- jsdom shims -------------------------------------------------------------

beforeAll(() => {
  // jsdom has no scrollIntoView; ConsolePanel's auto-scroll effects call it.
  Element.prototype.scrollIntoView = vi.fn();

  // Vitest's jsdom env normally provides rAF (pretendToBeVisual); polyfill
  // defensively so the flush helper below always works.
  if (typeof globalThis.requestAnimationFrame === 'undefined') {
    globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) =>
      setTimeout(() => cb(performance.now()), 0)) as typeof requestAnimationFrame;
    globalThis.cancelAnimationFrame = ((id: number) =>
      clearTimeout(id)) as typeof cancelAnimationFrame;
  }
});

/** Resolve after the next animation frame — any rAF scheduled before this
 *  call has already run by the time it resolves. */
const flushAnimationFrame = () =>
  new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ chatFocusRequest: 0 });
});

const renderPanel = (isOpen: boolean) =>
  render(<ConsolePanel isOpen={isOpen} onToggle={vi.fn()} nodes={[]} />);

// ---------------------------------------------------------------------------

describe('ConsolePanel chat focus', () => {
  it('does not focus the chat input on mount (chatFocusRequest is 0)', async () => {
    renderPanel(true);
    await act(async () => {
      await flushAnimationFrame();
    });
    const input = screen.getByPlaceholderText('Type a message...');
    expect(input).not.toHaveFocus();
  });

  it('focuses the chat input when chatFocusRequest increments while open', async () => {
    renderPanel(true);
    const input = screen.getByPlaceholderText('Type a message...');
    expect(input).not.toHaveFocus();

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });

    expect(input).toHaveFocus();
  });

  it('focuses again on a subsequent increment after focus moved elsewhere', async () => {
    renderPanel(true);
    const input = screen.getByPlaceholderText('Type a message...');

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });
    expect(input).toHaveFocus();

    act(() => {
      (input as HTMLInputElement).blur();
    });
    expect(input).not.toHaveFocus();

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });
    expect(input).toHaveFocus();
  });

  it('does not focus when the panel is closed', async () => {
    renderPanel(false);
    const input = screen.getByPlaceholderText('Type a message...');

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });

    expect(input).not.toHaveFocus();
  });
});
