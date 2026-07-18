/**
 * QrPairingPanel — config-driven QR pairing for WhatsApp and Android.
 * Status rows + QR config come from ProviderConfig — zero isWhatsApp conditionals.
 * Only action handlers need hook access (WhatsApp start vs Android relay connect).
 */

import React, { useMemo } from 'react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import ApiKeyInput from '../../ui/ApiKeyInput';
import QRCodeDisplay from '../../ui/QRCodeDisplay';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { useWhatsApp } from '../../../hooks/useWhatsApp';
import { useCredentialPanel } from '../useCredentialPanel';
import { useProviderStatus } from '../hooks';
import { StatusCard, ActionBar } from '../primitives';
import { RateLimitSection } from '../sections';
import { NodeIcon } from '../../../assets/icons';
import type { ActionDef } from '../primitives/ActionBar';
import type { ProviderConfig } from '../types';

const QrPairingPanel: React.FC<{ config: ProviderConfig; visible: boolean }> = ({ config, visible }) => {
  const panel = useCredentialPanel(config, visible);
  const status = useProviderStatus(config.statusHook);
  const { startConnection, restartConnection, getStatus: refreshWa } = useWhatsApp();
  const { sendRequest, setAndroidStatus } = useWebSocket();
  const qr = config.qr!;
  const connected = qr.isConnected(status);
  const qrData = status?.[qr.qrField];
  const field = config.fields?.[0];

  // Build actions from config.actions descriptors + hook methods
  const actionHandlers: Record<string, () => void> = useMemo(() => ({
    start: () => panel.execute('start', startConnection),
    restart: () => panel.execute('restart', restartConnection),
    refresh: () => panel.execute('refresh', refreshWa),
    connect: () => panel.execute('connect', async () => {
      const key = panel.form.getFieldValue('android_remote')?.trim();
      if (!key) { panel.setError('No API key configured'); return; }
      const res = await sendRequest('android_relay_connect', { url: (import.meta as any).env?.VITE_ANDROID_RELAY_URL || '', api_key: key });
      if (res.qr_data) setAndroidStatus((prev: any) => ({ ...prev, connected: true, paired: false, qr_data: res.qr_data }));
      return res;
    }),
    disconnect: () => panel.execute('disconnect', () => sendRequest('android_relay_disconnect', {})),
  }), [panel, startConnection, restartConnection, refreshWa, sendRequest, setAndroidStatus]);

  const actions: ActionDef[] = (config.actions ?? []).map(a => ({
    key: a.key,
    label: a.label,
    intent: a.intent,
    onClick: actionHandlers[a.key] ?? (() => {}),
    hidden: a.hidden?.(status, panel.stored),
    disabled: a.disabled?.(status, panel.stored),
  }));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-5">
      {field && <ApiKeyInput
        value={panel.form.getFieldValue(field.key) || ''}
        onChange={(v: string) => panel.form.setFieldValue(field.key, v)}
        onSave={() => panel.actions.save(field.key, (panel.form.getFieldValue(field.key) ?? '').trim()).then(() => panel.setStored(true))}
        onDelete={() => panel.actions.remove(field.key)}
        placeholder={field.placeholder} loading={panel.loading === 'save'} isStored={panel.stored}
      />}
      {config.statusRows && (
        <StatusCard
          icon={<NodeIcon icon={config.iconRef} className="h-6 w-6 text-2xl" />}
          title={config.name}
          rows={config.statusRows}
          status={status}
        />
      )}
      <div className="flex min-h-[300px] flex-1 flex-col items-center justify-center rounded-lg bg-muted p-5">
        <QRCodeDisplay value={qrData} isConnected={connected} size={280}
          connectedTitle={qr.connectedTitle} connectedSubtitle={qr.connectedSubtitle(status)}
          loading={qr.isLoading(status)} emptyText={qr.emptyText(status, panel.stored)} />
        {!connected && qrData && <div className="mt-3 text-sm text-muted-foreground">{qr.scanText}</div>}
      </div>
      {config.hasRateLimits && connected && <RateLimitSection />}
      {panel.error && (
        <Alert variant="destructive">
          <AlertDescription>{panel.error}</AlertDescription>
        </Alert>
      )}
      <ActionBar actions={actions} loading={panel.loading} />
    </div>
  );
};

export default QrPairingPanel;
