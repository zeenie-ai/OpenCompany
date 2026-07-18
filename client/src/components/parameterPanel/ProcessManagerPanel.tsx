import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Eye, RefreshCw, RotateCw, Search, Square } from 'lucide-react';
import { toast } from 'sonner';

import { useWebSocket } from '@/contexts/WebSocketContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';

interface Props { workflowId?: string }

interface ManagedProcess {
  name: string;
  command: string;
  pid: number;
  status: 'running' | 'stopped' | 'error' | string;
  started_at?: string;
  stopped_at?: string;
  exit_code?: number | null;
  working_directory?: string;
  stdout_lines?: number;
  stderr_lines?: number;
  ports?: number[];
}

const duration = (startedAt?: string, end = Date.now()) => {
  if (!startedAt) return '—';
  const ms = Math.max(0, end - new Date(startedAt).getTime());
  if (!Number.isFinite(ms)) return '—';
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
};

const timestamp = (value?: string) => value
  ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value))
  : '—';

const ProcessManagerPanel: React.FC<Props> = ({ workflowId }) => {
  const { sendRequest } = useWebSocket();
  const [processes, setProcesses] = useState<ManagedProcess[]>([]);
  const [maxProcesses, setMaxProcesses] = useState(10);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<ManagedProcess | null>(null);
  const [output, setOutput] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  // Populate wall-clock time from an effect so rendering stays pure.
  const [now, setNow] = useState(0);

  const refresh = useCallback(async (quiet = false) => {
    if (!workflowId) return;
    if (!quiet) setLoading(true);
    try {
      const response = await sendRequest<{ success: boolean; processes?: ManagedProcess[]; max_processes?: number }>(
        'process_list', { workflow_id: workflowId },
      );
      setProcesses(response.processes ?? []);
      setMaxProcesses(response.max_processes ?? 10);
      setSelected((current) => current
        ? (response.processes ?? []).find((process) => process.name === current.name) ?? current
        : null);
    } catch (error) {
      if (!quiet) toast.error(error instanceof Error ? error.message : 'Unable to load managed processes');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [sendRequest, workflowId]);

  const loadOutput = useCallback(async (process: ManagedProcess) => {
    if (!workflowId) return;
    setSelected(process);
    const response = await sendRequest<{ lines?: string[] }>('process_get_output', {
      workflow_id: workflowId, name: process.name, stream: 'stdout', tail: 300,
    });
    setOutput((response.lines ?? []).join('\n'));
  }, [sendRequest, workflowId]);

  const mutate = useCallback(async (operation: 'process_stop' | 'process_restart', process: ManagedProcess) => {
    if (!workflowId) return;
    setBusy(process.name);
    try {
      const response = await sendRequest<{ success: boolean; error?: string }>(operation, {
        workflow_id: workflowId, name: process.name,
      });
      if (!response.success) throw new Error(response.error || 'Process operation failed');
      toast.success(operation === 'process_stop' ? `Stopped ${process.name}` : `Restarted ${process.name}`);
      await refresh(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Process operation failed');
    } finally {
      setBusy(null);
    }
  }, [refresh, sendRequest, workflowId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    setNow(Date.now());
    const poll = window.setInterval(() => void refresh(true), 2500);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { window.clearInterval(poll); window.clearInterval(clock); };
  }, [refresh]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return term ? processes.filter((process) =>
      [process.name, process.command, process.working_directory, ...(process.ports ?? []).map(String)]
        .some((value) => value?.toLowerCase().includes(term)),
    ) : processes;
  }, [processes, search]);
  const running = processes.filter((process) => process.status === 'running').length;
  const failed = processes.filter((process) => process.status === 'error').length;

  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
    <header className="mb-4 flex shrink-0 items-start justify-between gap-3">
      <div><h2 className="text-lg font-semibold text-fg-default">Managed Processes</h2><p className="text-sm text-fg-muted">Live workflow processes, listener ports, output, and lifecycle controls.</p></div>
      <Button variant="outline" size="sm" disabled={loading} onClick={() => void refresh()}><RefreshCw className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} /> Refresh</Button>
    </header>
    <section className="mb-4 grid shrink-0 grid-cols-3 gap-3">
      <div className="rounded border border-border-default bg-bg-elevated p-3"><div className="text-xs text-fg-muted">Running</div><div className="text-xl font-semibold text-success">{running} / {maxProcesses}</div></div>
      <div className="rounded border border-border-default bg-bg-elevated p-3"><div className="text-xs text-fg-muted">Stopped</div><div className="text-xl font-semibold">{processes.length - running - failed}</div></div>
      <div className="rounded border border-border-default bg-bg-elevated p-3"><div className="text-xs text-fg-muted">Errors</div><div className="text-xl font-semibold text-destructive">{failed}</div></div>
    </section>
    <div className="relative mb-3 shrink-0"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" /><Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, command, directory, or port" /></div>
    <div className="min-h-0 flex-1 overflow-auto rounded border border-border-default bg-bg-elevated">
      <table className="w-full min-w-[1000px] border-collapse text-sm"><thead className="sticky top-0 z-10 bg-bg-panel text-left text-xs text-fg-muted"><tr>{['Name','Status','PID','Ports','Started','Elapsed','Output','Command',''].map((label) => <th key={label} className="border-b border-border-default px-3 py-2 font-medium">{label}</th>)}</tr></thead>
        <tbody>{visible.map((process) => <tr key={process.name} className="border-b border-border-default/70 hover:bg-bg-panel">
          <td className="px-3 py-3 font-medium">{process.name}</td><td className="px-3 py-3"><Badge variant="outline" className={process.status === 'running' ? 'text-success' : process.status === 'error' ? 'text-destructive' : 'text-fg-muted'}>{process.status}</Badge></td>
          <td className="px-3 py-3 tabular-nums">{process.pid}</td><td className="px-3 py-3 tabular-nums">{process.ports?.join(', ') || '—'}</td><td className="whitespace-nowrap px-3 py-3 text-xs">{timestamp(process.started_at)}</td><td className="px-3 py-3 tabular-nums">{duration(process.started_at, process.status === 'running' ? now : process.stopped_at ? new Date(process.stopped_at).getTime() : now)}</td>
          <td className="px-3 py-3 text-xs tabular-nums">{process.stdout_lines ?? 0} out · {process.stderr_lines ?? 0} err</td><td className="max-w-[320px] truncate px-3 py-3 font-mono text-xs" title={process.command}>{process.command}</td>
          <td className="whitespace-nowrap px-3 py-3"><Button variant="ghost" size="sm" aria-label={`Inspect ${process.name}`} onClick={() => void loadOutput(process)}><Eye className="h-4 w-4" /></Button>{process.status === 'running' && <Button variant="ghost" size="sm" disabled={busy === process.name} aria-label={`Stop ${process.name}`} onClick={() => void mutate('process_stop', process)}><Square className="h-4 w-4" /></Button>}<Button variant="ghost" size="sm" disabled={busy === process.name} aria-label={`Restart ${process.name}`} onClick={() => void mutate('process_restart', process)}><RotateCw className="h-4 w-4" /></Button></td>
        </tr>)}{!loading && visible.length === 0 && <tr><td colSpan={9} className="p-12 text-center text-fg-muted">No managed processes in this workflow.</td></tr>}</tbody>
      </table>
    </div>
    <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null); }}><DialogContent className="max-w-4xl"><DialogHeader><DialogTitle>{selected?.name}</DialogTitle></DialogHeader>{selected && <div className="space-y-3 text-sm"><div className="grid gap-2 sm:grid-cols-3"><span>PID<br/><strong>{selected.pid}</strong></span><span>Ports<br/><strong>{selected.ports?.join(', ') || '—'}</strong></span><span>Started<br/><strong>{timestamp(selected.started_at)}</strong></span></div><div><div className="mb-1 text-xs text-fg-muted">Command</div><code className="block rounded bg-bg-panel p-2 text-xs">{selected.command}</code></div><div><div className="mb-1 text-xs text-fg-muted">Recent stdout</div><pre className="max-h-[45vh] overflow-auto whitespace-pre-wrap rounded bg-bg-panel p-3 text-xs">{output || 'No stdout captured.'}</pre></div></div>}</DialogContent></Dialog>
  </div>;
};

export default ProcessManagerPanel;
