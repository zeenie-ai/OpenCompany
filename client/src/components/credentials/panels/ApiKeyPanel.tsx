/**
 * ApiKeyPanel — generic API key panel for AI providers, search, scrapers, services.
 * Composes: Card header + ApiKeyInput. Config-driven, zero per-provider JSX.
 */

import React from 'react';
import { CheckCircle } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ActionButton } from '@/components/ui/action-button';
import ApiKeyInput from '../../ui/ApiKeyInput';
import { useCredentialPanel } from '../useCredentialPanel';
import { ProviderDefaultsSection, LlmUsageSection, ApiUsageSection } from '../sections';
import { NodeIcon } from '../../../assets/icons';
import type { ProviderConfig } from '../types';

const ApiKeyPanel: React.FC<{ config: ProviderConfig; visible: boolean }> = ({ config, visible }) => {
  const panel = useCredentialPanel(config, visible);
  // Primary credential field (validate / connect target — bot token,
  // api key, etc.). Secondary fields (telegram_owner_chat_id,
  // optional metadata) render below as plain text inputs with their
  // own save button — they don't go through validate.
  const field = config.fields?.[0];
  const secondaryFields = (config.fields ?? []).slice(1);

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
            {/* AQ.-prefixed keys route to the Vertex backend — show the
                vertex display name so the user knows which billing path
                is active. */}
            <CardTitle className="text-lg">
              {config.vertexName && inputValue.startsWith('AQ.') ? config.vertexName : config.name}
            </CardTitle>
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
          {field?.help && (
            <p className="mt-2 text-xs text-muted-foreground">{field.help}</p>
          )}
        </CardContent>
      </Card>

      {panel.error && (
        <Alert variant="destructive">
          <AlertDescription>{panel.error}</AlertDescription>
        </Alert>
      )}

      {/* Secondary fields (e.g. telegram_owner_chat_id). Plain text +
          shadcn Input + Save ActionButton — bypasses the validate
          path because these are operator metadata, not credentials
          to probe upstream. Save writes via the same auth_service
          path the primary uses (panel.actions.save). */}
      {secondaryFields.map((sf) => (
        <SecondaryFieldRow
          key={sf.key}
          fieldKey={sf.key}
          label={sf.label}
          placeholder={sf.placeholder}
          help={sf.help}
          secret={sf.secret}
          value={panel.values[sf.key] ?? ''}
          onChange={(v) => panel.form.setFieldValue(sf.key, v)}
          onSave={() => panel.actions.save(sf.key, panel.values[sf.key] ?? '')}
          loading={panel.loading === 'save'}
        />
      ))}

      {config.hasDefaults && <ProviderDefaultsSection providerId={config.id} />}
      {config.hasDefaults && <LlmUsageSection providerId={config.id} providerName={config.name} />}
      {config.usageService && <ApiUsageSection service={config.usageService} serviceName={config.name} />}
    </div>
  );
};

interface SecondaryFieldRowProps {
  fieldKey: string;
  label: string;
  placeholder?: string;
  help?: string;
  secret?: boolean;
  value: string;
  onChange: (v: string) => void;
  onSave: () => void;
  loading: boolean;
}

/** Plain text/password field below the primary credential — for
 *  operator metadata like telegram_owner_chat_id. shadcn Input + Label
 *  + ActionButton; controlled by panel.values, saves via
 *  panel.actions.save which writes through to the credentials DB. */
const SecondaryFieldRow: React.FC<SecondaryFieldRowProps> = ({
  fieldKey, label, placeholder, help, secret, value, onChange, onSave, loading,
}) => (
  <Card>
    <CardContent className="flex flex-col gap-2 pt-4">
      <Label htmlFor={`cred-${fieldKey}`} className="text-sm font-medium">
        {label}
      </Label>
      <div className="flex gap-2">
        <Input
          id={`cred-${fieldKey}`}
          type={secret ? 'password' : 'text'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="flex-1"
        />
        <ActionButton
          intent="save"
          onClick={onSave}
          disabled={loading}
        >
          Save
        </ActionButton>
      </div>
      {help && (
        <p className="text-xs text-muted-foreground">{help}</p>
      )}
    </CardContent>
  </Card>
);

export default ApiKeyPanel;
