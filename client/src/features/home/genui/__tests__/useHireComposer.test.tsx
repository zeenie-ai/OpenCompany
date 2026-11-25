/**
 * The composer's binding: a chip's job goes in the box to be read, changed
 * and sent, never sent for the owner; "no AI model" opens the AI models
 * dialog; once a model is connected the draft that waited for one is sent
 * again, once; and a job goes to the model picked in the hire composer.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { USER_SETTINGS_QUERY_KEY } from '@/hooks/useUserSettingsQuery';

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
import { resetDraftForTests, useDraftStore } from '../draftStore';
import { useHireComposer, useJobComposer, useSendJob } from '../useHireComposer';

const GOOD_REPLY = (corpus as unknown as { name: string; reply: string }[]).find((c) => c.name === 'clean minified reply')!.reply;

let queryClient = new QueryClient();

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  queryClient = new QueryClient();
  sendRequest.mockReset();
  resetDraftForTests();
  connectors.hasAi = false;
  connectors.isLoading = false;
  useHomeStore.setState({ composerFocus: 0, view: { kind: 'hire' }, connectAI: { open: false, provider: null, closeOnConnect: true } });
});

async function submit(result: { current: ReturnType<typeof useHireComposer> }, job: string) {
  act(() => result.current.onChange(job));
  await act(async () => result.current.onSubmit());
}

describe('useHireComposer', () => {
  it('puts a chip’s job in the box without sending it', () => {
    const { result } = renderHook(() => useHireComposer(), { wrapper });
    act(() => result.current.pick('A receptionist who answers WhatsApp'));
    expect(useDraftStore.getState()).toMatchObject({ input: 'A receptionist who answers WhatsApp', refining: false });
    expect(useHomeStore.getState().composerFocus).toBe(1);
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('opens the AI models dialog when there is no AI model, and sends the draft once one is connected', async () => {
    sendRequest.mockResolvedValue({ success: false, error: 'no_ai_provider' });
    const { result, rerender } = renderHook(() => useHireComposer(), { wrapper });
    await submit(result, 'Answer my WhatsApp');
    await waitFor(() => expect(useDraftStore.getState().failure?.code).toBe('no_ai_provider'));
    // Connecting one closes it, so the draft goes on.
    expect(useHomeStore.getState().connectAI).toEqual({ open: true, provider: null, closeOnConnect: true });

    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    connectors.hasAi = true;
    await act(async () => rerender());
    expect(sendRequest).toHaveBeenCalledTimes(2);
    expect(sendRequest.mock.calls[1][1]).toMatchObject({ job: 'Answer my WhatsApp' });
    await waitFor(() => expect(useDraftStore.getState().status).toBe('ready'));
  });

  it('asks the model picked in the hire composer for the setup', async () => {
    queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, { hire_model: 'anthropic::claude-sonnet-5-5', hire_effort: 'high' });
    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    const { result } = renderHook(() => useHireComposer(), { wrapper });
    await submit(result, 'Answer my WhatsApp');
    expect(sendRequest.mock.calls[0][1]).toMatchObject({ job: 'Answer my WhatsApp', model: 'anthropic::claude-sonnet-5-5' });
  });

  it('does not send again when the model it has was already there', async () => {
    connectors.hasAi = true;
    sendRequest.mockResolvedValue({ success: false, error: 'no_ai_provider' });
    const { result, rerender } = renderHook(() => useHireComposer(), { wrapper });
    await submit(result, 'Answer my WhatsApp');
    await act(async () => rerender());
    expect(sendRequest).toHaveBeenCalledTimes(1);
    expect(useDraftStore.getState().failure?.code).toBe('no_ai_provider');
  });
});

describe('useJobComposer (the Welcome guide’s box)', () => {
  it('puts a chip’s job in the box without taking Home’s focus', () => {
    const { result } = renderHook(() => useJobComposer(), { wrapper });
    act(() => result.current.pick('A receptionist who answers WhatsApp'));
    expect(useDraftStore.getState().input).toBe('A receptionist who answers WhatsApp');
    expect(useHomeStore.getState().composerFocus).toBe(0);
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('sends the box as a new job, never as a change to the current draft', async () => {
    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    const { result } = renderHook(() => useJobComposer(), { wrapper });
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
    const { result } = renderHook(() => useJobComposer(), { wrapper });
    act(() => result.current.onChange('Answer my WhatsApp'));
    await act(async () => {
      await result.current.submit();
    });
    expect(useHomeStore.getState().connectAI.open).toBe(false);
  });
});

describe('useSendJob (the Welcome guide’s finish)', () => {
  it('sends what the box holds when called, as a new job', async () => {
    sendRequest.mockResolvedValue({ success: true, reply: GOOD_REPLY });
    const { result } = renderHook(() => useSendJob(), { wrapper });
    useDraftStore.setState({ input: 'Answer my WhatsApp', refining: true });
    await act(async () => {
      await result.current();
    });
    expect(sendRequest).toHaveBeenCalledWith('generate_employee_setup', expect.not.objectContaining({ refine: expect.anything() }), expect.any(Number));
    expect(sendRequest.mock.calls[0][1]).toMatchObject({ job: 'Answer my WhatsApp' });
  });
});
