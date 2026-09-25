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
import { theme } from '../../../styles/theme';
import { CREDENTIAL_PROBE_REQUEST_TIMEOUT } from '@/contexts/WebSocketContext';
import type { ServerEndpointSummary } from '@/hooks/useCatalogueQuery';
import type { ProviderConfig } from '../types';
import type { PanelVariant } from '../PanelRenderer';
import EndpointList from './EndpointList';

const ApiKeyPanel: React.FC<{ config: ProviderConfig; visible: boolean; variant?: PanelVariant }> = ({
  config,
  visible,
  variant = 'full',
}) => {
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
  // A provider that holds several rows (named OpenAI-compatible endpoints)
  // gets them from the catalogue, and its fields become a form adding one.
  const endpoints = config.endpoints;
  // ``validated`` mirrors real server state: panel.stored, or for a
  // several-row provider the catalogue's own flag. Pre-filled catalogue
  // defaults do NOT flip it to true.
  const validated = endpoints ? Boolean(config.stored) : panel.stored;
  const busy = panel.loading !== null;

  // Every field goes out under its catalogue key; the backend Credential
  // decides what each means. A rejected save carries its reason in
  // `message`, which the generic executor does not surface on its own.
  // Both probe the server, so they get the probe budget, not the default.
  const addEndpoint = async () => {
    const res = await panel.actions.sendWs('validate_api_key', {
      provider: config.id,
      api_key: inputValue.trim(),
      ...Object.fromEntries(secondaryFields.map((sf) => [sf.key, String(panel.values[sf.key] ?? '').trim()])),
    }, CREDENTIAL_PROBE_REQUEST_TIMEOUT);
    if (res?.valid) {
      for (const f of config.fields ?? []) panel.form.setFieldValue(f.key, '');
    } else if (res?.message) {
      panel.setError(res.message);
    }
  };
  const refreshEndpoint = async (endpoint: ServerEndpointSummary) => {
    const res = await panel.actions.sendWs('validate_api_key', {
      provider: config.id,
      api_key: endpoint.base_url || endpoint.ref,
      ref: endpoint.ref,
    }, CREDENTIAL_PROBE_REQUEST_TIMEOUT);
    if (res && !res.valid && res.message) panel.setError(res.message);
  };
  const removeEndpoint = (endpoint: ServerEndpointSummary) =>
    panel.actions.sendWs('delete_api_key', { provider: endpoint.ref });

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
                size={theme.iconSize.xl}
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
          {endpoints && config.instructions && (
            <p className="text-xs text-muted-foreground">{config.instructions}</p>
          )}
          {field && !endpoints && (
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
          {field?.help && !endpoints && (
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
      {(endpoints ? config.fields ?? [] : secondaryFields).map((sf) => (
        <SecondaryFieldRow
          key={sf.key}
          fieldKey={sf.key}
          label={sf.label}
          placeholder={sf.placeholder}
          help={sf.help}
          secret={sf.secret}
          value={panel.values[sf.key] ?? ''}
          onChange={(v) => panel.form.setFieldValue(sf.key, v)}
          onSave={endpoints ? undefined : () => panel.actions.save(sf.key, panel.values[sf.key] ?? '')}
          loading={panel.loading === 'save'}
        />
      ))}

      {endpoints && (
        <>
          <div className="flex justify-end">
            <ActionButton intent="save" onClick={addEndpoint} disabled={busy || !inputValue.trim()}>
              Add endpoint
            </ActionButton>
          </div>
          <EndpointList endpoints={endpoints} busy={busy} onRefresh={refreshEndpoint} onRemove={removeEndpoint} />
        </>
      )}

      {variant === 'full' && config.hasDefaults && <ProviderDefaultsSection providerId={config.id} />}
      {variant === 'full' && config.hasDefaults && <LlmUsageSection providerId={config.id} providerName={config.name} />}
      {variant === 'full' && config.usageService && <ApiUsageSection service={config.usageService} serviceName={config.name} />}
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
  /** Absent for a field of an add-row form, which is submitted as a whole. */
  onSave?: () => void;
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
        {onSave && (
          <ActionButton
            intent="save"
            onClick={onSave}
            disabled={loading}
          >
            Save
          </ActionButton>
        )}
      </div>
      {help && (
        <p className="text-xs text-muted-foreground">{help}</p>
      )}
    </CardContent>
  </Card>
);

export default ApiKeyPanel;
