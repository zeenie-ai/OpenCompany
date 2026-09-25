/**
 * Normal mode's Settings (design handoff "Settings modal"): Profile and
 * Connectors behind a left nav, in the shared Modal with its spring
 * entrance. Connect dialogs open on top from either tab (and from the
 * employee card's "Connect {App}"), through `useHomeConnect`.
 */

import { Plug, User, X } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { Tabs as TabsPrimitive } from 'radix-ui';
import { OcLogo } from '@/components/brand/Logo';
import { Button } from '@/components/ui/button';
import Modal from '@/components/ui/Modal';
import { stagger } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { useConnectors } from '../data/connectors';
import { useHomeStore, type SettingsTab } from '../state/homeStore';
import { ConnectorsTab } from './ConnectorsTab';
import { ProfileTab } from './ProfileTab';

const NAV_ITEM =
  'flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-sm font-medium text-fg-muted outline-none transition-colors hover:bg-bg-hover hover:text-fg-default focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=active]:bg-bg-hover data-[state=active]:text-fg-default';

export function HomeSettings({ onConnect }: { onConnect: (providerId: string) => void }) {
  const open = useHomeStore((s) => s.settingsOpen);
  const tab = useHomeStore((s) => s.settingsTab);
  const category = useHomeStore((s) => s.settingsCategory);
  const setTab = useHomeStore((s) => s.setSettingsTab);
  const close = useHomeStore((s) => s.closeSettings);
  const { connectedCount } = useConnectors();
  const bodyRef = useRef<HTMLDivElement>(null);

  // The tab's blocks rise in, 30ms apart (design handoff "Settings modal").
  useLayoutEffect(() => {
    if (!open || !bodyRef.current) return;
    stagger(
      bodyRef.current.querySelectorAll('[data-stagger]'),
      [
        { opacity: 0, transform: 'translateY(10px)' },
        { opacity: 1, transform: 'none' },
      ],
      { base: 40, step: 30, cap: 14, duration: 420, easing: 'spring' },
    );
  }, [open, tab, category]);

  return (
    <Modal
      isOpen={open}
      onClose={close}
      title="Settings"
      hideHeader
      motion="spring"
      scrollableBody={false}
      maxWidth="min(var(--w-settings), calc(100vw - 3rem))"
      maxHeight="min(var(--h-settings), calc(100vh - 3rem))"
      className="rounded-panel bg-bg-panel shadow-dialog"
    >
      <TabsPrimitive.Root
        orientation="vertical"
        value={tab}
        onValueChange={(next) => setTab(next as SettingsTab)}
        className="flex h-full min-h-0"
      >
        <div className="flex w-(--w-settings-nav) shrink-0 flex-col gap-1 border-r border-border-default px-3 py-5">
          <div className="flex items-center gap-2 px-2.5 pb-3.5">
            <OcLogo size="settings" wordmark={false} />
            <span className="text-md font-semibold text-fg-default">Settings</span>
          </div>
          <TabsPrimitive.List aria-label="Settings" className="flex flex-col gap-1">
            <TabsPrimitive.Trigger value="profile" className={NAV_ITEM}>
              <User aria-hidden className="size-4" />
              Profile
            </TabsPrimitive.Trigger>
            <TabsPrimitive.Trigger value="connectors" className={NAV_ITEM}>
              <Plug aria-hidden className="size-4" />
              Connectors
              <span className="ml-auto font-mono text-2xs text-status-working-ink">{connectedCount}</span>
            </TabsPrimitive.Trigger>
          </TabsPrimitive.List>
          <p className="mt-auto px-2.5 font-mono text-2xs text-fg-faint">Stored on this device</p>
        </div>
        <div ref={bodyRef} className="relative min-w-0 flex-1 overflow-y-auto">
          <TabsPrimitive.Content value="profile" className="outline-none">
            <ProfileTab onDone={close} />
          </TabsPrimitive.Content>
          <TabsPrimitive.Content value="connectors" className="outline-none">
            <ConnectorsTab onConnect={onConnect} />
          </TabsPrimitive.Content>
        </div>
      </TabsPrimitive.Root>
      <Button
        variant="quiet"
        size="icon"
        onClick={close}
        aria-label="Close settings"
        className={cn('absolute top-3.5 right-3.5 z-10')}
      >
        <X />
      </Button>
    </Modal>
  );
}

export default HomeSettings;
