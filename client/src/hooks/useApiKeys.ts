/**
 * useApiKeys Hook - WebSocket-based API key management
 *
 * Provides API key validation, storage, and retrieval via WebSocket.
 * This replaces the REST-based ApiKeyManagerService for real-time operations.
 */

import { useCallback, useState } from 'react';
import { useWebSocket } from '../contexts/WebSocketContext';

export interface ApiKeyValidationResult {
  isValid: boolean;
  error?: string;
  models?: string[];
}

export interface ProviderDefaults {
  default_model: string;
  temperature: number;
  max_tokens: number;
  thinking_enabled: boolean;
  thinking_budget: number;
  reasoning_effort: 'low' | 'medium' | 'high';
  reasoning_format: 'parsed' | 'hidden';
}

export interface ModelUsageSummary {
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost: number;
  output_cost: number;
  cache_cost: number;
  total_cost: number;
  execution_count: number;
}

export interface ProviderUsageSummary {
  provider: string;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_input_cost: number;
  total_output_cost: number;
  total_cache_cost: number;
  total_cost: number;
  execution_count: number;
  models: ModelUsageSummary[];
}

// Model constraints from model registry
export interface ModelConstraints {
  found: boolean;
  model: string;
  provider: string;
  max_output_tokens: number;
  context_length: number;
  temperature_range: [number, number];
  supports_thinking: boolean;
  thinking_type: 'budget' | 'effort' | 'format' | 'none';
  is_reasoning_model: boolean;
}

// API Usage interfaces (for Twitter, Google Maps, etc.)
export interface APIOperationSummary {
  operation: string;
  resource_count: number;
  total_cost: number;
  execution_count: number;
}

export interface APIUsageSummary {
  service: string;
  total_resources: number;
  total_cost: number;
  execution_count: number;
  operations: APIOperationSummary[];
}

export interface ValidatedProvider {
  provider: string;
  /** Backend-served label. Present for every provider the backend can return,
   *  including ones with no chat-model node (xai), so the UI never needs a
   *  local id -> label map that can fall behind `llm_defaults.json`. */
  display_name: string;
  models: string[];
  popular_models: string[];
  /** What a model picker lists for it (the server's rule,
   *  services/llm/connected.py): its curated models, else its stored ones. */
  offered: string[];
  default_model: string | null;
}

export interface GlobalModelState {
  providers: ValidatedProvider[];
  global_provider: string | null;
  global_model: string | null;
}

function normalizeGlobalModelState(response: unknown): GlobalModelState {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    throw new Error('Invalid validated AI providers response');
  }
  const state = response as Record<string, unknown>;
  // Correlated WebSocket handler errors resolve as responses, rather than
  // rejecting the request. They have no provider list to render.
  if (state.success === false || !Array.isArray(state.providers)) {
    throw new Error(typeof state.error === 'string' ? state.error : 'Invalid validated AI providers response');
  }
  const modelList = (value: unknown): string[] => Array.isArray(value)
    ? value.filter((model): model is string => typeof model === 'string' && model.length > 0)
    : [];
  return {
    providers: state.providers.flatMap((value): ValidatedProvider[] => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
      const provider = value as Record<string, unknown>;
      if (typeof provider.provider !== 'string' || !provider.provider) return [];
      return [{
        provider: provider.provider,
        display_name: typeof provider.display_name === 'string' && provider.display_name
          ? provider.display_name : provider.provider,
        models: modelList(provider.models),
        popular_models: modelList(provider.popular_models),
        offered: modelList(provider.offered),
        default_model: typeof provider.default_model === 'string' ? provider.default_model : null,
      }];
    }),
    global_provider: typeof state.global_provider === 'string' ? state.global_provider : null,
    global_model: typeof state.global_model === 'string' ? state.global_model : null,
  };
}

export interface UseApiKeysResult {
  // Validate and store API key
  validateApiKey: (provider: string, apiKey: string) => Promise<ApiKeyValidationResult>;

