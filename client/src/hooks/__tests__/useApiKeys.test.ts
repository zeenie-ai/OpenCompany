/**
 * Tests for useApiKeys hook.
 *
 * Locks in invariants 1, 6 from docs-internal/credentials_panel.md:
 *   - validate/save/delete route to the correct WebSocket message types
 *   - Google Maps and Apify use dedicated validators (not generic validate_api_key)
 *   - Provider Defaults uses save_provider_defaults, NOT save_api_key
 *   - Failure paths return {isValid: false, error} without throwing
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

import { useApiKeys } from '../useApiKeys';
import {
  makeProviderDefaults,
  makeModelConstraints,
  makeProviderUsage,
} from '../../test/builders';

// Mock the WebSocketContext module before importing the hook -- vi.mock is hoisted.
const wsMock = {
  isConnected: true,
  sendRequest: vi.fn(),
  validateApiKey: vi.fn(),
  getStoredApiKey: vi.fn(),
  saveApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
  validateMapsKey: vi.fn(),
  validateApifyKey: vi.fn(),
  getAiModels: vi.fn(),
};

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
}));

beforeEach(() => {
  Object.values(wsMock).forEach((v) => {
    if (typeof v === 'function' && 'mockReset' in v) {
      (v as ReturnType<typeof vi.fn>).mockReset();
    }
  });
  // Default resolutions
  wsMock.validateApiKey.mockResolvedValue({ valid: true, models: ['gpt-4'] });
  wsMock.getStoredApiKey.mockResolvedValue({ hasKey: false });
  wsMock.saveApiKey.mockResolvedValue(true);
  wsMock.deleteApiKey.mockResolvedValue(true);
  wsMock.validateMapsKey.mockResolvedValue({ valid: true });
  wsMock.validateApifyKey.mockResolvedValue({ valid: true });
  wsMock.getAiModels.mockResolvedValue(['gpt-4', 'gpt-3.5']);
  wsMock.sendRequest.mockResolvedValue({});
  wsMock.isConnected = true;
});


describe('useApiKeys.validateApiKey', () => {
  it('routes to validateApiKey with provider+key and returns models', async () => {
    const { result } = renderHook(() => useApiKeys());
    let validation: any;
    await act(async () => {
      validation = await result.current.validateApiKey('openai', 'sk-foo');
    });

    expect(wsMock.validateApiKey).toHaveBeenCalledWith('openai', 'sk-foo');
    expect(validation.isValid).toBe(true);
    expect(validation.models).toEqual(['gpt-4']);
  });

  it('toggles isValidating state during the call', async () => {
    let resolveFn!: (v: any) => void;
    wsMock.validateApiKey.mockReturnValue(
      new Promise((res) => {
        resolveFn = res;
      }),
    );

    const { result } = renderHook(() => useApiKeys());
    expect(result.current.isValidating).toBe(false);

    let pending: Promise<unknown>;
    act(() => {
      pending = result.current.validateApiKey('openai', 'sk');
    });

    await waitFor(() => expect(result.current.isValidating).toBe(true));

    await act(async () => {
      resolveFn({ valid: true, models: [] });
      await pending;
    });

    expect(result.current.isValidating).toBe(false);
  });

  it('sets validationError on invalid response', async () => {
    wsMock.validateApiKey.mockResolvedValue({
      valid: false,
      message: 'bad key',
    });
    const { result } = renderHook(() => useApiKeys());
    let validation: any;
    await act(async () => {
      validation = await result.current.validateApiKey('openai', 'sk-bad');
    });
    expect(validation.isValid).toBe(false);
    expect(validation.error).toBe('bad key');
    expect(result.current.validationError).toBe('bad key');
  });

  it('returns {isValid:false, error} on rejection -- never throws', async () => {
    wsMock.validateApiKey.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useApiKeys());

    let validation: any;
    await act(async () => {
      validation = await result.current.validateApiKey('openai', 'sk');
    });

    expect(validation.isValid).toBe(false);
    expect(validation.error).toBe('network down');
  });
});


describe('useApiKeys.validateGoogleMapsKey', () => {
  it('routes to validateMapsKey, NOT validateApiKey', async () => {
    const { result } = renderHook(() => useApiKeys());
    await act(async () => {
      await result.current.validateGoogleMapsKey('AIza-test');
    });

    expect(wsMock.validateMapsKey).toHaveBeenCalledWith('AIza-test');
    expect(wsMock.validateApiKey).not.toHaveBeenCalled();
  });
});


describe('useApiKeys.validateApifyKey', () => {
  it('routes to validateApifyKey, NOT validateApiKey', async () => {
    const { result } = renderHook(() => useApiKeys());
    await act(async () => {
      await result.current.validateApifyKey('apify-token');
    });

    expect(wsMock.validateApifyKey).toHaveBeenCalledWith('apify-token');
    expect(wsMock.validateApiKey).not.toHaveBeenCalled();
  });
});


describe('useApiKeys.saveApiKey', () => {
  it('returns success when WebSocket save succeeds', async () => {
    wsMock.saveApiKey.mockResolvedValue(true);
    const { result } = renderHook(() => useApiKeys());

    let res: any;
    await act(async () => {
      res = await result.current.saveApiKey('telegram', '123:abc');
    });

    expect(wsMock.saveApiKey).toHaveBeenCalledWith('telegram', '123:abc');
    expect(res.isValid).toBe(true);
  });

  it('returns failure when underlying save returns false', async () => {
    wsMock.saveApiKey.mockResolvedValue(false);
    const { result } = renderHook(() => useApiKeys());

    let res: any;
    await act(async () => {
      res = await result.current.saveApiKey('foo', 'bar');
    });

    expect(res.isValid).toBe(false);
    expect(res.error).toBeDefined();
  });
});


describe('useApiKeys.getStoredApiKey / hasStoredKey / getStoredModels', () => {
  it('getStoredApiKey returns null when hasKey=false', async () => {
    wsMock.getStoredApiKey.mockResolvedValue({ hasKey: false });
    const { result } = renderHook(() => useApiKeys());
    let stored: string | null = 'x';
    await act(async () => {
      stored = await result.current.getStoredApiKey('openai');
    });
    expect(stored).toBeNull();
  });

  it('getStoredApiKey returns key when hasKey=true', async () => {
    wsMock.getStoredApiKey.mockResolvedValue({ hasKey: true, apiKey: 'sk-x' });
    const { result } = renderHook(() => useApiKeys());
    let stored: string | null = null;
    await act(async () => {
      stored = await result.current.getStoredApiKey('openai');
    });
    expect(stored).toBe('sk-x');
  });

  it('hasStoredKey returns the hasKey boolean', async () => {
    wsMock.getStoredApiKey.mockResolvedValue({ hasKey: true });
    const { result } = renderHook(() => useApiKeys());
    let exists = false;
    await act(async () => {
      exists = await result.current.hasStoredKey('openai');
    });
    expect(exists).toBe(true);
  });

  it('getStoredModels returns models when present', async () => {
    wsMock.getStoredApiKey.mockResolvedValue({
      hasKey: true,
      models: ['gpt-4', 'gpt-3.5'],
    });
    const { result } = renderHook(() => useApiKeys());
    let models: string[] | null = null;
    await act(async () => {
      models = await result.current.getStoredModels('openai');
    });
    expect(models).toEqual(['gpt-4', 'gpt-3.5']);
  });

  it('getStoredModels returns null when no models stored', async () => {
    wsMock.getStoredApiKey.mockResolvedValue({ hasKey: true, models: [] });
    const { result } = renderHook(() => useApiKeys());
    let models: string[] | null = ['x'];
    await act(async () => {
      models = await result.current.getStoredModels('openai');
    });
    expect(models).toBeNull();
  });
});


describe('useApiKeys.removeApiKey', () => {
  it('routes to deleteApiKey', async () => {
    const { result } = renderHook(() => useApiKeys());
    await act(async () => {
      await result.current.removeApiKey('openai');
    });
    expect(wsMock.deleteApiKey).toHaveBeenCalledWith('openai');
  });

  it('does not throw on rejection', async () => {
    wsMock.deleteApiKey.mockRejectedValue(new Error('oops'));
    const { result } = renderHook(() => useApiKeys());
    await act(async () => {
      // Must not throw
      await result.current.removeApiKey('openai');
    });
  });
});


describe('useApiKeys.getProviderDefaults / saveProviderDefaults', () => {
  it('getProviderDefaults uses get_provider_defaults message type', async () => {
    const defaults = makeProviderDefaults({ default_model: 'gpt-5' });
    wsMock.sendRequest.mockResolvedValue({ defaults });

    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getProviderDefaults('openai');
    });

    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_provider_defaults', {
      provider: 'openai',
    });
    expect(got).toEqual(defaults);
  });

  it('getProviderDefaults returns sensible fallback when disconnected', async () => {
    wsMock.isConnected = false;
    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getProviderDefaults('openai');
    });
    expect(got.temperature).toBe(0.7);
    expect(got.max_tokens).toBe(4096);
    expect(wsMock.sendRequest).not.toHaveBeenCalled();
  });

  it('saveProviderDefaults uses save_provider_defaults, NOT save_api_key', async () => {
    wsMock.sendRequest.mockResolvedValue({ success: true });
    const { result } = renderHook(() => useApiKeys());

    const defaults = makeProviderDefaults({ temperature: 0.3 });
    let success = false;
    await act(async () => {
      success = await result.current.saveProviderDefaults('openai', defaults);
    });

    expect(success).toBe(true);
    expect(wsMock.sendRequest).toHaveBeenCalledWith('save_provider_defaults', {
      provider: 'openai',
      defaults,
    });
    expect(wsMock.saveApiKey).not.toHaveBeenCalled();
  });
});


describe('useApiKeys.getModelConstraints', () => {
  it('returns response from get_model_constraints', async () => {
    const constraints = makeModelConstraints({ context_length: 1_000_000 });
    wsMock.sendRequest.mockResolvedValue(constraints);

    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getModelConstraints('gpt-5', 'openai');
    });

    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_model_constraints', {
      model: 'gpt-5',
      provider: 'openai',
    });
    expect(got.context_length).toBe(1_000_000);
  });

  it('returns fallback constraints when disconnected', async () => {
    wsMock.isConnected = false;
    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getModelConstraints('gpt-4', 'openai');
    });
    expect(got.found).toBe(false);
    expect(got.max_output_tokens).toBe(4096);
  });
});


describe('useApiKeys.getProviderUsageSummary / getAPIUsageSummary', () => {
  it('getProviderUsageSummary uses get_provider_usage_summary', async () => {
    wsMock.sendRequest.mockResolvedValue({
      providers: [makeProviderUsage()],
    });
    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getProviderUsageSummary();
    });

    expect(wsMock.sendRequest).toHaveBeenCalledWith(
      'get_provider_usage_summary',
      {},
    );
    expect(got).toHaveLength(1);
    expect(got[0].provider).toBe('openai');
  });

  it('getAPIUsageSummary passes service filter', async () => {
    wsMock.sendRequest.mockResolvedValue({ success: true, services: [] });
    const { result } = renderHook(() => useApiKeys());
    await act(async () => {
      await result.current.getAPIUsageSummary('twitter');
    });
    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_api_usage_summary', {
      service: 'twitter',
    });
  });

  it('returns [] when disconnected', async () => {
    wsMock.isConnected = false;
    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getProviderUsageSummary();
    });
    expect(got).toEqual([]);
  });
});


describe('useApiKeys.getValidatedAiProviders / saveGlobalModel', () => {
  it('getValidatedAiProviders uses get_validated_ai_providers', async () => {
    wsMock.sendRequest.mockResolvedValue({
      providers: [],
      global_provider: 'openai',
      global_model: 'gpt-4',
    });
    const { result } = renderHook(() => useApiKeys());
    let got: any;
    await act(async () => {
      got = await result.current.getValidatedAiProviders();
    });
    expect(wsMock.sendRequest).toHaveBeenCalledWith(
      'get_validated_ai_providers',
      {},
    );
    expect(got.global_provider).toBe('openai');
  });

  it('saveGlobalModel uses save_global_model', async () => {
    wsMock.sendRequest.mockResolvedValue({ success: true });
    const { result } = renderHook(() => useApiKeys());
    let ok = false;
    await act(async () => {
      ok = await result.current.saveGlobalModel('openai', 'gpt-5');
    });
    expect(ok).toBe(true);
    expect(wsMock.sendRequest).toHaveBeenCalledWith('save_global_model', {
      provider: 'openai',
      model: 'gpt-5',
    });
  });
});
