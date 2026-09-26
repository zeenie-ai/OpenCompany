/**
 * BrowserProfilesPanel — the Web browser's login profiles.
 *
 * A profile is a browser's own cookies and logins. Each workflow's Browser
 * node uses that workflow's profile unless it picks a shared one, which is
 * added here. Logins come in two ways: sign in by hand with Take control in
 * the Browser workspace, or import a session file here (Playwright storage
 * state, Cookie-Editor JSON or cookies.txt). Sites show as domains and
 * cookie counts only; no cookie value ever reaches the client.
 */

import React, { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { ActionButton } from '@/components/ui/action-button';
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { buildApiUrl } from '@/config/api';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import type { ProviderConfig } from '../types';

interface BrowserSite {
  domain: string;
  cookie_count: number;
}

interface BrowserProfile {
  id: string;
  name: string;
  kind: 'shared' | 'employee';
  sites: BrowserSite[];
  in_use: { label: string } | null;
}

interface Result {
  success?: boolean;
  error?: string;
}

/** Invalidated by the `browser_profiles_updated` broadcast (WebSocketContext). */
const BROWSER_PROFILES_QUERY_KEY = ['browserProfiles'];

function sitesText(sites: BrowserSite[]): string {
  if (sites.length === 0) return 'no logins yet';
  const shown = sites.slice(0, 3).map((site) => site.domain).join(', ');
  return sites.length > 3 ? `${shown} and ${sites.length - 3} more` : shown;
}

const BrowserProfilesPanel: React.FC<{ config: ProviderConfig; visible: boolean }> = ({ visible }) => {
  const { sendRequest, isReady } = useWebSocketActions();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [doomed, setDoomed] = useState<BrowserProfile | null>(null);
  const importFor = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const profiles = useQuery({
    queryKey: BROWSER_PROFILES_QUERY_KEY,
    queryFn: async () => {
      const result = await sendRequest<Result & { profiles?: BrowserProfile[] }>('browser_profiles_list');
      if (result.success === false) throw new Error(result.error || 'Could not load the browser profiles.');
      return result.profiles ?? [];
    },
    enabled: isReady && visible,
    // Agent runs create profiles while this panel is closed.
    refetchOnMount: 'always',
    staleTime: 0,
  });

  /** Runs one change, reports a failure, and refreshes the list. */
  const run = async (change: () => Promise<Result>): Promise<boolean> => {
    setBusy(true);
    try {
      const result = await change();
      if (result.success === false) toast.error(result.error || 'That did not work.');
      return result.success !== false;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'That did not work.');
      return false;
    } finally {
      setBusy(false);
      void queryClient.invalidateQueries({ queryKey: BROWSER_PROFILES_QUERY_KEY });
    }
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (await run(() => sendRequest<Result>('browser_profile_create', { name: name.trim() }))) setName('');
  };

  const importFile = (profileId: string, file: File) =>
    run(async () => {
      const body = new FormData();
      body.append('file', file, file.name);
      // No Content-Type: the browser sets the multipart boundary.
      const response = await fetch(buildApiUrl(`/api/browser/profiles/${encodeURIComponent(profileId)}/session-file`), {
        method: 'POST',
        body,
        credentials: 'include',
      });
      const upload = await response.json().catch(() => null);
      if (!response.ok) return { success: false, error: upload?.detail || `Upload failed (${response.status}).` };
      const domains = ((upload?.sites ?? []) as BrowserSite[]).map((site) => site.domain);
      if (domains.length === 0) return { success: false, error: 'That file has no logins in it.' };
      // Opens the profile's browser to write the cookies, so allow for a start.
      const result = await sendRequest<Result>(
        'browser_import_commit',
        { import_id: upload.import_id, profile_id: profileId, domains },
        60_000,
      );
      if (result.success !== false) toast.success(`Imported logins for ${domains.length} ${domains.length === 1 ? 'site' : 'sites'}.`);
      return result;
    });

  const list = profiles.data ?? [];

  return (
    <div className="flex flex-col gap-4 p-6">
      <p className="m-0 text-sm text-muted-foreground">
        A profile keeps a browser's logins. Each workflow's browser has its own; add a shared one to use the same logins in
        several workflows. To sign in to a site, use Take control in the Browser workspace, or import a session file here.
      </p>

      <form onSubmit={(event) => void create(event)} className="flex gap-2">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="New profile, e.g. Work"
          aria-label="New profile name"
          maxLength={64}
        />
        <ActionButton intent="save" type="submit" disabled={busy || !name.trim()}>
          Add profile
        </ActionButton>
      </form>

      {profiles.isPending ? (
        <Loader2 aria-label="Loading profiles" className="size-5 animate-spin text-muted-foreground" />
      ) : profiles.isError ? (
        <p className="m-0 text-sm text-destructive">{profiles.error.message}</p>
      ) : list.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">No profiles yet. One is made the first time a workflow uses its browser.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {list.map((profile) => (
            <li key={profile.id} className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card p-3">
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-medium">{profile.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {profile.kind === 'employee' ? 'Workflow profile' : 'Shared profile'} · {sitesText(profile.sites)}
                </span>
              </div>
              {profile.in_use && <Badge variant="secondary">In use by {profile.in_use.label}</Badge>}
              <ActionButton
                intent="config"
                disabled={busy}
                onClick={() => {
                  importFor.current = profile.id;
                  fileInput.current?.click();
                }}
              >
                <Upload aria-hidden className="size-3.5" />
                Import logins
              </ActionButton>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Delete ${profile.name}`}
                title={profile.in_use ? 'In use; stop it first' : `Delete ${profile.name}`}
                disabled={busy || profile.in_use !== null}
                onClick={() => setDoomed(profile)}
              >
                <Trash2 aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <input
        ref={fileInput}
        type="file"
        accept=".json,.txt"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file && importFor.current) void importFile(importFor.current, file);
        }}
      />

      <AlertDialog open={doomed !== null} onOpenChange={(open) => !open && setDoomed(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {doomed?.name}?</AlertDialogTitle>
            <AlertDialogDescription>Its saved logins are removed. Browsers that used it will need to sign in again.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const profile = doomed;
                setDoomed(null);
                if (profile) void run(() => sendRequest<Result>('browser_profile_delete', { profile_id: profile.id }));
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default BrowserProfilesPanel;
