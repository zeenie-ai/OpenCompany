import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { useApiKeys } from './useApiKeys';

export type ValidationStatus = 'idle' | 'validating' | 'valid' | 'invalid';

interface UseApiKeyValidationProps {
  provider?: string;
  requestKey?: string;
  onSuccess?: (models: string[]) => void;
}

export const useApiKeyValidation = ({ provider, requestKey, onSuccess }: UseApiKeyValidationProps) => {
  const [status, setStatus] = useState<ValidationStatus>('idle');
  const [hasStoredKeyState, setHasStoredKeyState] = useState(false);
  const requestGeneration = useRef(0);
  const requestIdentity = `${requestKey ?? ''}\u0000${provider ?? ''}`;
  const activeRequestIdentity = useRef(requestIdentity);
  // Refs update during render, before effect cleanup. This closes the small
  // window where node A's promise can settle after node B has rendered but
  // before A's effect cleanup increments the generation counter.
  activeRequestIdentity.current = requestIdentity;
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;

  // Use WebSocket-based API key management
  const {
    validateApiKey: wsValidateApiKey,
    getStoredApiKey: wsGetStoredApiKey,
    hasStoredKey: wsHasStoredKey,
    removeApiKey: wsRemoveApiKey,
    getStoredModels: wsGetStoredModels
  } = useApiKeys();

  // Check for existing key on mount
  useEffect(() => {
    const request = ++requestGeneration.current;
    const identity = requestIdentity;
    const isCurrent = () => (
      request === requestGeneration.current
      && activeRequestIdentity.current === identity
    );
    setStatus('idle');
    setHasStoredKeyState(false);
    if (!provider) return () => {
      requestGeneration.current += 1;
    };

    const checkStoredKey = async () => {
      try {
        const hasKey = await wsHasStoredKey(provider);
        if (!isCurrent()) return;
        if (hasKey) {
          setHasStoredKeyState(true);
          setStatus('valid');

          // Try to get models if there's a stored key
          const models = await wsGetStoredModels(provider);
          if (!isCurrent()) return;
          if (models?.length && onSuccessRef.current) {
            onSuccessRef.current(models);
          }
        }
      } catch (error) {
        if (isCurrent()) {
          console.warn('Error checking stored key:', error);
        }
      }
    };

    void checkStoredKey();
    return () => {
      requestGeneration.current += 1;
    };
  }, [provider, requestKey, requestIdentity, wsHasStoredKey, wsGetStoredModels]);

  const validate = useCallback(async (apiKey: string) => {
    if (!provider || !apiKey.trim()) {
      toast.error('Please enter an API key');
      return;
    }

    const request = ++requestGeneration.current;
    const identity = requestIdentity;
    const isCurrent = () => (
      request === requestGeneration.current
      && activeRequestIdentity.current === identity
    );
    setStatus('validating');

    try {
      const result = await wsValidateApiKey(provider, apiKey.trim());
      if (!isCurrent()) return;

      if (result.isValid) {
        setStatus('valid');
        setHasStoredKeyState(true);
        toast.success('API key validated successfully!');

        if (result.models?.length && onSuccessRef.current) {
          onSuccessRef.current(result.models);
        }
      } else {
        setStatus('invalid');
        toast.error(result.error || 'Invalid API key');
      }
    } catch (error: any) {
      if (isCurrent()) {
        setStatus('invalid');
        toast.error(error.message || 'Validation failed');
      }
    }
  }, [provider, requestIdentity, wsValidateApiKey]);

  const clear = useCallback(async () => {
    if (!provider) return false;

    const request = ++requestGeneration.current;
    const identity = requestIdentity;
    const isCurrent = () => (
      request === requestGeneration.current
      && activeRequestIdentity.current === identity
    );
    try {
      await wsRemoveApiKey(provider);
      if (!isCurrent()) return false;
      setHasStoredKeyState(false);
      setStatus('idle');
      toast.success('API key cleared');
      return true;
    } catch (error) {
      if (isCurrent()) {
        toast.error('Failed to clear API key');
      }
      return false;
    }
  }, [provider, requestIdentity, wsRemoveApiKey]);

  const getStoredKey = useCallback(async () => {
    if (!provider) return null;
    const identity = requestIdentity;
    const storedKey = await wsGetStoredApiKey(provider);
    return activeRequestIdentity.current === identity ? storedKey : null;
  }, [provider, requestIdentity, wsGetStoredApiKey]);

  return {
    status,
    hasStoredKey: hasStoredKeyState,
    validate,
    clear,
    getStoredKey,
    isValidating: status === 'validating',
    isValid: status === 'valid',
    isInvalid: status === 'invalid'
  };
};
