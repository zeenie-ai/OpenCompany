/**
 * useCredentialPanel — single hook for credential panel state.
 *
 * The "what is stored on the server" source of truth is a TanStack
 * Query keyed by provider (`queryKeys.credentialValues.byProvider(id)`).
 * Visible in the React Query devtools so per-provider state can be
 * inspected at runtime.
 *
 * `values` / `stored` are derived from the query data — never held in a
 * separate useState copy, so there is one source of truth.
 *
 * `loading` / `error` stay as local useState since they are transient UI
 * state for in-flight user actions (validate / save / delete), not
 * server-cached data.
 *
 * `execute(key, fn)` wraps try/catch/loading/error uniformly so every
 * panel action has the same ergonomics.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useApiKeys } from '../../hooks/useApiKeys';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { queryKeys, STALE_TIME } from '../../lib/queryConfig';
import type { ProviderConfig } from './types';

export type CredentialFormValues = Record<string, string>;

const EMPTY_VALUES: CredentialFormValues = {};

export function useCredentialPanel(config: ProviderConfig, visible: boolean) {
  const qc = useQueryClient();
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stored, setStored] = useState(false);
  // Device-flow one-time code from the login response (RFC 8628 —
  // GitHub et al. require the user to TYPE it on the verification
  // page, so the panel must display it).
  const [verificationCode, setVerificationCode] = useState<string | null>(null);

  // Reset transient UI state when the user switches providers so a
  // "Bot Token required" error from Telegram doesn't bleed into the
  // Google Workspace panel, etc. `stored` is re-seeded from the
  // credentialValuesQuery below.
  useEffect(() => {
    setError(null);
    setLoading(null);
    setStored(false);
    setVerificationCode(null);
  }, [config.id]);

  const {
    validateApiKey, saveApiKey, removeApiKey,
    getProviderDefaults, saveProviderDefaults,
    getProviderUsageSummary, getAPIUsageSummary, getStoredModels, getModelConstraints,
    isConnected,
  } = useApiKeys();
  const { sendRequest } = useWebSocket();

  // Server-cached credential values. Single RPC per field consolidates
  // the previous hasStoredKey + getStoredApiKey pair (both went to
  // ``get_stored_api_key`` anyway). The handler returns
  // ``{hasKey, apiKey?}``; ``apiKey`` may carry the catalogue's
  // ``default`` (e.g. local-LLM canonical Base URL) even when
  // ``hasKey: false``, so the form renders a sensible value on a fresh
  // install. ``hadStored`` tracks the real server state separately so
  // the validated/connected badge stays honest — pre-filled defaults
  // do NOT flip it to true.
  const credentialValuesQuery = useQuery<{ values: CredentialFormValues; hadStored: boolean }, Error>({
    queryKey: queryKeys.credentialValues.byProvider(config.id).queryKey,
    queryFn: async () => {
      if (!config.fields) return { values: EMPTY_VALUES, hadStored: false };
      const results = await Promise.all(
        config.fields.map(async (field) => {
          const storeKey = field.key === 'apiKey' ? config.id : field.key;
          const r = await sendRequest<{ hasKey: boolean; apiKey?: string }>(
            'get_stored_api_key', { provider: storeKey },
          );
          return { key: field.key, hasKey: r.hasKey, apiKey: r.apiKey };
        }),
      );
      const next: CredentialFormValues = {};
      let hadStored = false;
      for (const r of results) {
        if (r.apiKey) next[r.key] = r.apiKey;
        if (r.hasKey) hadStored = true;
      }
      return { values: next, hadStored };
    },
    enabled: visible && isConnected && !!config.fields,
    staleTime: STALE_TIME.FOREVER,
  });

  const values = credentialValuesQuery.data?.values ?? EMPTY_VALUES;

  // Imperative form-like API kept for compat with existing panel code.
  // Writes go through setQueryData on the provider's query so the cache
  // (and devtools) stays in sync; there's no separate useState copy.
  const valuesRef = useRef(values);
  valuesRef.current = values;

  const providerKey = config.id;
  // The query stores `{values, hadStored}` (see queryFn above), NOT a flat
  // CredentialFormValues. Earlier this called
  // `setQueryData<CredentialFormValues>(...)` and the updater spread `prev`
  // as if it were the inner `values` dict — at runtime `prev` was the whole
  // envelope, so typed characters were merged at the envelope level next to
  // `values` / `hadStored` and the input selector (which reads `.values[k]`)
  // never saw them. Net effect: input felt unresponsive even though the
  // setter was firing. Fix: update only the envelope's inner `values` and
  // preserve `hadStored` verbatim.
  const writeValues = useCallback(
    (updater: (prev: CredentialFormValues) => CredentialFormValues) => {
      qc.setQueryData<{ values: CredentialFormValues; hadStored: boolean }>(
        queryKeys.credentialValues.byProvider(providerKey).queryKey,
        (prev) => ({
          values: updater(prev?.values ?? EMPTY_VALUES),
          hadStored: prev?.hadStored ?? false,
        }),
      );
    },
    [qc, providerKey],
  );

  // useMemo keyed on writeValues so form.setFieldValue always targets the
  // current provider's cache. useRef would freeze the closure to the
  // first render's providerKey — breaking every panel opened after the
  // first one (typing would write to the wrong provider, save would read
  // undefined from the right provider).
  const form = useMemo(
    () => ({
      getFieldValue: (key: string): string | undefined => valuesRef.current[key],
      getFieldsValue: (): CredentialFormValues => ({ ...valuesRef.current }),
      setFieldValue: (key: string, value: string) => {
        writeValues((prev) => ({ ...prev, [key]: value }));
      },
      setFieldsValue: (next: CredentialFormValues) => {
        writeValues((prev) => ({ ...prev, ...next }));
      },
      resetFields: () => writeValues(() => EMPTY_VALUES),
    }),
    [writeValues],
  );

  // Sync stored from query on first load — if the backend already has
  // mark stored=true based on the real server state (`hadStored`),
  // NOT on whether the form happens to be populated. Pre-filled
  // catalogue defaults (e.g. local-LLM Base URL) populate the form
  // without being saved, so deriving from `Object.keys(values).length`
  // would prematurely show the connected badge.
  const queriedStored = credentialValuesQuery.data?.hadStored ?? false;
  if (queriedStored && !stored) setStored(true);

  // Generic action executor — replaces 19 duplicate handler functions.
  const execute = useCallback(async (key: string, fn: () => Promise<any>) => {
    setLoading(key);
    setError(null);
    try {
      const result = await fn();
      if (result && !result.success && result.error) {
        setError(result.error);
      }
      return result;
    } catch (err: any) {
      setError(err.message || `Failed: ${key}`);
      return undefined;
    } finally {
      setLoading(null);
    }
  }, []);

  // After every mutation, invalidate the provider's credentialValues
  // query so the loader's queryFn re-runs against the backend — same
  // path as a page reload. Guarantees every observer (Connected badge,
  // Valid pill, ApiKeyInput isStored) reflects the new state.
  const invalidateValues = useCallback(() => {
    return qc.invalidateQueries({
      queryKey: queryKeys.credentialValues.byProvider(providerKey).queryKey,
    });
  }, [qc, providerKey]);

  // Pre-built actions that panels call directly.
  const actions = {
    validate: (id: string, key: string) => execute('validate', async () => {
      const r = await validateApiKey(id, key);
      if (r?.isValid) {
        setStored(true);
        await invalidateValues();
      }
      return r;
    }),
    save: (key: string, value: string) => execute('save', async () => {
      const r = await saveApiKey(key, value);
      if (r?.isValid) {
        setStored(true);
        await invalidateValues();
      }
      return r;
    }),
    remove: (key: string) => execute('remove', async () => {
      await removeApiKey(key);
      setStored(false);
      await invalidateValues();
    }),
    oauthLogin: () => execute('login', async () => {
      const res = await sendRequest(config.ws!.login, {});
      setVerificationCode(res.success ? (res.verification_code ?? null) : null);
      if (res.success && res.url) window.open(res.url, '_blank');
      return res;
    }),
    oauthLogout: () => execute('logout', async () => {
      setVerificationCode(null);
      return sendRequest(config.ws!.logout, {});
    }),
    oauthRefresh: () => execute('refresh', () => sendRequest(config.ws!.status, {})),
    sendWs: (type: string, data?: Record<string, any>) => execute(type, () => sendRequest(type, data ?? {})),
  };

  return {
    form, values, loading, error, stored, setStored, setError,
    verificationCode,
    execute, actions, isConnected,
    getProviderDefaults, saveProviderDefaults,
    getProviderUsageSummary, getAPIUsageSummary,
    getStoredModels, getModelConstraints,
  };
}
