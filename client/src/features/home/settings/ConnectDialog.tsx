/**
 * Connect one app from Normal mode: the editor's credential panel for the
 * provider's kind (API key, OAuth sign-in, QR pairing, email account), in
 * its compact variant (no usage or model-defaults sections). The panel owns
 * every connect / validate / sign-in rule; this is only its frame.
 *
 * Takes a provider id and reads the live catalogue entry, so the panel sees
 * `stored` / `connected` flip the moment the connection lands.
 */

import { useMemo } from 'react';
import Modal from '@/components/ui/Modal';
import PanelRenderer from '@/components/credentials/PanelRenderer';
import { rehydrateProvider } from '@/components/credentials/catalogueAdapter';
import { useHomeCatalogue } from '../data/connectors';

export function ConnectDialog({ providerId, onClose }: { providerId: string | null; onClose: () => void }) {
  const { data } = useHomeCatalogue();
  const provider = useMemo(
    () => (providerId ? (data?.providers.find((p) => p.id === providerId) ?? null) : null),
    [data, providerId],
  );
  const config = useMemo(() => (provider ? rehydrateProvider(provider) : null), [provider]);
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
