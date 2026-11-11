/**
 * The Process Manager's panel: the processes this workflow started, with
 * their ports, output and controls.
 *
 * The head counts them by state. Under it a search narrows the table, and a
 * row's eye opens the process (its command and its recent output) in one of
 * the editor's modals.
 *
 * Colour marks state only (theme_system.md, "Colour marks state or role"):
 * a state is a dot beside neutral words, numbers are ink, and every button
 * is neutral.
 */

import React, { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Eye, RefreshCw, RotateCw, Search, Square, Terminal } from 'lucide-react';
import { toast } from 'sonner';

import { useWebSocket } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import Modal from '@/components/ui/Modal';
import type { StatusTone } from '@/features/home/data/presentation';
import { cn } from '@/lib/utils';
import { NodePanel } from './NodePanel';
import { StateMark, StatePill } from './StatePill';

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

const COLUMNS = ['Name', 'Status', 'PID', 'Ports', 'Started', 'Elapsed', 'Output', 'Command'];

const NEUTRAL = 'border-border-default text-fg-default';
const CARD = 'rounded-lg border border-border-default bg-bg-elevated';
const HEAD_CELL = 'border-b border-border-default px-3 py-2 font-medium whitespace-nowrap';
const MONO_CELL = 'px-3 py-2.5 font-mono text-xs whitespace-nowrap text-fg-muted tabular-nums';
const HEADING = 'font-display text-sm font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]';
const CODE = 'overflow-auto rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-3 font-mono text-xs break-words whitespace-pre-wrap text-[var(--code-text)]';

/** A process's state: running is work under way, an error wants someone. */
const TONE: Partial<Record<string, StatusTone>> = { running: 'working', error: 'attention' };

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

function ProcessState({ status }: { status: string }) {
  return <StatePill tone={TONE[status] ?? 'paused'} pulse={status === 'running'}>{status}</StatePill>;
}

/** One count in the head: a state's mark and name over its number. */
function Count({ tone, label, children }: { tone: StatusTone; label: string; children: ReactNode }) {
  return (
    <div className={cn(CARD, 'px-3 py-2')}>
      <dt className="flex items-center gap-1.5 text-xs text-fg-muted"><StateMark tone={tone} />{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold text-fg-default tabular-nums">{children}</dd>
    </div>
  );
}

/** One labelled fact in the opened process, in the mono face. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="mt-0.5 font-mono text-xs break-words text-fg-default tabular-nums">{children}</dd>
    </div>
  );
}

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
    // Never show the previously opened process's output under this one's name.
    setOutput('');
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

  /** How long a process has run: to now while it runs, to its stop after. */
  const elapsedOf = (process: ManagedProcess) => duration(
    process.started_at,
    process.status === 'running' || !process.stopped_at ? now : new Date(process.stopped_at).getTime(),
  );

  return (
    <NodePanel
      title="Managed Processes"
      description="The processes this workflow started, with their ports, output and controls."
      actions={(
        <Button type="button" variant="quiet" className={NEUTRAL} disabled={loading} onClick={() => void refresh()}>
          <RefreshCw className={cn(loading && 'animate-spin')} /> Refresh
        </Button>
      )}
      summary={(
        <dl className="grid grid-cols-3 gap-2.5">
          <Count tone="working" label="Running">
            {running} <span className="font-normal text-fg-faint">/ {maxProcesses}</span>
          </Count>
          <Count tone="paused" label="Stopped">{processes.length - running - failed}</Count>
          <Count tone="attention" label="Errors">{failed}</Count>
        </dl>
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col p-4">
        <div className="relative mb-3 shrink-0">
          <Search aria-hidden className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-muted" />
          <Input
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search name, command, directory, or port"
            aria-label="Search processes"
          />
        </div>
        {/* The table is as tall as its rows, and scrolls once they outgrow the panel. */}
        <div className="flex min-h-0 flex-1 flex-col">
          <div className={cn(CARD, 'min-h-0 overflow-auto')}>
            <table className="w-full min-w-[960px] border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-bg-panel text-left text-xs text-fg-muted">
                <tr>
                  {COLUMNS.map((label) => <th key={label} scope="col" className={HEAD_CELL}>{label}</th>)}
                  <th scope="col" className={HEAD_CELL}><span className="sr-only">Controls</span></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((process) => (
                  <tr key={process.name} className="border-b border-border-default last:border-b-0 hover:bg-bg-hover">
                    <td className="px-3 py-2.5 font-medium text-fg-default">{process.name}</td>
                    <td className="px-3 py-2.5"><ProcessState status={process.status} /></td>
                    <td className={MONO_CELL}>{process.pid}</td>
                    <td className={MONO_CELL}>{process.ports?.join(', ') || '—'}</td>
                    <td className={MONO_CELL}>{timestamp(process.started_at)}</td>
                    <td className={MONO_CELL}>{elapsedOf(process)}</td>
                    <td className={MONO_CELL}>{process.stdout_lines ?? 0} out · {process.stderr_lines ?? 0} err</td>
                    <td className="max-w-[320px] truncate px-3 py-2.5 font-mono text-xs text-fg-default" title={process.command}>
                      {process.command}
                    </td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                      <Button type="button" variant="quiet" size="icon-sm" aria-label={`Inspect ${process.name}`} title="Inspect" onClick={() => void loadOutput(process)}>
                        <Eye />
                      </Button>
                      {process.status === 'running' && (
                        <Button type="button" variant="quiet" size="icon-sm" disabled={busy === process.name} aria-label={`Stop ${process.name}`} title="Stop" onClick={() => void mutate('process_stop', process)}>
                          <Square />
                        </Button>
                      )}
                      <Button type="button" variant="quiet" size="icon-sm" disabled={busy === process.name} aria-label={`Restart ${process.name}`} title="Restart" onClick={() => void mutate('process_restart', process)}>
                        <RotateCw />
                      </Button>
                    </td>
                  </tr>
                ))}
                {!loading && visible.length === 0 && (
                  <tr>
                    <td colSpan={COLUMNS.length + 1} className="p-12 text-center text-fg-muted">
                      {processes.length === 0 ? 'No managed processes in this workflow.' : 'No processes match this search.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <Modal
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title="Process"
        titleIcon={<Terminal className="h-4 w-4 text-fg-muted" />}
        maxWidth="min(760px, calc(100vw - 2rem))"
        maxHeight="min(85vh, 720px)"
        autoHeight
      >
        {selected && (
          <div className="flex flex-col gap-5 px-5 py-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <h3 className={cn(HEADING, 'min-w-0 text-base break-words')}>{selected.name}</h3>
              <ProcessState status={selected.status} />
            </div>
            <dl className={cn(CARD, 'grid grid-cols-2 gap-x-4 gap-y-3 p-3.5 sm:grid-cols-4')}>
              <Fact label="PID">{selected.pid}</Fact>
              <Fact label="Ports">{selected.ports?.join(', ') || '—'}</Fact>
              <Fact label="Started">{timestamp(selected.started_at)}</Fact>
              <Fact label="Elapsed">{elapsedOf(selected)}</Fact>
            </dl>
            <section className="flex flex-col gap-1.5">
              <h4 className={HEADING}>Command</h4>
              <pre className={CODE}>{selected.command}</pre>
            </section>
            <section className="flex flex-col gap-1.5">
              <h4 className={HEADING}>Recent stdout</h4>
              <pre className={cn(CODE, 'max-h-[45vh]')}>{output || 'No stdout captured.'}</pre>
            </section>
          </div>
        )}
      </Modal>
    </NodePanel>
  );
};

export default ProcessManagerPanel;