  // Save API key without validation
  saveApiKey: (provider: string, apiKey: string) => Promise<ApiKeyValidationResult>;

  // Get stored API key
  getStoredApiKey: (provider: string) => Promise<string | null>;

  // Check if API key exists
  hasStoredKey: (provider: string) => Promise<boolean>;

  // Get stored models for a provider
  getStoredModels: (provider: string) => Promise<string[] | null>;

  // Remove stored API key
  removeApiKey: (provider: string) => Promise<void>;

  // Validate Google Maps API key
  validateGoogleMapsKey: (apiKey: string) => Promise<ApiKeyValidationResult>;

  // Validate Apify API key
  validateApifyKey: (apiKey: string) => Promise<ApiKeyValidationResult>;

  // Get AI models for a provider
  getAiModels: (provider: string, apiKey: string) => Promise<string[]>;

  // Provider defaults
  getProviderDefaults: (provider: string) => Promise<ProviderDefaults>;
  saveProviderDefaults: (provider: string, defaults: ProviderDefaults) => Promise<boolean>;

  // Provider usage summary (LLM tokens)
  getProviderUsageSummary: () => Promise<ProviderUsageSummary[]>;

  // API usage summary (Twitter, etc.)
  getAPIUsageSummary: (service?: string) => Promise<APIUsageSummary[]>;

  // Model constraints from registry
  getModelConstraints: (model: string, provider: string) => Promise<ModelConstraints>;

  // Global model selection
  getValidatedAiProviders: () => Promise<GlobalModelState>;
  saveGlobalModel: (provider: string, model: string) => Promise<boolean>;

  // State
  isValidating: boolean;
  validationError: string | null;
  isConnected: boolean;
}

