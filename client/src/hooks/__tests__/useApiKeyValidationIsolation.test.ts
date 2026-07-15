import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useApiKeyValidation } from '../useApiKeyValidation';

const { apiKeys, toast } = vi.hoisted(() => ({
  apiKeys: {
    validateApiKey: vi.fn(),
    getStoredApiKey: vi.fn(),
    hasStoredKey: vi.fn(),
    removeApiKey: vi.fn(),
    getStoredModels: vi.fn(),
  },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('../useApiKeys', () => ({ useApiKeys: () => apiKeys }));
vi.mock('sonner', () => ({ toast }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe('useApiKeyValidation request isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiKeys.getStoredModels.mockResolvedValue([]);
  });

  it('ignores stored-key results from a previously selected node/provider', async () => {
    const pendingA = deferred<boolean>();
    const pendingB = deferred<boolean>();
    apiKeys.hasStoredKey.mockImplementation((provider: string) => (
      provider === 'provider-a' ? pendingA.promise : pendingB.promise
    ));
    apiKeys.getStoredModels.mockResolvedValue(['model-b']);
    const onSuccess = vi.fn();

    const view = renderHook(
      ({ provider, requestKey }) => useApiKeyValidation({ provider, requestKey, onSuccess }),
      { initialProps: { provider: 'provider-a', requestKey: 'node-a:provider-a' } },
    );

    view.rerender({ provider: 'provider-b', requestKey: 'node-b:provider-b' });
    await act(async () => {
      pendingA.resolve(true);
      await pendingA.promise;
    });
    expect(onSuccess).not.toHaveBeenCalled();

    await act(async () => {
      pendingB.resolve(true);
      await pendingB.promise;
      await Promise.resolve();
    });
    expect(onSuccess).toHaveBeenCalledOnce();
    expect(onSuccess).toHaveBeenCalledWith(['model-b']);
    expect(view.result.current.status).toBe('valid');
  });

  it('ignores validation completion after request identity changes', async () => {
    apiKeys.hasStoredKey.mockResolvedValue(false);
    const pendingValidation = deferred<{ isValid: boolean; models: string[] }>();
    apiKeys.validateApiKey.mockReturnValue(pendingValidation.promise);
    const onSuccess = vi.fn();

    const view = renderHook(
      ({ provider, requestKey }) => useApiKeyValidation({ provider, requestKey, onSuccess }),
      { initialProps: { provider: 'provider-a', requestKey: 'node-a:provider-a' } },
    );
    await act(async () => { await Promise.resolve(); });

    let validation!: Promise<void>;
    act(() => {
      validation = view.result.current.validate('secret');
    });
    view.rerender({ provider: 'provider-b', requestKey: 'node-b:provider-b' });

    await act(async () => {
      pendingValidation.resolve({ isValid: true, models: ['stale-model'] });
      await validation;
    });

    expect(onSuccess).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalledWith('API key validated successfully!');
    expect(view.result.current.status).toBe('idle');
  });

  it('reports a credential clear as stale after switching nodes', async () => {
    apiKeys.hasStoredKey.mockResolvedValue(false);
    const pendingClear = deferred<void>();
    apiKeys.removeApiKey.mockReturnValue(pendingClear.promise);

    const view = renderHook(
      ({ provider, requestKey }) => useApiKeyValidation({ provider, requestKey }),
      { initialProps: { provider: 'provider-a', requestKey: 'node-a:provider-a' } },
    );
    await act(async () => { await Promise.resolve(); });

    let clearResult!: Promise<boolean>;
    act(() => {
      clearResult = view.result.current.clear();
    });
    view.rerender({ provider: 'provider-b', requestKey: 'node-b:provider-b' });

    let applied = true;
    await act(async () => {
      pendingClear.resolve();
      applied = await clearResult;
    });

    expect(applied).toBe(false);
    expect(toast.success).not.toHaveBeenCalledWith('API key cleared');
    expect(view.result.current.status).toBe('idle');
  });
});
