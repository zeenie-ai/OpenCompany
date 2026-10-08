import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ActionButton } from '@/components/ui/action-button';
import { useWebSocket } from '@/contexts/WebSocketContext';
import { CREDENTIAL_PROBE_REQUEST_TIMEOUT } from '@/contexts/WebSocketContext';
import { queryKeys } from '@/lib/queryConfig';
import type { SavedCredentialSource } from '../useCredentialPanel';

/** References are operator configuration. Resolved values never reach this form. */
export default function OnePasswordField({ provider, saved, endpoint = false, onError, onSaved }: {
  provider: string;
  saved?: SavedCredentialSource;
  endpoint?: boolean;
  onError: (message: string) => void;
  onSaved: () => void;
}) {
  const { sendRequest } = useWebSocket();
  const queryClient = useQueryClient();
  const [reference, setReference] = useState(saved?.reference ?? '');
  const [slug, setSlug] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => setReference(saved?.reference ?? ''), [saved?.reference]);
  const validate = async () => {
    setBusy(true);
    try {
      const result = await sendRequest<{ success?: boolean; valid?: boolean; error?: string; message?: string }>(
        endpoint ? 'onepassword_endpoint_validate' : 'validate_api_key',
        endpoint
          ? { provider: `openai_compatible:${slug}`, reference, base_url: baseUrl, label: slug }
          : { provider, credential_source: 'onepassword', reference },
        CREDENTIAL_PROBE_REQUEST_TIMEOUT,
      );
      if (result.valid) {
        onSaved();
        await queryClient.invalidateQueries({ queryKey: queryKeys.credentialValues.byProvider(provider).queryKey });
        await queryClient.invalidateQueries({ queryKey: ['credentialCatalogue'] });
        if (endpoint) { setReference(''); setSlug(''); setBaseUrl(''); }
      } else onError(result.error || result.message || 'Could not validate the 1Password binding.');
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Could not validate the binding.');
    } finally { setBusy(false); }
  };
  return <div className="flex flex-col gap-3">
    <p className="text-xs text-muted-foreground">Authorize 1Password on this runtime. OpenCompany saves the reference and reads the field only when needed.</p>
    {endpoint && <>
      <Label htmlFor={`op-slug-${provider}`}>Endpoint name</Label>
      <Input id={`op-slug-${provider}`} value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="my-model-server" />
      <Label htmlFor={`op-url-${provider}`}>Base URL</Label>
      <Input id={`op-url-${provider}`} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://models.example.com/v1" />
    </>}
    <Label htmlFor={`op-ref-${provider}`}>1Password field reference</Label>
    <Input id={`op-ref-${provider}`} value={reference} onChange={(event) => setReference(event.target.value)} placeholder="op://vault-ID/item-ID/field" autoComplete="off" />
    <p className="text-xs text-muted-foreground">Use vault and item IDs. Passwords and API keys stay out of the form.</p>
    <ActionButton intent="secret" disabled={busy || !reference || (endpoint && (!slug || !baseUrl))} onClick={validate}>
      {busy ? 'Validating…' : endpoint ? 'Add 1Password endpoint' : 'Validate 1Password binding'}
    </ActionButton>
  </div>;
}
