import React, { useId, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Trash2 } from 'lucide-react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import OnePasswordSetup from './OnePasswordSetup';

interface Binding {
  id: string; label: string; origin: string;
  profile_id?: string | null; employee_id?: string | null; workflow_id?: string | null;
  success_origin: string; success_path: string; success_selector?: string;
}
interface Values {
  label: string; origin: string; username_reference: string; password_reference: string;
  success_origin: string; success_path: string; success_selector: string; profile_id: string;
}
type Draft = Values & { binding_id?: string; employee_id?: string | null; workflow_id?: string | null };
interface Result { success?: boolean; error?: string }
const QUERY_KEY = ['browserCredentialBindings'];
const empty = (): Draft => ({ label: '', origin: '', username_reference: '', password_reference: '',
  success_origin: '', success_path: '', success_selector: '', profile_id: '' });
const fields: { key: keyof Values; label: string; placeholder?: string; maxLength: number; required?: boolean; type?: string }[] = [
  { key: 'label', label: 'Login label', placeholder: 'Work account', maxLength: 128, required: true },
  { key: 'origin', label: 'Exact login origin', placeholder: 'https://accounts.example.com', maxLength: 2048, required: true, type: 'url' },
  { key: 'username_reference', label: 'Username 1Password reference', placeholder: 'op://vault-ID/item-ID/username', maxLength: 512, required: true },
  { key: 'password_reference', label: 'Password 1Password reference', placeholder: 'op://vault-ID/item-ID/password', maxLength: 512, required: true },
  { key: 'success_origin', label: 'Successful login origin (optional)', placeholder: 'Defaults to login origin', maxLength: 2048, type: 'url' },
  { key: 'success_path', label: 'Successful login path', placeholder: '/account', maxLength: 1024, required: true },
  { key: 'success_selector', label: 'Successful login selector (optional)', placeholder: '[data-account-menu]', maxLength: 512 },
];

