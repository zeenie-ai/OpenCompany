/**
 * ApiKeyPanel — generic API key panel for AI providers, search, scrapers, services.
 * Composes: Card header + ApiKeyInput. Config-driven, zero per-provider JSX.
 */

import React from 'react';
import { CheckCircle } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import ApiKeyInput from '../../ui/ApiKeyInput';
import { useCredentialPanel } from '../useCredentialPanel';
import { ProviderDefaultsSection, LlmUsageSection, ApiUsageSection } from '../sections';
import { NodeIcon } from '../../../assets/icons';
import type { ProviderConfig } from '../types';

const ApiKeyPanel: React.FC<{ config: ProviderConfig; visible: boolean }> = ({ config, visible }) => {
  const panel = useCredentialPanel(config, visible);
  const field = config.fields?.[0];

  // Single source of truth: panel.values (server-cached query data,
  // includes backend-served catalogue defaults like local-LLM Base URL).
  // Reactive — when the query resolves the input re-renders. No
  // separate useState/useEffect mirror.
  const inputValue = field ? (panel.values[field.key] ?? '') : '';
  // ``validated`` mirrors panel.stored (real server state). Pre-filled
  // catalogue defaults do NOT flip it to true.
  const validated = panel.stored;

  return (
    <div className="flex flex-col gap-5 p-5">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0 pb-3">
          <div className="flex items-center gap-3">
            <div
              className="rounded-lg bg-tint-soft"
              // currentColor is the provider's brand color;
              // `bg-tint-soft` mixes it against transparent at the
              // canonical alpha (--tint-soft). The icon picks up the
              // same color via NodeIcon.
              style={{ color: config.color }}
            >
              <NodeIcon
                icon={config.iconRef}
                className="h-12 w-12 text-2xl"
              />
            </div>
            <CardTitle className="text-lg">{config.name}</CardTitle>
          </div>
          {validated && (
            <Badge variant="success" className="gap-1">
              <CheckCircle className="h-3 w-3" />
              Connected
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {field && (
            <ApiKeyInput
              value={inputValue}
              onChange={(v) => panel.form.setFieldValue(field.key, v)}
              onSave={() => panel.actions.validate(config.id, inputValue.trim())}
              onDelete={
                validated
                  ? async () => {
                      // Local providers also stored a Base URL under
                      // {field.key} (e.g. `ollama_proxy`) — wipe both
                      // so the modal returns to a clean empty state.
                      await panel.actions.remove(config.id);
                      if (field.key !== 'apiKey') {
                        await panel.actions.remove(field.key);
                      }
                    }
                  : undefined
              }
              placeholder={field.placeholder}
              loading={panel.loading === 'validate'}
              isStored={validated}
              // Local-LLM providers declare a non-`apiKey` field key
              // (e.g. `ollama_proxy`) — the click probes the user's URL
              // and pulls the model list. Cloud providers validate an
              // upstream API key. The verb on the button reflects the
              // semantic difference.
              saveLabel={field.key === 'apiKey' ? 'Validate' : 'Fetch'}
              savedLabel={field.key === 'apiKey' ? 'Valid' : 'Connected'}
            />
          )}
        </CardContent>
      </Card>

      {panel.error && (
        <Alert variant="destructive">
          <AlertDescription>{panel.error}</AlertDescription>
        </Alert>
      )}

      {config.hasDefaults && <ProviderDefaultsSection providerId={config.id} />}
      {config.hasDefaults && <LlmUsageSection providerId={config.id} providerName={config.name} />}
      {config.usageService && <ApiUsageSection service={config.usageService} serviceName={config.name} />}
    </div>
  );
};

export default ApiKeyPanel;
