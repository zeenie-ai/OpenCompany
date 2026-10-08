/** One app-level host for browsing, connecting and managing credentials.
 *  The Welcome guide's Connect step shows the same provider page inline
 *  (ProviderPage); every other entry point opens this dialog. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import Modal from '@/components/ui/Modal';
import { useShellDialogsStore, type CredentialsIntent, type CredentialsOptions } from '@/stores/shellDialogsStore';
import CredentialsBrowser from './CredentialsBrowser';
import { isConnected, useCredentialsCatalogue } from './catalogue';
import { ProviderPage } from './ProviderPage';

interface Props { visible: boolean; onClose: () => void }

/** Each explicit open starts fresh navigation; switching mode does not. */
export default function CredentialsModal(props: Props) {
  const options = useShellDialogsStore((s) => s.credentialsOptions);
  const requestId = useShellDialogsStore((s) => s.credentialsRequestId);
  return <CredentialsSession key={requestId} {...props} options={options} />;
}

function CredentialsSession({ visible, onClose, options }: Props & { options: CredentialsOptions }) {
  const view = useCredentialsCatalogue();
  const [browsing, setBrowsing] = useState(!options.providerId);
  const [selection, setSelection] = useState<{ id: string; intent: CredentialsIntent } | null>(
    options.providerId ? { id: options.providerId, intent: options.intent ?? 'manage' } : null,
  );
  const browserOpener = useRef<HTMLElement | null>(null);
  const providerOpener = useRef<HTMLElement | null>(null);
  const sessionOpener = useRef<HTMLElement | null>(null);
  const provider = view.providers.find((p) => p.id === selection?.id) ?? null;
  const connected = provider ? isConnected(provider) : false;
  const seen = useRef({ id: selection?.id, known: false, connected: false });

  const closeProvider = useCallback(() => {
    if (browsing) setSelection(null);
    else onClose();
  }, [browsing, onClose]);

  useEffect(() => {
    const before = seen.current;
    seen.current = { id: selection?.id, known: visible && provider !== null, connected };
    if (!visible || !provider || before.id !== selection?.id || !before.known || before.connected || !connected) return;
    toast.success(`${provider.name} is connected`);
    if (selection?.intent === 'connect') {
      if (options.intent === 'connect') onClose();
      else closeProvider();
    }
  }, [visible, provider, selection, connected, options.intent, onClose, closeProvider]);

  const allProviders = () => { setBrowsing(true); setSelection(null); };
  const aiSetup = options.categoryId === 'ai' && options.intent === 'connect';

  return (
    <>
      <Modal
        isOpen={visible && browsing}
        onClose={onClose}
        title={aiSetup ? 'Connect an AI model' : 'Connectors'}
        titleIcon={<ShieldCheck className="size-4" />}
        motion="spring"
        maxWidth="min(1040px, calc(100vw - 2rem))"
        maxHeight="min(760px, calc(100dvh - 2rem))"
        onOpenAutoFocus={() => {
          browserOpener.current = document.activeElement as HTMLElement | null;
          sessionOpener.current ??= browserOpener.current;
        }}
        onCloseAutoFocus={(event) => {
          if (sessionOpener.current?.isConnected) {
            event.preventDefault();
            sessionOpener.current.focus();
          }
        }}
      >
        <CredentialsBrowser catalogue={view} initialCategory={options.categoryId}
          onConnect={(id, intent = 'connect') => setSelection({ id, intent })} />
      </Modal>

      <Modal
        isOpen={visible && selection !== null}
        onClose={closeProvider}
        title={provider ? `${selection?.intent === 'manage' ? 'Manage' : 'Connect'} ${provider.name}` : 'Connector'}
        titleIcon={null}
        motion="spring"
        maxWidth="min(640px, calc(100vw - 2rem))"
        maxHeight="min(800px, calc(100dvh - 2rem))"
        autoHeight
        onOpenAutoFocus={() => {
          providerOpener.current = document.activeElement as HTMLElement | null;
          sessionOpener.current ??= providerOpener.current;
        }}
        onCloseAutoFocus={(event) => {
          // Closing both layers returns to the screen, not to a disappearing card.
          event.preventDefault();
          if ((!visible && browsing) || (browsing && providerOpener.current === sessionOpener.current)) return;
          if (providerOpener.current?.isConnected) providerOpener.current.focus();
        }}
      >
        {selection && (
          <ProviderPage
            view={view}
            selection={selection}
            backLabel={options.categoryId === 'ai' ? 'All AI models' : 'All connectors'}
            onBack={allProviders}
            visible={visible}
          />
        )}
      </Modal>
    </>
  );
}
