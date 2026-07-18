/**
 * OAuthConnect — reusable OAuth connect/disconnect flow.
 * Composes StatusCard (data-driven) + FieldRenderer + ActionBar.
 * Accepts optional `extraSection` slot for panel-specific content (e.g., API usage).
 */

import React from 'react';
import { Loader2, RotateCcw } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ActionButton } from '@/components/ui/action-button';
import ApiKeyInput from '../../ui/ApiKeyInput';
import FieldRenderer from './FieldRenderer';
import ActionBar, { type ActionDef } from './ActionBar';
import StatusCard from './StatusCard';
import type { ProviderConfig, StatusRowDef } from '../types';

interface CredentialFormShim {
  getFieldValue: (key: string) => string | undefined;
  setFieldValue: (key: string, value: string) => void;
}

interface Props {
  config: ProviderConfig;
  form: CredentialFormShim;
  connected: boolean;
  stored: boolean;
  loading: string | null;
  error: string | null;
  /** Device-flow one-time code from the login response (RFC 8628) —
   * shown while the user completes auth in the opened browser tab. */
  verificationCode?: string | null;
  icon: React.ReactNode;
  onSaveCredentials: () => void;
  /** Validate-and-store for the canonical `apiKey` field (dual-path
   * providers like Cloudflare: OAuth login OR a validated API token).
   * Same wiring as ApiKeyPanel's primary field — the backend
   * Credential's `_probe` owns all validation logic; the panel just
   * calls `panel.actions.validate(config.id, value)`. Rendered via the
   * shared ApiKeyInput component when the catalogue declares an
   * `apiKey` field. */
  onValidateApiKey?: () => void;
  onDeleteApiKey?: () => void;
  onLogin: () => void;
  onLogout: () => void;
  onRefresh: () => void;
  /** Optional slot rendered below the info box, above the ActionBar. */
  extraSection?: React.ReactNode;
}

const OAuthConnect: React.FC<Props> = ({
  config, form, connected, stored, loading, error, verificationCode, icon,
  onSaveCredentials, onValidateApiKey, onDeleteApiKey, onLogin, onLogout, onRefresh, extraSection,
}) => {
  // Some providers (e.g. Stripe) delegate auth entirely to an external
  // CLI tool — they have no OpenCompany-side credentials to paste, so
  // there's nothing to "store" before Login is meaningful. Only
  // *required* fields gate the Login button: optional fields are an
  // alternative auth path, not a prerequisite (e.g. Vercel's access
  // token alongside its CLI device-flow login).
  const hasRequiredFields = !!config.fields?.some((f) => f.required);

  const statusRows: StatusRowDef[] = [
    { label: 'Status', ok: () => connected, trueText: 'Connected', falseText: 'Not Connected' },
    ...(hasRequiredFields
      ? [{ label: 'Credentials', ok: () => stored, trueText: 'Configured', falseText: 'Not configured' } as StatusRowDef]
      : []),
  ];

  const actions: ActionDef[] = [
    { key: 'login', label: `Login with ${config.name}`, intent: 'save', onClick: onLogin, disabled: hasRequiredFields && !stored, hidden: connected },
    { key: 'logout', label: 'Disconnect', intent: 'stop', onClick: onLogout, hidden: !connected },
    { key: 'refresh', label: 'Refresh', intent: 'save', onClick: onRefresh, icon: <RotateCcw className="h-4 w-4" /> },
  ];

  const isSaving = loading === 'save';

  // The canonical `apiKey` field renders via the shared ApiKeyInput
  // (Validate button + Valid state + delete — same component every
  // api_key provider uses); remaining fields keep the generic
  // FieldRenderer + Save Credentials path.
  const apiKeyField = config.fields?.find((f) => f.key === 'apiKey');
  const otherFields = (config.fields ?? []).filter((f) => f.key !== 'apiKey');

  // Dual-path providers keep the token field reachable while
  // OAuth-connected (e.g. adding a Cloudflare analytics token after
  // logging in) — plain OAuth providers hide fields once connected.
  const showFields = !!config.fields && (!connected || !!apiKeyField);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <StatusCard icon={icon} title={config.name} rows={statusRows} status={null} />

      {showFields && config.fields && (
        <div className="flex w-full flex-col gap-3">
          {apiKeyField && onValidateApiKey && (
            <div className="flex flex-col gap-2">
              <ApiKeyInput
                value={form.getFieldValue('apiKey') ?? ''}
                onChange={(v) => form.setFieldValue('apiKey', v)}
                onSave={onValidateApiKey}
                onDelete={stored ? onDeleteApiKey : undefined}
                placeholder={apiKeyField.placeholder}
                loading={loading === 'validate'}
                isStored={stored}
              />
              {apiKeyField.help && (
                <p className="text-xs text-muted-foreground">{apiKeyField.help}</p>
              )}
            </div>
          )}
          {otherFields.length > 0 && (
            <>
              <FieldRenderer fields={otherFields} form={form} />
              <ActionButton intent="secret" onClick={onSaveCredentials} disabled={isSaving}>
                {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
                Save Credentials
              </ActionButton>
            </>
          )}
          {config.instructions && (
            <div className="text-xs leading-relaxed text-muted-foreground">
              {config.instructions}
              {config.callbackUrl && (
                <>
                  <br />
                  Callback URL:{' '}
                  <code className="text-accent">{config.callbackUrl}</code>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="rounded-md border border-accent/30 bg-accent/10 p-3">
        <div className="text-sm leading-relaxed text-muted-foreground">
          {connected
            ? config.account_label
              ? `Connected as ${config.account_label}.`
              : `Your ${config.name} account is connected.`
            : verificationCode
              ? (
                <div className="flex flex-col items-center gap-2 py-1 text-center">
                  <span>Enter this one-time code in the browser tab that just opened:</span>
                  <code className="select-all font-mono text-3xl font-bold tracking-[0.25em] text-accent">
                    {verificationCode}
                  </code>
                  <span className="text-xs">Click the code to select it, then approve the request to finish connecting.</span>
                </div>
              )
              : (stored || !hasRequiredFields)
                ? 'Click Login to authorize.'
                : 'Enter your credentials above to get started.'}
        </div>
      </div>

      {extraSection}

      <div className="flex-1" />

      <ActionBar actions={actions} loading={loading} />
    </div>
  );
};

export default OAuthConnect;