/** Saved rows contain only metadata. A replacement always takes fresh references. */
export default function BrowserLoginBindings({ visible, profiles }: {
  visible: boolean; profiles: { id: string; name: string }[];
}) {
  const { sendRequest, isReady } = useWebSocketActions();
  const queryClient = useQueryClient();
  const id = useId();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [doomed, setDoomed] = useState<Binding | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const bindings = useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const result = await sendRequest<Result & { bindings?: Binding[] }>('browser_credential_bindings_list');
      if (result.success === false) throw new Error(result.error || 'Could not load website logins.');
      return result.bindings ?? [];
    },
    enabled: isReady && visible, staleTime: 0, refetchOnMount: 'always',
  });
  const change = async (command: string, data: Record<string, unknown>): Promise<boolean> => {
    setBusy(true); setError('');
    try {
      const result = await sendRequest<Result>(command, data);
      if (result.success === false) throw new Error(result.error || 'Could not save this change.');
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: ['credentialCatalogue'] });
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save this change.');
      return false;
    } finally { setBusy(false); }
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft || busy || !isReady) return;
    if (await change('browser_credential_binding_save', {
      ...draft, label: draft.label.trim(),
      success_origin: draft.success_origin || draft.origin,
      profile_id: draft.profile_id || null,
    })) setDraft(null);
  };
  const replace = (binding: Binding) => {
    setError('');
    setDraft({ ...empty(), binding_id: binding.id, label: binding.label, origin: binding.origin,
      profile_id: binding.profile_id || '', employee_id: binding.employee_id, workflow_id: binding.workflow_id,
      success_origin: binding.success_origin, success_path: binding.success_path, success_selector: binding.success_selector || '' });
  };

  return <section aria-label="1Password website logins" className="flex flex-col gap-3 border-t border-border pt-4">
    <div className="flex items-center justify-between gap-3">
      <h3 className="m-0 text-sm font-medium">1Password website logins</h3>
      {!draft && <ActionButton intent="secret" disabled={!isReady || busy} onClick={() => { setError(''); setDraft(empty()); }}>Add website login</ActionButton>}
    </div>
    <p className="m-0 text-xs text-muted-foreground">Save username and password field references using vault and item IDs. The browser reads credentials privately when an approved login needs them. Multi-step login and MFA use Take control.</p>
    <OnePasswordSetup visible={visible} />
    {!isReady ? <p className="m-0 text-xs text-muted-foreground">Reconnect to manage website logins.</p>
      : bindings.isPending ? <Loader2 aria-label="Loading website logins" className="size-4 animate-spin text-muted-foreground" />
      : bindings.isError ? <p role="alert" className="m-0 text-sm text-destructive">{bindings.error.message}</p>
      : (bindings.data ?? []).length === 0 ? <p className="m-0 text-xs text-muted-foreground">No website login bindings saved.</p>
      : <ul className="m-0 flex list-none flex-col gap-2 p-0">{bindings.data?.map((binding) => <li key={binding.id} className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card p-3">
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium">{binding.label}</span>
          <span className="truncate text-xs text-muted-foreground">{binding.origin}</span>
          {binding.profile_id && <span className="text-xs text-muted-foreground">Profile: {profiles.find((profile) => profile.id === binding.profile_id)?.name || 'Saved profile'}</span>}
          {(binding.employee_id || binding.workflow_id) && <span className="text-xs text-muted-foreground">Restricted to its saved employee or workflow.</span>}
        </div>
        <Button variant="outline" size="sm" disabled={busy || !isReady} aria-label={`Replace ${binding.label}`} onClick={() => replace(binding)}>Replace references</Button>
        <Button variant="ghost" size="icon-sm" disabled={busy || !isReady} aria-label={`Remove website login ${binding.label}`} onClick={() => setDoomed(binding)}><Trash2 aria-hidden /></Button>
      </li>)}</ul>}

    {draft && <form onSubmit={(event) => void save(event)} className="rounded-md border border-border p-3">
      <fieldset disabled={busy || !isReady} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
        <legend className="mb-3 text-sm font-medium">{draft.binding_id ? 'Replace website login' : 'Add website login'}</legend>
        {draft.binding_id && <p className="m-0 text-xs text-muted-foreground">Enter both references again. Saved reference fields are kept private.</p>}
        {fields.map((field) => <div key={field.key} className="flex flex-col gap-2">
          <Label htmlFor={`${id}-${field.key}`}>{field.label}</Label>
          <Input id={`${id}-${field.key}`} type={field.type || 'text'} value={draft[field.key]}
            onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
            required={field.required} maxLength={field.maxLength} placeholder={field.placeholder} autoComplete="off" spellCheck={false} />
        </div>)}
        <p className="m-0 text-xs text-muted-foreground">Origins use only the scheme and host, plus a port when needed. The success path is exact and excludes queries and fragments.</p>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-profile`}>Restrict to browser profile (optional)</Label>
          <select id={`${id}-profile`} value={draft.profile_id} onChange={(event) => setDraft({ ...draft, profile_id: event.target.value })}
            className="h-9 rounded-md border border-input bg-transparent px-3 text-sm">
            <option value="">All my browser profiles</option>
            {draft.profile_id && !profiles.some((profile) => profile.id === draft.profile_id) && <option value={draft.profile_id}>Saved profile</option>}
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
          </select>
        </div>
        <div className="flex gap-2">
          <ActionButton intent="secret" type="submit" disabled={!draft.label.trim() || !draft.origin || !draft.username_reference || !draft.password_reference || !draft.success_path}>{busy ? 'Saving…' : 'Save website login'}</ActionButton>
          <Button variant="ghost" onClick={() => { setDraft(null); setError(''); }}>Cancel</Button>
        </div>
      </fieldset>
    </form>}
    {error && <p role="alert" className="m-0 text-sm text-destructive">{error}</p>}
    <AlertDialog open={doomed !== null} onOpenChange={(open) => !open && setDoomed(null)}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Remove {doomed?.label}?</AlertDialogTitle><AlertDialogDescription>Its saved reference association is removed from OpenCompany. Future browser tasks will need another login binding.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => {
          const binding = doomed; setDoomed(null);
          if (binding) void change('browser_credential_binding_delete', { binding_id: binding.id });
        }}>Remove login</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </section>;
}
