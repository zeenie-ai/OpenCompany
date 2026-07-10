/**
 * OAuthPanel — generic OAuth panel for Twitter, Google Workspace, Telegram.
 * Delegates entirely to the OAuthConnect primitive.
 */

import React from 'react';
import { useCredentialPanel } from '../useCredentialPanel';
import { useProviderStatus } from '../hooks';
import { OAuthConnect } from '../primitives';
import { ApiUsageSection } from '../sections';
import { NodeIcon } from '../../../assets/icons';
import type { ProviderConfig } from '../types';

const OAuthPanel: React.FC<{ config: ProviderConfig; visible: boolean }> = ({ config, visible }) => {
  const panel = useCredentialPanel(config, visible);
  const status = useProviderStatus(config.statusHook);
  // Providers without a registered status hook (e.g. CLI-managed auth
  // like Stripe) signal "connected" via the catalogue's authoritative
  // `stored` field — same source the modal sidebar uses for badges.
  // Providers with a hook keep their hook-driven semantics.
  const connected = status ? !!status.connected : !!config.stored;

  return (
    <div className="flex min-h-0 flex-1 flex-col p-5">
      <OAuthConnect
        config={config} form={panel.form} connected={connected}
        stored={panel.stored} loading={panel.loading} error={panel.error}
        verificationCode={panel.verificationCode}
        icon={<NodeIcon icon={config.iconRef} className="h-6 w-6 text-2xl" />}
        onSaveCredentials={() => {
          const missing = config.fields?.find(f => f.required && !panel.form.getFieldValue(f.key)?.trim());
          if (missing) { panel.setError(`${missing.label} is required`); return; }
          // Snapshot values BEFORE the loop. Each panel.actions.save call
          // invalidates the credentialValues query, which refetches and
          // only reflects fields already stored on the server — reading
          // getFieldValue inside the loop would return undefined for any
          // field we haven't saved yet, silently dropping them.
          const snapshot = panel.form.getFieldsValue();
          panel.execute('save', async () => {
            for (const f of config.fields!) {
              const v = snapshot[f.key]?.trim();
              if (v) await panel.actions.save(f.key, v);
            }
            panel.setStored(true);
            return { success: true };
          });
        }}
        onLogin={() => panel.actions.oauthLogin()}
        onLogout={() => panel.actions.oauthLogout()}
        onRefresh={() => panel.actions.oauthRefresh()}
        extraSection={config.usageService && <ApiUsageSection service={config.usageService} serviceName={config.name} />}
      />
    </div>
  );
};

export default OAuthPanel;
