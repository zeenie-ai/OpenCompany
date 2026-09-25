/**
 * Settings > Connectors (design handoff): every app the owner can connect,
 * searchable and grouped by consumer category, each with Connect or a
 * Connected state and Disconnect.
 *
 * Connect opens the provider's own credential panel (ConnectDialog).
 * Disconnect removes what the panel would remove for API-key and signed-in
 * providers, after a confirmation; for the rest (a paired phone, an email
 * account) it opens the panel, which owns those steps. A card glows and a
 * toast confirms only when a provider actually flips to connected while
 * the tab is open, never on first render.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { filterProviders, isConnected, useConnectors, type ConsumerProvider } from '../data/connectors';
import { useHomeStore } from '../state/homeStore';
import { AppMark, StatusDot } from '../ui/primitives';
import { pillToast } from '../ui/pillToast';

/** Remove a provider's credential the way its panel would. False when the
 *  panel has to do it (QR pairing, the IMAP/SMTP account). */
async function disconnect(
  provider: ConsumerProvider,
  sendRequest: <T = unknown>(type: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<boolean> {
  if (provider.kind === 'apiKey') {
    await sendRequest('delete_api_key', { provider: provider.id });
    // Local model servers also keep their base URL under the field key.
    const fieldKey = provider.fields?.[0]?.key;
    if (fieldKey && fieldKey !== 'apiKey') await sendRequest('delete_api_key', { provider: fieldKey });
    return true;
  }
  if (provider.kind === 'oauth' && provider.ws?.logout) {
    await sendRequest(provider.ws.logout, {});
    return true;
  }
  return false;
}

function ConnectorCard({
  provider,
  onConnect,
  onDisconnect,
}: {
  provider: ConsumerProvider;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const connected = isConnected(provider);
  const ref = useRef<HTMLDivElement>(null);
  const wasConnected = useRef(connected);

  useEffect(() => {
    if (connected && !wasConnected.current) {
      animate(
        ref.current,
        [
          { boxShadow: '0 0 0 0 transparent' },
          { boxShadow: '0 0 0 1px var(--status-working-border), 0 0 28px var(--status-working-fill)', offset: 0.3 },
          { boxShadow: '0 0 0 0 transparent' },
        ],
        { duration: 1200, easing: 'default', fill: 'none' },
      );
      pillToast(`${provider.name} is connected`);
    }
    wasConnected.current = connected;
  }, [connected, provider.name]);

  return (
    <div
      ref={ref}
      data-connector={provider.id}
      className={cn(
        'flex gap-3 rounded-card border bg-bg-elevated p-3.5 transition-[border-color,translate] duration-(--dur-slow) hover:-translate-y-px motion-reduce:hover:translate-y-0',
        connected ? 'border-status-working-border' : 'border-border-default',
      )}
    >
      <AppMark name={provider.name} iconRef={provider.icon_ref} size="lg" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-base font-semibold text-fg-default">{provider.name}</span>
          {provider.runs_locally && (
            <span className="rounded-sm border border-node-workflow-edge px-1.25 font-mono text-2xs font-medium tracking-label text-node-workflow-ink">
              LOCAL
            </span>
          )}
        </div>
        {provider.description && <p className="text-meta leading-relaxed text-pretty text-fg-muted">{provider.description}</p>}
        <div className="mt-1.5 flex min-h-7 items-center gap-2">
          {connected ? (
            <>
              <StatusDot tone="working" />
              <span className="truncate text-xs font-medium text-status-working-ink">
                Connected{provider.account_label ? ` as ${provider.account_label}` : ''}
              </span>
              <Button
                variant="quiet"
                size="xs"
                onClick={onDisconnect}
                className="ml-auto hover:border-status-attention-border hover:bg-status-attention-fill hover:text-status-attention-ink"
              >
                Disconnect
              </Button>
            </>
          ) : (
            <Button
              variant="quiet"
              size="xs"
              onClick={onConnect}
              className="border-border-strong text-fg-default hover:border-action-run-border hover:bg-action-run-soft hover:text-action-run-ink"
            >
              Connect
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export function ConnectorsTab({ onConnect }: { onConnect: (providerId: string) => void }) {
  const { providers, categories, connectedCount, isLoading } = useConnectors();
  const category = useHomeStore((s) => s.settingsCategory);
  const setCategory = useHomeStore((s) => s.setSettingsCategory);
  const { sendRequest } = useWebSocketActions();
  const [query, setQuery] = useState('');
  const [confirming, setConfirming] = useState<ConsumerProvider | null>(null);
  const shown = useMemo(() => filterProviders(providers, query, category), [providers, query, category]);

  const confirmDisconnect = async () => {
    const provider = confirming;
    setConfirming(null);
    if (!provider) return;
    try {
      const done = await disconnect(provider, sendRequest);
      if (!done) onConnect(provider.id);
    } catch (error) {
      pillToast(error instanceof Error ? error.message : `Couldn't disconnect ${provider.name}`, { tone: 'error' });
    }
  };

  return (
    <div className="flex flex-col gap-4.5 px-8 pt-7 pb-8">
      <div className="flex items-start gap-4 pr-9">
        <div className="flex flex-1 flex-col gap-1">
          <h2 className="text-lg font-semibold text-fg-default">Connectors</h2>
          <p className="text-sm text-pretty text-fg-muted">
            Give your agents access to the apps you use. Credentials stay on this device.
          </p>
        </div>
        <span className="shrink-0 rounded-pill border border-status-working-border bg-status-working-fill px-2.5 py-1 font-mono text-2xs font-medium tracking-label text-status-working-ink uppercase">
          {connectedCount} connected
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-[1_1_220px]">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-faint" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search connectors"
            aria-label="Search connectors"
            className="h-9.5 pl-8.5"
          />
        </div>
        <ToggleGroup
          type="single"
          variant="chips"
          size="lg"
          aria-label="Category"
          value={category}
          onValueChange={(next) => next && setCategory(next)}
        >
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          {categories.map((c) => (
            <ToggleGroupItem key={c.key} value={c.key}>
              {c.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-2.5">
        {isLoading && providers.length === 0
          ? [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-card" />)
          : shown.map((provider) => (
              <ConnectorCard
                key={provider.id}
                provider={provider}
                onConnect={() => onConnect(provider.id)}
                onDisconnect={() => setConfirming(provider)}
              />
            ))}
      </div>
      {!isLoading && shown.length === 0 && (
        <p className="text-sm text-fg-muted">No apps match {query ? `"${query}"` : 'this category'}.</p>
      )}

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect {confirming?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Employees that use {confirming?.name} stop using it until you connect it again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDisconnect()}>Disconnect</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default ConnectorsTab;
