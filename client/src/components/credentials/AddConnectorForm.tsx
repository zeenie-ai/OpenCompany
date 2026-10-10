/**
 * The Connectors page's form for a custom connector: an MCP server's URL, a
 * name, and how it signs in (none, a bearer token, one header, or OAuth on
 * the server's own sign-in page). The server reads the server's tools before
 * keeping anything (`mcp_connector_add`, nodes/mcp/_handlers.py), so a wrong
 * URL or a refused sign-in comes back here in the server's words and nothing
 * is saved. With OAuth it answers with the sign-in page, which opens in a new
 * tab: the connector is kept, and its card shows, once the owner signed in
 * there.
 */

import { useId, useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { CREDENTIAL_PROBE_REQUEST_TIMEOUT, useWebSocketActions } from '@/contexts/WebSocketContext';

type SignInKind = 'none' | 'bearer' | 'header' | 'oauth';

const FIELD = 'h-9.5 rounded-lg bg-bg-app text-row font-normal md:text-row dark:bg-bg-app';
const FAILED = "Couldn't add that connector. Try again.";

export interface AddConnectorFormProps {
  close: () => void;
  /** The connector was saved; `ref` is its card's id (`mcp:<slug>`). */
  onAdded: (ref: string) => void;
}

export function AddConnectorForm({ close, onAdded }: AddConnectorFormProps) {
  const { sendRequest, isReady } = useWebSocketActions();
  const signInLabel = useId();
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [signIn, setSignIn] = useState<SignInKind>('none');
  const [token, setToken] = useState('');
  const [header, setHeader] = useState('');
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const signedIn =
    signIn === 'none' || signIn === 'oauth' || (signIn === 'bearer' ? token.trim() !== '' : header.trim() !== '' && value.trim() !== '');
  const ready = isReady && !saving && url.trim() !== '' && signedIn;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setSaving(true);
    setError('');
    try {
      const result = await sendRequest<{ success?: boolean; error?: string; ref?: string; sign_in_url?: string }>(
        'mcp_connector_add',
        {
          name,
          url,
          sign_in:
            signIn === 'bearer' ? { kind: 'bearer', token } : signIn === 'header' ? { kind: 'header', header, value } : { kind: signIn },
        },
        CREDENTIAL_PROBE_REQUEST_TIMEOUT,
      );
      if (result.success === false || !result.ref) throw new Error(result.error || FAILED);
      close();
      if (result.sign_in_url) {
        window.open(result.sign_in_url, '_blank');
        toast.info('Sign in on the page that opened. The connector shows here once you have.');
        return;
      }
      onAdded(result.ref);
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : FAILED);
      setSaving(false);
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)} aria-label="Custom connector" className="flex flex-col gap-2.5">
      <span className="text-base font-semibold text-fg-default">Custom connector</span>
      <Input
        value={url}
        onChange={(event) => setUrl(event.target.value)}
        placeholder="https://mcp.example.com/mcp"
        aria-label="Server URL"
        inputMode="url"
        autoComplete="off"
        spellCheck={false}
        className={FIELD}
      />
      <Input
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="Name it (optional), e.g. Orders"
        aria-label="Name"
        maxLength={60}
        className={FIELD}
      />
      <div className="flex flex-wrap items-center gap-2.5">
        <span id={signInLabel} className="text-sm text-fg-muted">
          Sign-in
        </span>
        <ToggleGroup
          type="single"
          variant="segmented"
          aria-labelledby={signInLabel}
          value={signIn}
          onValueChange={(next) => next && setSignIn(next as SignInKind)}
        >
          <ToggleGroupItem value="none" className="px-3 text-sm">
            None
          </ToggleGroupItem>
          <ToggleGroupItem value="bearer" className="px-3 text-sm">
            Token
          </ToggleGroupItem>
          <ToggleGroupItem value="header" className="px-3 text-sm">
            Header
          </ToggleGroupItem>
          <ToggleGroupItem value="oauth" className="px-3 text-sm">
            OAuth
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      {signIn === 'bearer' && (
        <Input
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="The token the server gave you"
          aria-label="Token"
          autoComplete="off"
          className={FIELD}
        />
      )}
      {signIn === 'header' && (
        <div className="flex flex-wrap gap-2.5">
          <Input
            value={header}
            onChange={(event) => setHeader(event.target.value)}
            placeholder="Header, e.g. X-API-Key"
            aria-label="Header name"
            autoComplete="off"
            spellCheck={false}
            className={`flex-[1_1_180px] ${FIELD}`}
          />
          <Input
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Its value"
            aria-label="Header value"
            autoComplete="off"
            className={`flex-[2_1_220px] ${FIELD}`}
          />
        </div>
      )}
      {error && (
        <p role="alert" className="m-0 text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="quiet" size="sm" onClick={close}>
          Cancel
        </Button>
        <ActionButton intent="run" type="submit" disabled={!ready} className="h-7.5 rounded-lg px-3.5 text-meta">
          {saving ? (signIn === 'oauth' ? 'Opening its sign-in…' : 'Reading its tools…') : 'Add'}
        </ActionButton>
      </div>
    </form>
  );
}

export default AddConnectorForm;
