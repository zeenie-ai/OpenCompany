/**
 * Connect one app from Normal mode: the editor's credential panel for the
 * provider's kind (API key, OAuth sign-in, QR pairing, email account), in
 * its compact variant (no usage or model-defaults sections). The panel owns
 * every connect / validate / sign-in rule; this is only its frame.
 *
 * Takes a provider id and reads the live catalogue entry, so the panel sees
 * `stored` / `connected` flip the moment the connection lands. When it does,
 * the dialog closes; from outside Settings (a draft's or an employee's
 * "Connect {App}") it also confirms with a toast, which Settings' own card
 * does there.
 */

import { useEffect, useMemo, useRef } from 'react';
import Modal from '@/components/ui/Modal';
import PanelRenderer from '@/components/credentials/PanelRenderer';
import { rehydrateProvider } from '@/components/credentials/catalogueAdapter';
import { isConnected, useHomeCatalogue } from '../data/connectors';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

export function ConnectDialog({ providerId, onClose }: { providerId: string | null; onClose: () => void }) {
  const { data } = useHomeCatalogue();
  const provider = useMemo(
    () => (providerId ? (data?.providers.find((p) => p.id === providerId) ?? null) : null),
    [data, providerId],
  );
  const config = useMemo(() => (provider ? rehydrateProvider(provider) : null), [provider]);

  // Only the same app going from not connected to connected counts.
  const connected = provider ? isConnected(provider) : false;
  const seen = useRef({ id: providerId, known: provider !== null, connected });
  useEffect(() => {
    const before = seen.current;
    seen.current = { id: providerId, known: provider !== null, connected };
    const flipped = before.id === providerId && before.known && !before.connected && connected;
    if (!flipped || !provider) return;
    if (!useHomeStore.getState().settingsOpen) pillToast(`${provider.name} is connected`);
    onClose();
  }, [providerId, provider, connected, onClose]);

  return (
    <Modal
      isOpen={providerId !== null}
      onClose={onClose}
      title={provider ? `Connect ${provider.name}` : 'Connect'}
      titleIcon={null}
      motion="spring"
      maxWidth="min(560px, calc(100vw - 2rem))"
      maxHeight="min(720px, calc(100vh - 2rem))"
      autoHeight
      className="rounded-panel bg-bg-panel shadow-dialog"
    >
      <PanelRenderer config={config} visible={providerId !== null} variant="compact" />
    </Modal>
  );
}

export default ConnectDialog;
