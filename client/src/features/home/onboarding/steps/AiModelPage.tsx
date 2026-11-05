/**
 * An AI model's page in Home's AI models dialog (onboarding v3, "AI models
 * dialog"). A model the catalogue describes as one field and a button (an
 * API key, or the address of a server that runs models) gets the plain
 * page: what it is, the field, Connect. Every other one keeps the full page
 * (FullProviderPage, as the Welcome guide shows it): the Gateway with its
 * allowance line, named endpoints, an install that requires 1Password, and
 * a key saved as a 1Password binding, which only the full page can change.
 *
 * A first connect is the list's to report (ConnectStep): its card turns
 * connected, the pill says so, and the dialog closes or returns to the
 * list. Saving a model that was already connected says so here and returns
 * to the list. Disconnect asks first. The page never shows a saved key.
 */

import { useId, useMemo, useState, type FormEvent } from 'react';
import { ArrowLeft, CircleAlert, ExternalLink } from 'lucide-react';
import { FEATURED_AI_PROVIDERS } from '@/components/credentials/aiProviderLinks';
import { rehydrateProvider } from '@/components/credentials/catalogueAdapter';
import { ConfirmDisconnect } from '@/components/credentials/ConfirmDisconnect';
import { useCredentialPanel } from '@/components/credentials/useCredentialPanel';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { isConnected, type ConsumerProvider } from '../../data/connectors';
import { pillToast } from '../../ui/pillToast';
import { AppMark, StatusPill } from '../../ui/primitives';
import { FullProviderPage, type ConnectPage } from './ConnectStep';

/** One field and a button: no allowance line, no named endpoints, and no
 *  1Password requirement. */
function hasPlainPage(provider: ConsumerProvider): boolean {
  return (
    provider.kind === 'apiKey' && provider.fields?.length === 1 && !provider.endpoints && !provider.sourceRequired && !provider.ws?.status
  );
}

export function AiModelPage(page: ConnectPage) {
  const provider = page.view.providers.find((p) => p.id === page.selection.id);
  if (!provider || !hasPlainPage(provider)) return <FullProviderPage {...page} />;
  return <PlainPage key={provider.id} {...page} provider={provider} />;
}

function PlainPage({ provider, view, selection, onBack }: ConnectPage & { provider: ConsumerProvider }) {
  const config = useMemo(() => rehydrateProvider(provider), [provider]);
  const panel = useCredentialPanel(config, true);
  const [draft, setDraft] = useState('');
  const [confirming, setConfirming] = useState(false);
  const id = useId();
  // hasPlainPage: exactly one field.
  const [field] = config.fields ?? [];

  // The saved key is a 1Password binding: the full page holds that choice.
  if (panel.sources[field.key]) return <FullProviderPage view={view} selection={selection} onBack={onBack} />;

  const connected = isConnected(provider);
  const featured = FEATURED_AI_PROVIDERS.find((p) => p.id === provider.id);
  const description = provider.description || featured?.hint;
  const secret = Boolean(field.secret);
  // A key is only ever typed here; a server's address shows what is saved
  // (or the catalogue's default).
  const value = secret ? draft : (panel.values[field.key] ?? '');
  const checking = panel.loading === 'validate';
  const busy = panel.loading !== null;
  const describedBy = [field.help && `${id}-field-help`, panel.error && `${id}-error`, `${id}-help`].filter(Boolean).join(' ');

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const entered = value.trim();
    if (checking || !entered) return;
    const result = await panel.actions.validate(config.id, entered);
    // A first connect is the list's to report, once its card turns connected.
    if (connected && result?.isValid) {
      pillToast(`${provider.name} is connected`);
      onBack();
    }
  };

  const disconnect = async () => {
    await panel.actions.remove(config.id);
    // A local server also keeps its address under the field's own key.
    if (field.key !== 'apiKey') await panel.actions.remove(field.key);
    pillToast(`${provider.name} disconnected`, { tone: 'info' });
    onBack();
  };

  return (
    <div className="flex flex-col gap-5.5 px-11 pt-6 pb-8">
      <Button data-stagger variant="quiet" size="sm" onClick={onBack} className="-ml-1 gap-1 self-start">
        <ArrowLeft aria-hidden className="size-3.75" />
        All AI models
      </Button>
      <div data-stagger className="flex items-center gap-3.5 pr-10">
        <AppMark name={provider.name} iconRef={provider.icon_ref} size="xl" className="bg-bg-elevated" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 className="m-0 text-lg font-semibold tracking-[-0.02em] text-fg-default">{provider.name}</h2>
          {description && <p className="m-0 text-row text-fg-muted">{description}</p>}
        </div>
        {connected && <StatusPill tone="working" label="Connected" size="compact" />}
      </div>
      <form
        data-stagger
        onSubmit={(event) => void submit(event)}
        className="flex flex-col gap-3 rounded-card border border-border-default bg-bg-elevated p-4.5"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={`${id}-input`} className="text-sm text-fg-default">
            {secret ? 'API key' : 'Server address'}
          </Label>
          {featured && (
            <a
              href={featured.keyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-sm text-fg-default underline-offset-4 hover:underline"
            >
              Get a key from {provider.name}
              <ExternalLink aria-hidden className="size-3.5" />
            </a>
          )}
        </div>
        <Input
          id={`${id}-input`}
          type={secret ? 'password' : 'text'}
          value={value}
          onChange={(event) => (secret ? setDraft(event.target.value) : panel.form.setFieldValue(field.key, event.target.value))}
          placeholder={secret && connected ? '•••••••• saved' : field.placeholder}
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={panel.error ? true : undefined}
          aria-describedby={describedBy}
          className="h-10 rounded-row border-border-default bg-bg-app px-3 font-mono text-sm dark:bg-bg-app"
        />
        {field.help && (
          <p id={`${id}-field-help`} className="m-0 text-xs text-fg-muted">
            {field.help}
          </p>
        )}
        {panel.error && (
          <Alert
            id={`${id}-error`}
            variant="destructive"
            aria-live="assertive"
            className="rounded-row border-danger-border bg-danger-soft px-3 py-2.5 text-sm"
          >
            <CircleAlert aria-hidden className="size-3.75" />
            <AlertDescription>{panel.error}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-2.5">
          <span id={`${id}-help`} className="min-w-45 flex-1 text-meta text-fg-muted">
            {secret ? 'OpenCompany keeps your key encrypted.' : `Start ${provider.name} first. OpenCompany connects to it at this address.`}
          </span>
          {connected && (
            <Button type="button" variant="quiet" disabled={busy} onClick={() => setConfirming(true)} className="h-8.5 rounded-row px-3 text-row">
              Disconnect
            </Button>
          )}
          <Button
            type="submit"
            variant="invert"
            disabled={checking || !value.trim()}
            className="h-8.5 gap-2 rounded-row px-4 text-row font-semibold"
          >
            {checking ? (
              <>
                <RingSpinner className="size-3 border-border-strong border-t-fg-muted" />
                Checking…
              </>
            ) : connected ? (
              'Save'
            ) : (
              'Connect'
            )}
          </Button>
        </div>
      </form>
      <ConfirmDisconnect name={provider.name} open={confirming} onOpenChange={setConfirming} onConfirm={() => void disconnect()} />
    </div>
  );
}

export default AiModelPage;
