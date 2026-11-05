/**
 * Home's AI models dialog (onboarding v3, "AI models dialog"): every AI
 * model in the catalogue (ConnectStep, the list the Welcome guide's Connect
 * step shows), and a model's page in place of the list (AiModelPage). No
 * nav and no footer. Close, Esc and a click outside close it; Esc first
 * leaves a model's page, and a disconnect confirmation (its own layer)
 * before that.
 *
 * Whatever needs a model opens it (homeStore.openConnectAI): a setup or a
 * hire that found none, Start refused for want of one, an employee's
 * Connect an AI model. Connecting one then closes it, so that task goes
 * on. The Get started checklist opens it to stay on the list instead. Its
 * state lives in homeStore (`connectAI`); focus goes back to whatever had
 * it when the dialog opened.
 */

import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { staggerSettings } from '@/components/catalog/stagger';
import { Button } from '@/components/ui/button';
import Modal from '@/components/ui/Modal';
import { useHomeStore, type ConnectAIState } from '../state/homeStore';
import { AiModelPage } from './steps/AiModelPage';
import { ConnectStep } from './steps/ConnectStep';

/** The scrolling body. It mounts with the dialog's content (the portal
 *  renders that a step after the dialog opens), so its blocks rise in as
 *  the dialog opens, and again when the list and a model's page swap. */
function Body({ page, children }: { page: ConnectAIState['provider']; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    staggerSettings(ref.current);
  }, [page]);
  return (
    <div ref={ref} className="min-h-0 flex-1 overflow-y-auto">
      {children}
    </div>
  );
}

export function AiModelsDialog() {
  const open = useHomeStore((s) => s.connectAI.open);
  const provider = useHomeStore((s) => s.connectAI.provider);
  const closeOnConnect = useHomeStore((s) => s.connectAI.closeOnConnect);
  const close = useHomeStore((s) => s.closeConnectAI);
  const setProvider = useHomeStore((s) => s.setConnectAIProvider);
  const opener = useRef<HTMLElement | null>(null);

  return (
    <Modal
      isOpen={open}
      onClose={close}
      onEscapeKeyDown={(event) => {
        if (!provider) return;
        event.preventDefault();
        setProvider(null);
      }}
      // Radix gives focus back to a dialog's trigger, and this one has none.
      onOpenAutoFocus={() => {
        opener.current = document.activeElement as HTMLElement | null;
      }}
      onCloseAutoFocus={(event) => {
        if (!opener.current?.isConnected) return;
        event.preventDefault();
        opener.current.focus();
      }}
      title="AI models"
      hideHeader
      motion="spring"
      scrollableBody={false}
      maxWidth="min(var(--w-ai-models), calc(100vw - 3rem))"
      maxHeight="min(var(--h-ai-models), calc(100vh - 3rem))"
      className="rounded-panel bg-bg-panel shadow-dialog"
    >
      <Body page={provider}>
        <ConnectStep
          selection={provider}
          onSelect={setProvider}
          finishOnConnect={closeOnConnect}
          onFinish={close}
          renderPage={(page) => <AiModelPage {...page} />}
        />
      </Body>
      <Button variant="quiet" size="icon-lg" onClick={close} aria-label="Close" className="absolute top-3.5 right-3.5 z-10">
        <X />
      </Button>
    </Modal>
  );
}

export default AiModelsDialog;
