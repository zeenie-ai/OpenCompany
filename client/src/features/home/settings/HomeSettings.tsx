/**
 * Normal mode's Settings (design handoff "Settings modal"): the pages
 * behind a left nav, in the shared Modal with its spring entrance. One
 * `PAGES` list drives both the nav and the panels. The nav's search keeps
 * the pages whose label or keywords match, and drops a group with none.
 * Connectors opens the app shell's shared credentials dialog on top, so
 * closing it returns to the same Settings page.
 */

import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { CircleHelp, CircleUserRound, CreditCard, Grid2x2Plus, Plug, Star, X, type LucideIcon } from 'lucide-react';
import { Tabs as TabsPrimitive } from 'radix-ui';
import { OcLogo } from '@/components/brand/Logo';
import { Button } from '@/components/ui/button';
import { CredentialsBrowser } from '@/components/credentials/CredentialsBrowser';
import Modal from '@/components/ui/Modal';
import type { CredentialsIntent } from '@/stores/shellDialogsStore';
import { useHomeStore, type SettingsTab } from '../state/homeStore';
import { NAV_ITEM } from '../ui/nav';
import { MicroLabel, SearchField } from '../ui/primitives';
import { BillingTab } from './BillingTab';
import { HelpTab } from './HelpTab';
import { PluginsTab } from './PluginsTab';
import { ProfileTab } from './ProfileTab';
import { SkillsTab } from './SkillsTab';
import { AccessTab } from './AccessTab';
import { staggerSettings } from './stagger';

interface PageContext {
  close: () => void;
  onConnect: (providerId: string, intent?: CredentialsIntent) => void;
  /** The category the page was opened on ('all' when none). */
  initialCategory: string;
}

interface Page {
  tab: SettingsTab;
  group: 'settings' | 'customize';
  label: string;
  icon: LucideIcon;
  /** Extra words the nav search matches. */
  keywords: string;
  render: (context: PageContext) => ReactNode;
}

const GROUPS = [
  { key: 'settings', label: 'Settings' },
  { key: 'customize', label: 'Customize' },
] as const;

const PAGES: Page[] = [
  { tab: 'access', group: 'settings', label: 'App access', icon: Plug, keywords: 'permissions allow revoke', render: () => <AccessTab /> },
  {
    tab: 'profile',
    group: 'settings',
    label: 'Profile',
    icon: CircleUserRound,
    keywords: 'account name',
    render: ({ close }) => <ProfileTab onDone={close} />,
  },
  {
    tab: 'billing',
    group: 'settings',
    label: 'Billing',
    icon: CreditCard,
    keywords: 'plan invoices usage',
    render: () => <BillingTab />,
  },
  {
    tab: 'help',
    group: 'settings',
    label: 'Help',
    icon: CircleHelp,
    keywords: 'guide welcome tour replay checklist get started',
    render: ({ close }) => <HelpTab onDone={close} />,
  },
  {
    tab: 'skills',
    group: 'customize',
    label: 'Skills',
    icon: Star,
    keywords: 'library abilities instructions',
    render: () => <SkillsTab />,
  },
  {
    tab: 'connectors',
    group: 'customize',
    label: 'Connectors',
    icon: Grid2x2Plus,
    keywords: 'apps',
    render: ({ onConnect, initialCategory }) => <CredentialsBrowser onConnect={onConnect} initialCategory={initialCategory} />,
  },
  {
    tab: 'plugins',
    group: 'customize',
    label: 'Plugins',
    icon: Plug,
    keywords: 'bundles starters templates',
    render: () => <PluginsTab />,
  },
];

/** The nav column. Rendered inside the dialog, so its search starts empty
 *  each time Settings opens. */
function SettingsNav() {
  const [query, setQuery] = useState('');
  const idBase = useId();
  const q = query.trim().toLowerCase();
  const matches = PAGES.filter((page) => !q || `${page.label} ${page.keywords}`.toLowerCase().includes(q));
  return (
    <div className="flex w-(--w-settings-nav) shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-border-default px-3 py-4">
      <SearchField value={query} onChange={setQuery} placeholder="Search" label="Search settings" className="mb-1" />
      {GROUPS.map((group) => {
        const pages = matches.filter((page) => page.group === group.key);
        if (pages.length === 0) return null;
        const labelId = `${idBase}-${group.key}`;
        return (
          <div key={group.key} className="flex flex-col gap-0.5">
            <MicroLabel className="px-2.5 pt-3.5 pb-1.5">
              <span id={labelId}>{group.label}</span>
            </MicroLabel>
            <TabsPrimitive.List aria-labelledby={labelId} className="flex flex-col gap-0.5">
              {pages.map(({ tab, label, icon: Icon }) => (
                <TabsPrimitive.Trigger key={tab} value={tab} className={NAV_ITEM}>
                  <Icon aria-hidden strokeWidth={1.75} className="size-4.25" />
                  {label}
                </TabsPrimitive.Trigger>
              ))}
            </TabsPrimitive.List>
          </div>
        );
      })}
      <div className="mt-auto flex items-center gap-2 px-2.5 pt-3.5">
        <OcLogo size="settings" wordmark={false} />
        <span className="font-mono text-2xs text-fg-faint">Stored on this device</span>
      </div>
    </div>
  );
}

export function HomeSettings({ onConnect }: { onConnect: (providerId: string, intent?: CredentialsIntent) => void }) {
  const open = useHomeStore((s) => s.settingsOpen);
  const tab = useHomeStore((s) => s.settingsTab);
  const category = useHomeStore((s) => s.settingsCategory);
  const setTab = useHomeStore((s) => s.setSettingsTab);
  const close = useHomeStore((s) => s.closeSettings);
  const bodyRef = useRef<HTMLDivElement>(null);

  // The page's blocks rise in when the dialog opens or the page changes.
  useLayoutEffect(() => {
    if (open) staggerSettings(bodyRef.current);
  }, [open, tab]);

  const context: PageContext = { close, onConnect, initialCategory: category };
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
        <SettingsNav />
        <div ref={bodyRef} className="relative min-w-0 flex-1 overflow-y-auto">
          {PAGES.map((page) => (
            <TabsPrimitive.Content key={page.tab} value={page.tab} className="outline-none">
              {page.render(context)}
            </TabsPrimitive.Content>
          ))}
        </div>
      </TabsPrimitive.Root>
      <Button variant="quiet" size="icon" onClick={close} aria-label="Close settings" className="absolute top-3.5 right-3.5 z-10">
        <X />
      </Button>
    </Modal>
  );
}

export default HomeSettings;
