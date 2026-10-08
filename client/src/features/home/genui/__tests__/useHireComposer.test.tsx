/**
 * The composer's binding: a chip's job goes in the box to be read, changed
 * and sent, never sent for the owner; "no AI model" opens the guided
 * connect dialog; and once a model is connected the draft that waited for
 * one is sent again, once.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const sendRequest = vi.fn();
const connectors = { hasAi: false, isLoading: false };

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../../data/connectors', () => ({
  useConnectors: () => ({ providers: [], categories: [], connectedApps: [], ...connectors }),
}));

import corpus from '../__fixtures__/replies.json';
import { useHomeStore } from '../../state/homeStore';
import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import { resetDraftForTests, useDraftStore } from '../draftStore';
import { useHireComposer, useJobComposer, useSendJob } from '../useHireComposer';

const GOOD_REPLY = (corpus as unknown as { name: string; reply: string }[]).find((c) => c.name === 'clean minified reply')!.reply;

beforeEach(() => {
  sendRequest.mockReset();
  resetDraftForTests();
  connectors.hasAi = false;
  connectors.isLoading = false;
  useHomeStore.setState({ composerFocus: 0, view: { kind: 'hire' } });
  useShellDialogsStore.setState({ credentialsOpen: false });
});

async function submit(result: { current: ReturnType<typeof useHireComposer> }, job: string) {
  act(() => result.current.onChange(job));
  await act(async () => result.current.onSubmit());
}

describe('useHireComposer', () => {
  it('puts a chip’s job in the box without sending it', () => {
    const { result } = renderHook(() => useHireComposer());
    act(() => result.current.pick('A receptionist who answers WhatsApp'));
    expect(useDraftStore.getState()).toMatchObject({ input: 'A receptionist who answers WhatsApp', refining: false });
    expect(useHomeStore.getState().composerFocus).toBe(1);
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('opens the guided dialog when there is no AI model, and sends the draft once one is connected', async () => {
    sendRequest.mockResolvedValue({ success: false, error: 'no_ai_provider' });
    const { result, rerender } = renderHook(() => useHireComposer());
    await submit(result, 'Answer my WhatsApp');
    expect(useDraftStore.getState().failure?.code).toBe('no_ai_provider');
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });

    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    connectors.hasAi = true;
    await act(async () => rerender());
    expect(sendRequest).toHaveBeenCalledTimes(2);
    expect(sendRequest.mock.calls[1][1]).toMatchObject({ job: 'Answer my WhatsApp' });
    expect(useDraftStore.getState().status).toBe('ready');
  });

  it('does not send again when the model it has was already there', async () => {
    connectors.hasAi = true;
    sendRequest.mockResolvedValue({ success: false, error: 'no_ai_provider' });
    const { result, rerender } = renderHook(() => useHireComposer());
    await submit(result, 'Answer my WhatsApp');
    await act(async () => rerender());
    expect(sendRequest).toHaveBeenCalledTimes(1);
    expect(useDraftStore.getState().failure?.code).toBe('no_ai_provider');
  });
});

describe('useJobComposer (the Welcome guide’s box)', () => {
  it('puts a chip’s job in the box without taking Home’s focus', () => {
    const { result } = renderHook(() => useJobComposer());
    act(() => result.current.pick('A receptionist who answers WhatsApp'));
    expect(useDraftStore.getState().input).toBe('A receptionist who answers WhatsApp');
    expect(useHomeStore.getState().composerFocus).toBe(0);
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('sends the box as a new job, never as a change to the current draft', async () => {
    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    const { result } = renderHook(() => useJobComposer());
    useDraftStore.setState({ refining: true });
    act(() => result.current.onChange('Answer my WhatsApp'));
    await act(async () => {
      await result.current.submit();
    });
    expect(sendRequest).toHaveBeenCalledWith('generate_employee_setup', expect.not.objectContaining({ refine: expect.anything() }), expect.any(Number));
    expect(sendRequest.mock.calls[0][1]).toMatchObject({ job: 'Answer my WhatsApp' });
  });

  it('opens nothing on its own when there is no AI model', async () => {
    sendRequest.mockResolvedValue({ success: false, error: 'no_ai_provider' });
    const { result } = renderHook(() => useJobComposer());
    act(() => result.current.onChange('Answer my WhatsApp'));
    await act(async () => {
      await result.current.submit();
    });
    expect(useShellDialogsStore.getState().credentialsOpen).toBe(false);
  });
});

describe('useSendJob (the Welcome guide’s finish)', () => {
  it('sends what the box holds when called, as a new job', async () => {
    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    const { result } = renderHook(() => useSendJob());
    useDraftStore.setState({ input: 'Answer my WhatsApp', refining: true });
    await act(async () => {
      await result.current();
    });
    expect(sendRequest).toHaveBeenCalledWith('generate_employee_setup', expect.not.objectContaining({ refine: expect.anything() }), expect.any(Number));
    expect(sendRequest.mock.calls[0][1]).toMatchObject({ job: 'Answer my WhatsApp' });
  });
});