export const useApiKeys = (): UseApiKeysResult => {
  const {
    validateApiKey: wsValidateApiKey,
    getStoredApiKey: wsGetStoredApiKey,
    saveApiKey: wsSaveApiKey,
    deleteApiKey: wsDeleteApiKey,
    validateMapsKey: wsValidateMapsKey,
    validateApifyKey: wsValidateApifyKey,
    getAiModels: wsGetAiModels,
    sendRequest,
    isConnected
  } = useWebSocket();

  const [isValidating, setIsValidating] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  /**
   * Validate API key and store if valid
   */
  const validateApiKey = useCallback(async (
    provider: string,
    apiKey: string
  ): Promise<ApiKeyValidationResult> => {
    setIsValidating(true);
    setValidationError(null);

    try {
      const result = await wsValidateApiKey(provider, apiKey);

      if (!result.valid) {
        setValidationError(result.message || 'Validation failed');
      }

      // A valid key's message ("API key validated", the gateway's allowance)
      // is not an error: panels show `error` as a failure.
      return {
        isValid: result.valid,
        error: result.valid ? undefined : result.message,
        models: result.models
      };
    } catch (error: any) {
      const errorMsg = error.message || 'Validation failed';
      setValidationError(errorMsg);
      return {
        isValid: false,
        error: errorMsg
      };
    } finally {
      setIsValidating(false);
    }
  }, [wsValidateApiKey]);

  /**
   * Save API key without validation (for keys that can't be validated beforehand)
   */
  const saveApiKey = useCallback(async (
    provider: string,
    apiKey: string
  ): Promise<ApiKeyValidationResult> => {
    try {
      const success = await wsSaveApiKey(provider, apiKey);
      return {
        isValid: success,
        error: success ? undefined : 'Failed to save API key'
      };
    } catch (error: any) {
      return {
        isValid: false,
        error: error.message || 'Failed to save API key'
      };
    }
  }, [wsSaveApiKey]);

  /**
   * Get stored API key for a provider
   */
  const getStoredApiKey = useCallback(async (
    provider: string
  ): Promise<string | null> => {
    try {
      const result = await wsGetStoredApiKey(provider);
      return result.hasKey ? (result.apiKey || null) : null;
    } catch (error) {
      console.warn(`Error retrieving API key for ${provider}:`, error);
      return null;
    }
  }, [wsGetStoredApiKey]);

  /**
   * Check if a stored key exists for a provider
   */
  const hasStoredKey = useCallback(async (
    provider: string
  ): Promise<boolean> => {
    try {
      const result = await wsGetStoredApiKey(provider);
      return result.hasKey;
    } catch (error) {
      return false;
    }
  }, [wsGetStoredApiKey]);

  /**
   * Get stored models for a provider
   */
  const getStoredModels = useCallback(async (
    provider: string
  ): Promise<string[] | null> => {
    try {
      // Get models directly from stored API key response (includes models from DB)
      const result = await wsGetStoredApiKey(provider);
      if (result.hasKey && result.models && result.models.length > 0) {
        return result.models;
      }
      return null;
    } catch (error) {
      console.warn(`Error retrieving models for ${provider}:`, error);
      return null;
    }
  }, [wsGetStoredApiKey]);

  /**
   * Remove stored API key
   */
  const removeApiKey = useCallback(async (
    provider: string
  ): Promise<void> => {
    try {
      await wsDeleteApiKey(provider);
    } catch (error) {
      console.warn(`Error removing API key for ${provider}:`, error);
    }
  }, [wsDeleteApiKey]);

  /**
   * Validate Google Maps API key
   */
  const validateGoogleMapsKey = useCallback(async (
    apiKey: string
  ): Promise<ApiKeyValidationResult> => {
    setIsValidating(true);
    setValidationError(null);

    try {
      const result = await wsValidateMapsKey(apiKey);

      if (!result.valid) {
        setValidationError(result.message || 'Validation failed');
      }

      return {
        isValid: result.valid,
        error: result.valid ? undefined : result.message
      };
    } catch (error: any) {
      const errorMsg = error.message || 'Validation failed';
      setValidationError(errorMsg);
      return {
        isValid: false,
        error: errorMsg
      };
    } finally {
      setIsValidating(false);
    }
  }, [wsValidateMapsKey]);

  /**
   * Validate Apify API key
   */
  const validateApifyKey = useCallback(async (
    apiKey: string
  ): Promise<ApiKeyValidationResult> => {
    setIsValidating(true);
    setValidationError(null);

    try {
      const result = await wsValidateApifyKey(apiKey);

      if (!result.valid) {
        setValidationError(result.message || 'Validation failed');
      }

      return {
        isValid: result.valid,
        error: result.valid ? undefined : result.message
      };
    } catch (error: any) {
      const errorMsg = error.message || 'Validation failed';
      setValidationError(errorMsg);
      return {
        isValid: false,
        error: errorMsg
      };
    } finally {
      setIsValidating(false);
    }
  }, [wsValidateApifyKey]);

  /**
   * Get available AI models for a provider
   */
  const getAiModels = useCallback(async (
    provider: string,
    apiKey: string
  ): Promise<string[]> => {
    try {
      return await wsGetAiModels(provider, apiKey);
    } catch (error) {
      console.warn(`Error fetching AI models for ${provider}:`, error);
      return [];
    }
  }, [wsGetAiModels]);

  /**
   * Get default parameters for a provider
   */
  const getProviderDefaults = useCallback(async (
    provider: string
  ): Promise<ProviderDefaults> => {
    const defaultValues: ProviderDefaults = {
      default_model: '',
      temperature: 0.7,
      max_tokens: 4096,
      thinking_enabled: false,
      thinking_budget: 2048,
      reasoning_effort: 'medium',
      reasoning_format: 'parsed',
    };

    if (!isConnected) return defaultValues;

    try {
      const response = await sendRequest<{ defaults: ProviderDefaults }>('get_provider_defaults', { provider });
      return response?.defaults || defaultValues;
    } catch (error) {
      console.warn(`Error fetching provider defaults for ${provider}:`, error);
      return defaultValues;
    }
  }, [sendRequest, isConnected]);

  /**
   * Save default parameters for a provider
   */
  const saveProviderDefaults = useCallback(async (
    provider: string,
    defaults: ProviderDefaults
  ): Promise<boolean> => {
    if (!isConnected) return false;

    try {
      const response = await sendRequest<{ success: boolean }>('save_provider_defaults', { provider, defaults });
      return response?.success || false;
    } catch (error) {
      console.warn(`Error saving provider defaults for ${provider}:`, error);
      return false;
    }
  }, [sendRequest, isConnected]);

  /**
   * Get aggregated usage and cost summary by provider (LLM tokens)
   */
  const getProviderUsageSummary = useCallback(async (): Promise<ProviderUsageSummary[]> => {
    if (!isConnected) return [];

    try {
      const response = await sendRequest<{ providers: ProviderUsageSummary[] }>('get_provider_usage_summary', {});
      return response?.providers || [];
    } catch (error) {
      console.warn('Error fetching provider usage summary:', error);
      return [];
    }
  }, [sendRequest, isConnected]);

  /**
   * Get aggregated API usage and cost by service (Twitter, etc.)
   * Optionally filter by service name.
   */
  const getAPIUsageSummary = useCallback(async (
    service?: string
  ): Promise<APIUsageSummary[]> => {
    if (!isConnected) return [];

    try {
      const response = await sendRequest<{ success: boolean; services: APIUsageSummary[] }>(
        'get_api_usage_summary',
        { service }
      );
      return response?.services || [];
    } catch (error) {
      console.warn('Error fetching API usage summary:', error);
      return [];
    }
  }, [sendRequest, isConnected]);

  /**
   * Get model constraints from the model registry
   */
  const getModelConstraints = useCallback(async (
    model: string,
    provider: string
  ): Promise<ModelConstraints> => {
    const fallback: ModelConstraints = {
      found: false,
      model,
      provider,
      max_output_tokens: 4096,
      context_length: 128000,
      temperature_range: [0, 2],
      supports_thinking: false,
      thinking_type: 'none',
      is_reasoning_model: false,
    };

    if (!isConnected) return fallback;

    try {
      const response = await sendRequest<ModelConstraints>('get_model_constraints', { model, provider });
      return response || fallback;
    } catch (error) {
      console.warn(`Error fetching model constraints for ${provider}/${model}:`, error);
      return fallback;
    }
  }, [sendRequest, isConnected]);

  const getValidatedAiProviders = useCallback(async (): Promise<GlobalModelState> => {
    const fallback: GlobalModelState = { providers: [], global_provider: null, global_model: null };
    if (!isConnected) return fallback;
    try {
      const response = await sendRequest<unknown>('get_validated_ai_providers', {});
      return normalizeGlobalModelState(response);
    } catch (error) {
      console.warn('Error fetching validated AI providers:', error);
      return fallback;
    }
  }, [sendRequest, isConnected]);

  const saveGlobalModel = useCallback(async (provider: string, model: string): Promise<boolean> => {
    if (!isConnected) return false;
    try {
      const response = await sendRequest<{ success: boolean }>('save_global_model', { provider, model });
      return response?.success ?? false;
    } catch (error) {
      console.warn('Error saving global model:', error);
      return false;
    }
  }, [sendRequest, isConnected]);

  return {
    validateApiKey,
    saveApiKey,
    getStoredApiKey,
    hasStoredKey,
    getStoredModels,
    removeApiKey,
    validateGoogleMapsKey,
    validateApifyKey,
    getAiModels,
    getProviderDefaults,
    saveProviderDefaults,
    getProviderUsageSummary,
    getAPIUsageSummary,
    getModelConstraints,
    getValidatedAiProviders,
    saveGlobalModel,
    isValidating,
    validationError,
    isConnected
  };
};
