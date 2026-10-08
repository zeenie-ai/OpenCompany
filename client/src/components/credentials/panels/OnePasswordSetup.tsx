import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';

type Status = { available?: boolean; error?: string; version?: string; auth_mode?: string; success?: boolean };

/** Installation/version checks never read a field or trigger vault authorization. */
export default function OnePasswordSetup({ visible = true }: { visible?: boolean }) {
  const { sendRequest, isReady } = useWebSocketActions();
  const queryClient = useQueryClient();
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState('');
  const status = useQuery({
    queryKey: ['onepasswordStatus'],
    queryFn: () => sendRequest<Status>('onepassword_status'),
    enabled: visible && isReady,
    staleTime: 30_000,
    retry: false,
  });
  const install = async () => {
    setInstalling(true); setError('');
    try {
      const result = await sendRequest<Status>('onepassword_install', {}, 180_000);
      if (!result.available) setError(result.error || 'Could not install the verified CLI.');
      await queryClient.invalidateQueries({ queryKey: ['onepasswordStatus'] });
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not install the CLI.'); }
    finally { setInstalling(false); }
  };
  return <div className="mb-3 flex flex-col gap-2 text-xs text-muted-foreground">
    {status.data?.available
      ? <p>1Password CLI {status.data.version} · {status.data.auth_mode === 'service_account' ? 'Service account' : 'Desktop authorization'}</p>
      : <><p>{status.data?.error || 'Checking 1Password CLI…'}</p>
        <Button variant="outline" size="sm" disabled={installing || !isReady} onClick={install}>{installing ? 'Installing…' : 'Install verified CLI'}</Button>
      </>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    <a className="underline" href="https://www.1password.dev/cli/get-started" target="_blank" rel="noreferrer">Configure 1Password authorization</a>
  </div>;
}
