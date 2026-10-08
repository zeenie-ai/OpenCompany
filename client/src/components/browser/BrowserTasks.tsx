import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { buildApiUrl } from '@/config/api';
import type { WorkspaceFileRef } from '@/types/workspaceFiles';
import { createBrowserAgent } from '@/services/browserAgentApi';
import { useAppStore } from '@/store/useAppStore';

interface Agent { node_id: string; label: string }
interface Task {
  invocation_id: string; submission_id: string; node_id: string; prompt: string; status: string;
  result?: { response?: string; artifacts?: WorkspaceFileRef[]; error?: string } | null;
  created_at?: string;
  owner_available?: boolean; detail?: string;
}
const active = new Set(['queued', 'accepted', 'pending', 'running', 'started', 'waiting', 'cancel_requested']);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildApiUrl(path), { credentials: 'include', ...init });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Browser task request failed.');
  return body;
}

export default function BrowserTasks({ workflowId, browserNodeId, visible }: { workflowId: string; browserNodeId: string; visible: boolean }) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [selected, setSelected] = useState('');
  const agentId = agents?.some((agent) => agent.node_id === selected) ? selected : agents?.[0]?.node_id;
  const [prompt, setPrompt] = useState('');
  const [items, setItems] = useState<Task[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const pending = useRef<{ prompt: string; agent: string; id: string } | null>(null);
  const olderPages = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const discover = useCallback(async () => {
    const query = new URLSearchParams({ workflow_id: workflowId, browser_node_id: browserNodeId });
    const result = await request<{ agents: Agent[] }>(`/api/browser/tasks/discovery?${query}`);
    if (mounted.current) setAgents(result.agents);
  }, [workflowId, browserNodeId]);
  const history = useCallback(async (next?: string) => {
    const query = new URLSearchParams({ workflow_id: workflowId, limit: '20' });
    if (agentId) query.set('node_id', agentId);
    if (next) query.set('cursor', next);
    const result = await request<{ items: Task[]; next_cursor: string | null }>(`/api/browser/tasks/history?${query}`);
    if (mounted.current) {
      setItems((previous) => next ? [...previous, ...result.items.filter((item) => !previous.some((old) => old.invocation_id === item.invocation_id))]
        : olderPages.current ? [...result.items, ...previous.filter((old) => !result.items.some((item) => item.invocation_id === old.invocation_id))] : result.items);
      if (next || !olderPages.current) setCursor(result.next_cursor);
      if (next) olderPages.current = true;
    }
  }, [workflowId, agentId]);
  useEffect(() => { void discover().catch(() => { if (mounted.current) setError('Browser tasks are temporarily unavailable.'); }); }, [discover]);
  useEffect(() => { olderPages.current = false; setItems([]); setCursor(null); }, [agentId]);
  useEffect(() => {
    if (!visible || !agentId) return;
    let polling = false;
    const update = async () => {
      if (polling) return;
      polling = true;
      try { await history(); } catch { /* A subsequent refresh resynchronizes history. */ }
      finally { polling = false; }
    };
    void update();
    const timer = setInterval(() => void update(), 3000);
    return () => clearInterval(timer);
  }, [visible, agentId, history]);

  const addAgent = async () => {
    setBusy(true); setError('');
    try {
      const result = await createBrowserAgent({ workflow_id: workflowId, browser_node_id: browserNodeId });
      useAppStore.getState().adoptSavedOperations(workflowId, result.operations);
      await discover();
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : 'Could not add the Browser AI Agent.'); }
    finally { if (mounted.current) setBusy(false); }
  };
  const submit = async () => {
    if (!agentId || !prompt.trim()) return;
    setBusy(true); setError('');
    if (pending.current?.prompt !== prompt || pending.current?.agent !== agentId) pending.current = { prompt, agent: agentId, id: crypto.randomUUID() };
    try {
      await request('/api/browser/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow_id: workflowId, agent_node_id: agentId, prompt, submission_id: pending.current.id }) });
      pending.current = null;
      if (mounted.current) { setPrompt(''); setExpanded(true); }
      await history();
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : 'Could not submit the task.'); }
    finally { if (mounted.current) setBusy(false); }
  };
  const cancel = async (task: Task) => {
    setError('');
    try {
      const query = new URLSearchParams({ workflow_id: workflowId, agent_node_id: task.node_id });
      await request(`/api/browser/tasks/${encodeURIComponent(task.submission_id)}?${query}`, { method: 'DELETE' });
      await history();
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : 'Could not cancel the task.'); }
  };

  if (agents === null) return <section aria-label="Browser AI tasks" className="shrink-0 border-t border-border-default px-3 py-2 text-xs text-fg-muted">
    <p>{error || 'Loading Browser AI Agent…'}</p>
    {error && <Button size="sm" variant="outline" onClick={() => { setError(''); void discover().catch(() => setError('Browser tasks are temporarily unavailable.')); }}>Retry task connection</Button>}
  </section>;
  return <section aria-label="Browser AI tasks" className="shrink-0 border-t border-border-default bg-bg-panel px-3 py-2 text-xs">
    {!agentId ? <div className="flex items-center justify-between gap-3"><span className="text-fg-muted">Give this browser a task with a Browser AI Agent.</span><Button size="sm" variant="outline" disabled={busy} onClick={() => void addAgent()}>{busy ? 'Adding…' : 'Add Browser AI Agent'}</Button></div> : <>
      {agents.length > 1 && <select aria-label="Browser AI Agent" value={agentId} onChange={(event) => setSelected(event.target.value)} className="mb-2 w-full rounded border border-border-default bg-bg-panel p-1">{agents.map((agent) => <option key={agent.node_id} value={agent.node_id}>{agent.label || 'Browser AI Agent'}</option>)}</select>}
      <form className="flex items-end gap-2" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <textarea aria-label="Browser task" placeholder="Describe a task for this browser…" maxLength={20000} rows={2} className="min-w-0 flex-1 resize-y rounded border border-border-default bg-bg-panel p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring" value={prompt} onChange={(event) => setPrompt(event.target.value)} disabled={busy} />
        <Button type="submit" size="sm" disabled={busy || !prompt.trim()}>{busy ? 'Submitting…' : 'Run task'}</Button>
      </form>
      <button type="button" className="mt-2 text-fg-muted hover:text-fg-default" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>Recent tasks{items.some((item) => active.has(item.status)) ? ' · task in progress' : ''}</button>
      {expanded && <div className="mt-2 max-h-60 space-y-2 overflow-y-auto">
        {items.length === 0 && <p className="text-fg-muted">No recent browser tasks.</p>}
        {items.map((task) => <article key={task.invocation_id} className="rounded border border-border-default p-2">
          <div className="flex items-start justify-between gap-2"><p className="whitespace-pre-wrap break-words">{task.prompt}</p>{active.has(task.status) && <Button size="sm" variant="ghost" onClick={() => void cancel(task)}>Cancel task</Button>}</div>
          <p className="mt-1 text-fg-muted">{task.status.replaceAll('_', ' ')}</p>
          {task.owner_available === false && active.has(task.status) && <p className="mt-1 text-fg-muted">{task.detail || 'Browser unavailable — waiting for its owner.'}</p>}
          {task.result?.response && <p className="mt-2 whitespace-pre-wrap break-words">{task.result.response}</p>}
          {task.result?.error && <p className="mt-2 text-destructive">{task.result.error}</p>}
          {task.result?.artifacts?.map((file) => file.url?.startsWith('/api/') ? <a key={file.path} href={buildApiUrl(file.url)} download={file.filename} className="mt-1 block underline">{file.filename}</a> : null)}
        </article>)}
        {cursor && <Button size="sm" variant="outline" onClick={() => void history(cursor).catch(() => setError('Could not load older tasks.'))}>Load older tasks</Button>}
      </div>}
    </>}
    {error && <p role="alert" className="mt-2 text-destructive">{error}</p>}
  </section>;
}
