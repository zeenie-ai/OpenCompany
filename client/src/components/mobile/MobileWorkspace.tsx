import { PhoneActivity } from './PhoneActivity';
import { FullView } from '../workspace/FullView';
import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { PointerEvent, ReactNode, Ref } from 'react';
import { ArrowLeft, Home, LayoutList, Play, RotateCw, RotateCcwSquare, ScanLine, Smartphone, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { buildApiUrl } from '@/config/api';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { mobilePath, mobilePoint, mobileRequest, type Doctor, type Geometry, type Invocation, type MobileStatus } from './api';
import type { SurfaceControl } from '../workspace/surface';

const fieldClass = 'min-w-0 rounded border border-border-default bg-bg-input px-2 py-1.5 text-sm text-fg-default outline-none focus-visible:ring-2 focus-visible:ring-ring';
const finished = new Set(['completed', 'success', 'succeeded', 'failed', 'cancelled', 'canceled']);
const taskState = (task: Invocation | null) => task?.status ?? task?.state ?? '';
const describe = (value: unknown): string => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);

export default function MobileWorkspace({ workflowId, nodes, visible = true, canvasNodeId = null, notify, ref }: {
  workflowId?: string | null; nodes: { node_id: string; label: string }[]; visible?: boolean;
  /** The Workspace's Take over (SurfaceControl), for the phone shown. */
  ref?: Ref<SurfaceControl>;
  /** The Canvas board a screenshot goes to; without one there is no Screenshot to Canvas. */
  canvasNodeId?: string | null;
  /** A short message for the owner (Home: the pill toast; Dev: a toast). */
  notify?: (message: string, tone: 'success' | 'error') => void;
}) {
  const { addEventListener } = useWebSocketActions();
  const [selected, setSelected] = useState('');
  const [sessionVersion, setSessionVersion] = useState(0);
  const resetVersion = useRef(0);
  const isCurrentSession = useCallback(() => resetVersion.current === sessionVersion, [sessionVersion]);
  const nodeId = nodes.some((node) => node.node_id === selected) ? selected : nodes[0]?.node_id;
  useEffect(() => addEventListener('workflow_runtime_reset', (event) => {
    if (!workflowId || event?.workflow_id !== workflowId) return;
    // Invalidate pending responses immediately, before React unmounts the old
    // session. A delayed task submission must not restore its recovery ID.
    resetVersion.current += 1;
    for (const node of nodes) {
      try { sessionStorage.removeItem(`mobile-task:${workflowId}:${node.node_id}`); } catch { /* optional recovery */ }
    }
    setSessionVersion(resetVersion.current);
  }), [addEventListener, workflowId, nodes]);
  if (!workflowId || workflowId === 'new') return <Empty message="Save this workflow before opening its mobile workspace." />;
  if (!nodeId) return <Empty message="Add a Mobile Agent or Android tool to this workflow to use its phone here." />;
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
    {nodes.length > 1 && <select className={`${fieldClass} mx-2 mt-2 shrink-0`} aria-label="Mobile agent" value={nodeId} onChange={(event) => setSelected(event.target.value)}>
      {nodes.map((node) => <option key={node.node_id} value={node.node_id}>{node.label}</option>)}
    </select>}
    <MobileSession key={`${workflowId}:${nodeId}:${sessionVersion}`} workflowId={workflowId} nodeId={nodeId} visible={visible} isCurrentSession={isCurrentSession} canvasNodeId={canvasNodeId} notify={notify} ref={ref} />
  </div>;
}

function Empty({ message }: { message: string }) {
  return <div className="m-auto flex max-w-80 flex-col items-center gap-3 p-6 text-center text-sm text-fg-muted"><Smartphone aria-hidden className="size-6" />{message}</div>;
}

function MobileSession({ workflowId, nodeId, visible, isCurrentSession, canvasNodeId, notify, ref }: {
  workflowId: string; nodeId: string; visible: boolean; isCurrentSession: () => boolean;
  canvasNodeId: string | null; notify?: (message: string, tone: 'success' | 'error') => void; ref?: Ref<SurfaceControl>;
}) {
  const { sendRequest } = useWebSocketActions();
  const path = mobilePath(workflowId, nodeId);
  const [viewerId] = useState(() => crypto.randomUUID());
  const [status, setStatus] = useState<MobileStatus | null>(null);
  const [doctor, setDoctor] = useState<Doctor | null>(null);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [busy, setBusy] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [text, setText] = useState('');
  const [task, setTask] = useState<Invocation | null>(null);
  const [submission, setSubmission] = useState<string | null>(() => {
    try { return sessionStorage.getItem(`mobile-task:${workflowId}:${nodeId}`); } catch { return null; }
  });
  const [epoch, setEpoch] = useState<number | null>(null);
  const [leaseOwner, setLeaseOwner] = useState<string | null>(null);
  const [videoError, setVideoError] = useState('');
  const [videoAttempt, setVideoAttempt] = useState(0);
  const [live, setLive] = useState(false);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const canvas = useRef<HTMLCanvasElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const active = useRef(true);
  const operation = useRef(false);
  const leaseEpoch = useRef<number | null>(null);
  const viewActive = useRef(visible && pageVisible);
  viewActive.current = visible && pageVisible;
  const pendingSubmission = useRef<{ id: string; prompt: string } | null>(null);
  const pointer = useRef<{ x: number; y: number; at: number; geometry: Geometry } | null>(null);
  const held = status?.control_state === 'human' && leaseOwner !== null && status.controller === leaseOwner && epoch === status.epoch;
  const hasTask = !!submission && !finished.has(taskState(task));
  const running = status?.running === true;
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const next = await mobileRequest<MobileStatus>(`${path}/status`, undefined, signal);
    if (active.current && isCurrentSession() && !signal?.aborted) { setStatus(next); setConnectionError(''); }
  }, [path, isCurrentSession]);

  useEffect(() => {
    active.current = true;
    const changed = () => setPageVisible(!document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => { active.current = false; document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => {
    if (!visible || !pageVisible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(controller.signal); }
      catch { if (!controller.signal.aborted && active.current && isCurrentSession()) setConnectionError('Connection to the phone was lost. Retrying automatically…'); }
      if (!controller.signal.aborted && isCurrentSession()) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    void mobileRequest<Doctor>(`${path}/doctor`, undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted && isCurrentSession()) setDoctor(value);
    }).catch((cause) => { if (!controller.signal.aborted && isCurrentSession()) setError(cause.message); });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [visible, pageVisible, refresh, path, isCurrentSession]);
  useEffect(() => {
    if (!submission || !visible || !pageVisible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await mobileRequest<Invocation>(`${path}/tasks/${encodeURIComponent(submission)}`, undefined, controller.signal);
        if (!controller.signal.aborted && isCurrentSession()) setTask(next);
        if (!finished.has(taskState(next)) && !controller.signal.aborted && isCurrentSession()) timer = setTimeout(() => void poll(), 1500);
      } catch (cause) {
        if (!controller.signal.aborted && isCurrentSession()) { setError(cause instanceof Error ? cause.message : 'Could not read this task.'); timer = setTimeout(() => void poll(), 4000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [submission, visible, pageVisible, path, isCurrentSession]);
  useEffect(() => {
    if (!running || !visible || !pageVisible || !canvas.current) return;
    let disposed = false;
    let disconnect: (() => void) | undefined;
    setVideoError(''); setLive(false);
    const target = canvas.current;
    void import('./video').then(({ connectMobileVideo }) => {
      if (!disposed) disconnect = connectMobileVideo({ workflowId, nodeId, viewerId, canvas: target,
        onError: setVideoError, onLive: () => setLive(true) });
    }).catch(() => { if (!disposed) setVideoError('Could not load the video player. Reconnect to try again.'); });
    return () => { disposed = true; disconnect?.(); };
  }, [running, visible, pageVisible, workflowId, nodeId, viewerId, videoAttempt]);
  useEffect(() => {
    if (status?.setup !== 'ready') return;
    const controller = new AbortController();
    void mobileRequest<Doctor>(`${path}/doctor`, undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted) setDoctor(value);
    }).catch(() => {});
    return () => controller.abort();
  }, [status?.setup, path]);
  // Hiding the view relinquishes only this grant, never a later control session.
  useEffect(() => {
    if (!visible || !pageVisible) return;
    return () => {
      const releasedEpoch = leaseEpoch.current;
      leaseEpoch.current = null;
      // Reset cancels AI work but preserves a person's server-owned lease.
      if (releasedEpoch !== null && isCurrentSession()) void mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch: releasedEpoch, resume: false }).catch(() => {});
    };
  }, [visible, pageVisible, path, viewerId, isCurrentSession]);

  const perform = async (label: string, action: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true; setBusy(label); setError('');
    try { await action(); if (active.current && isCurrentSession() && label !== 'input') await refresh(); }
    catch (cause) { if (active.current && isCurrentSession()) setError(cause instanceof Error ? cause.message : 'That action failed.'); }
    finally { operation.current = false; if (active.current && isCurrentSession()) setBusy(''); }
  };
  const input = async (action: string, parameters: Record<string, unknown>, geometry = status?.geometry) => {
    if (!held || epoch === null || !geometry) return;
    await mobileRequest(`${path}/input`, { viewer_id: viewerId, epoch, operation_id: crypto.randomUUID(), operation: action, parameters, geometry });
  };
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = surface.current?.getBoundingClientRect();
    const geometry = status?.geometry;
    if (!rect || !geometry) return null;
    return mobilePoint(event.clientX - rect.left, event.clientY - rect.top, rect.width, rect.height, geometry);
  };
  const down = (event: PointerEvent<HTMLDivElement>) => {
    if (!held || busy || !live || videoError) return;
    const start = point(event);
    if (!start || !status?.geometry) return;
    pointer.current = { ...start, at: performance.now(), geometry: { ...status.geometry } };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const up = (event: PointerEvent<HTMLDivElement>) => {
    const start = pointer.current; pointer.current = null;
    const end = point(event);
    if (!start || !end || !held || !live || videoError) return;
    const duration = Math.max(100, Math.min(2000, Math.round(performance.now() - start.at)));
    void perform('input', () => Math.hypot(end.x - start.x, end.y - start.y) < 12
      ? input('tap', { x: end.x, y: end.y }, start.geometry)
      : input('swipe', { x: start.x, y: start.y, end_x: end.x, end_y: end.y, duration }, start.geometry));
  };
  const submit = () => perform('task', async () => {
    const clean = prompt.trim();
    if (!clean || hasTask) return;
    // A lost response retries the same task; changing the text makes a new submission.
    const attempt = pendingSubmission.current?.prompt === clean ? pendingSubmission.current : { id: crypto.randomUUID(), prompt: clean };
    pendingSubmission.current = attempt;
    const response = await mobileRequest<Invocation>(`${path}/tasks`, { submission_id: attempt.id, prompt: clean });
    if (!active.current || !isCurrentSession()) return;
    setSubmission(attempt.id); setTask(response); setPrompt(''); pendingSubmission.current = null;
    try { sessionStorage.setItem(`mobile-task:${workflowId}:${nodeId}`, attempt.id); } catch { /* optional recovery */ }
  });
  // Use phone, and the Workspace's Take over: the phone's lease for this view.
  const takeControl = async () => {
    const claim = await mobileRequest<{ epoch: number; owner: string }>(`${path}/takeover`, { viewer_id: viewerId });
    if (!active.current || !isCurrentSession() || !viewActive.current) {
      await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch: claim.epoch, resume: false });
      return false;
    }
    leaseEpoch.current = claim.epoch; setEpoch(claim.epoch); setLeaseOwner(claim.owner);
    return true;
  };
  // Hand back: give the phone back and let a task waiting for the owner go
  // on; without the lease (this view let it go) only the task resumes.
  const handBack = async () => {
    const held = leaseEpoch.current;
    if (held !== null) {
      await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch: held, resume: true });
      leaseEpoch.current = null; setEpoch(null);
    } else {
      await mobileRequest(`${path}/resume`, {});
    }
  };
  useImperativeHandle(ref, () => ({
    claim: () => takeControl().catch(() => false),
    release: () => { void handBack().catch((cause) => { if (active.current) setError(cause instanceof Error ? cause.message : 'Could not hand the phone back.'); }); },
  }));
  // The phone's screen as a PNG in the workspace, then on the Canvas board.
  const screenshot = () => perform('saving a screenshot', async () => {
    const { ref } = await mobileRequest<{ ref: { path: string } }>(`${path}/screenshot`, {});
    const added = await sendRequest<{ success?: boolean; error?: string }>('canvas_add', { workflow_id: workflowId, node_id: canvasNodeId, path: ref.path });
    if (added?.success === false) throw new Error(added.error || 'The screenshot could not go on the Canvas.');
    notify?.('Screenshot added to Canvas', 'success');
  });
  // Rotate turns a phone held upright on its side, and back.
  const rotate = () => perform('input', () => input('rotate', { orientation: status?.geometry?.rotation ? 'natural' : 'left' }));
  const installed = doctor?.adb && doctor?.emulator && doctor?.image && doctor?.engine && doctor?.video;
  const setupActive = typeof status?.setup === 'string' && status.setup.startsWith('installing_');
  const setupFailed = status?.setup === 'error' || status?.setup === 'interrupted' || !!status?.setup_error;
  const powerControl = (
    <Button size="sm" variant="outline" disabled={!!busy || setupActive || status?.starting || !status || doctor?.supported === false || (!running && !installed)} onClick={() => void perform(running ? 'stopping' : 'starting', async () => { await mobileRequest(`${path}/${running ? 'stop' : 'start'}`, {}); setEpoch(null); })}>
      {running ? <Square className="size-3.5" /> : <Play className="size-3.5" />}{running ? 'Stop phone' : 'Start phone'}
    </Button>
  );
  return <FullView label="Phone" toolbar={<>
      <Smartphone aria-hidden className="size-4 shrink-0 text-fg-muted" />
      <span role="status" className="min-w-0 flex-1 truncate text-xs text-fg-muted">{setupActive ? 'Setting up' : status?.starting ? 'Starting phone…' : busy ? `${busy}…` : running ? (held ? 'You’re using the phone' : status.active?.status === 'running' ? 'AI is working' : status.control_state === 'recovering' ? 'Reconnecting…' : 'Ready') : status ? 'Phone is off' : 'Connecting…'}</span>
      {running && status?.active?.run_id != null && <span role="status" className="text-xs text-fg-muted">{String(status.active.phase || status.active.status || 'Working')} · Engine step {Number(status.active.steps || 0)} / {Number(status.active.max_steps || 0)}</span>}
      {!running && powerControl}
      {running && (held ? <>
        <Button size="sm" onClick={() => void perform('releasing', async () => { await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch, resume: true }); leaseEpoch.current = null; setEpoch(null); })} disabled={!!busy}>Let AI continue</Button>
        <Button size="sm" variant="outline" onClick={() => void perform('releasing', async () => { await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch, resume: false }); leaseEpoch.current = null; setEpoch(null); })} disabled={!!busy}>Finish using phone</Button>
      </> : <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void perform('taking control', async () => { await takeControl(); })}>Use phone</Button>)}
    </>} controls={<>
      <div className="flex flex-wrap items-center gap-2">
        {running && powerControl}
        <Button size="icon-sm" variant="ghost" aria-label="Refresh mobile status" onClick={() => void perform('refresh', async () => { setDoctor(await mobileRequest(`${path}/doctor`)); })}><RotateCw /></Button>
      </div>
      <p className="m-0 text-xs text-fg-muted">{held ? 'Tap or swipe the screen, just like a phone. Finish when you want the AI to use it.' : 'Watch your phone here. Choose Use phone to tap and type yourself.'}</p>
      {held && <label className="flex items-center gap-2 text-xs text-fg-muted">Install an app file (.apk, up to 256 MB)
        <input type="file" accept=".apk" aria-label="Install APK" disabled={!!busy} className="min-w-0 flex-1 text-xs" onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = '';
          if (!file) return;
          if (!file.name.toLowerCase().endsWith('.apk') || file.size > 256 * 1024 * 1024) { setError('Choose an APK file no larger than 256 MB.'); return; }
          void perform('installing APK', async () => {
            const query = new URLSearchParams({ viewer_id: viewerId, epoch: String(epoch), operation_id: crypto.randomUUID(), filename: file.name });
            const response = await fetch(buildApiUrl(`${path}/apk?${query}`), { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/vnd.android.package-archive' }, body: file });
            if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(typeof body?.detail === 'string' ? body.detail : 'APK installation failed.'); }
          });
        }} />
      </label>}
      {status?.setup === 'ready' && installed && !setupFailed && status.setup_progress && <details aria-label="Completed mobile setup" className="rounded border border-border-default p-3 text-xs text-fg-muted"><summary className="cursor-pointer">Setup finished · View details</summary>
        <SetupProgress status={status} ticking={visible && pageVisible} />
      </details>}
      {doctor?.acceleration && <details className="rounded border border-border-default px-3 py-2 text-xs text-fg-muted">
        <summary className="cursor-pointer font-medium">Help &amp; diagnostics</summary>
        <p className="mb-0 mt-2">If the phone will not start, share the details below when asking for help.</p><pre className="mb-0 mt-2 whitespace-pre-wrap break-words">{doctor.acceleration}</pre>
        <p className="mb-0 mt-2">If acceleration is unavailable on Windows, enable virtualization in your computer’s firmware and Windows Hypervisor Platform in Windows Features, then restart Windows and refresh mobile status.</p>
        {!!status?.diagnostics?.length && <details className="mt-2"><summary className="cursor-pointer">Recent phone activity</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap">{status.diagnostics.map((item) => `${item.at} ${item.level} ${item.event}${item.operation ? ` (${item.operation})` : ''}${item.error_type ? `: ${item.error_type}` : ''}${item.code ? ` [${item.code}]` : ''}`).join('\n')}</pre></details>}
      </details>}
    </>}>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {(connectionError || error || setupFailed || doctor?.supported === false || !running || setupActive) && <div className={`space-y-2 overflow-y-auto p-2 ${running ? 'max-h-[40%] shrink-0' : 'min-h-0 flex-1'}`}>
        {connectionError && <p role="status" className="m-0 text-sm text-fg-muted">{connectionError}</p>}
        {error && <p role="alert" className="m-0 rounded border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive"><span>{error}</span><button type="button" aria-label="Dismiss message" className="ml-3 underline" onClick={() => setError('')}>Dismiss</button></p>}
        {setupFailed && <p role="alert" className="m-0 text-sm text-destructive">{status?.setup_error || (status?.setup === 'interrupted' ? 'Setup was interrupted. Retry to finish preparing the phone.' : 'Setup failed. Review the details below and retry.')}</p>}
        {doctor?.supported === false && <p className="m-0 text-sm text-fg-muted">This host cannot run the mobile runtime. Android setup currently requires Windows x64 with virtualization available.</p>}
        {((doctor && !installed && !running) || setupActive || setupFailed) && <section aria-label="Mobile setup" className="space-y-2 rounded border border-border-default bg-bg-panel p-3">
          <h3 className="m-0 text-sm font-semibold">Set up your Android phone</h3>
          <p className="m-0 text-xs text-fg-muted">One-time setup downloads everything your phone needs. This may take several minutes. Your apps and sign-ins are saved.</p>
          <details><summary className="cursor-pointer text-xs text-fg-muted">Download details</summary><ul className="m-0 list-none space-y-1 p-0 text-xs text-fg-muted">{(['adb', 'emulator', 'image', 'engine', 'video'] as const).map((key) => <li key={key}>{key === 'adb' ? 'Android tools' : key === 'engine' ? 'Agent runtime' : key === 'video' ? 'Live video' : key === 'image' ? 'Android image' : 'Emulator'}: {doctor?.[key] ? 'Ready' : 'Needed'}</li>)}</ul></details>
          <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} aria-label="Accept Android SDK license terms" />
            <span>I accept the <a className="underline" href="https://developer.android.com/studio/terms" target="_blank" rel="noreferrer">Android SDK license terms</a> and want to download the required components.</span></label>
          <Button size="sm" disabled={!accepted || !!busy || setupActive || doctor?.supported === false} onClick={() => void perform('setup', async () => {
            await mobileRequest(`${path}/setup`, { licenses_accepted: true }); setDoctor(await mobileRequest(`${path}/doctor`));
          })}>{setupFailed ? 'Retry setup' : 'Set up phone'}</Button>
          {(setupActive || setupFailed || status?.setup_progress) && <SetupProgress status={status} ticking={visible && pageVisible} />}
        </section>}
        {!error && status?.start_error && <p role="alert" className="m-0 text-sm text-destructive">{status.start_error}</p>}
        {!running && !status?.starting && installed && !status?.start_error && <p className="m-0 text-sm text-fg-muted">Your phone is ready to turn on. Click Start phone to use it.</p>}
      </div>}
      {running && <>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center gap-2.5 p-3">
          {status.device && <p role="status" className="m-0 flex shrink-0 items-center gap-2 font-mono text-2xs text-fg-muted"><span aria-hidden className="size-1.5 rounded-full bg-action-run-ink" />{status.device}</p>}
          <PhoneFrame geometry={status.geometry}>
            <div ref={surface} className={`relative h-full w-full touch-none overflow-hidden rounded-(--radius-phone-screen) bg-(--phone-bezel) ${held ? 'cursor-crosshair' : ''}`} onPointerDown={down} onPointerUp={up} onPointerCancel={() => { pointer.current = null; }} aria-label={held ? 'Phone screen: tap or drag to interact' : 'Phone screen, view only'}>
              <canvas ref={canvas} className="absolute inset-0 h-full w-full object-contain" />
              {!live && !videoError && <p className="pointer-events-none absolute inset-0 z-10 m-0 grid place-items-center text-sm text-fg-muted">Connecting live view…</p>}
            </div>
          </PhoneFrame>
          <div role="toolbar" aria-label="Phone buttons" className="flex shrink-0 items-center gap-0.5 rounded-pill border border-border-default bg-bg-panel p-0.75">
            <DeviceButton label="Back" disabled={!held || !!busy} onClick={() => void perform('input', () => input('key', { key: 'back' }))}><ArrowLeft /></DeviceButton>
            <DeviceButton label="Home" disabled={!held || !!busy} onClick={() => void perform('input', () => input('key', { key: 'home' }))}><Home /></DeviceButton>
            <DeviceButton label="Recent apps" disabled={!held || !!busy} onClick={() => void perform('input', () => input('key', { key: 'recent' }))}><LayoutList /></DeviceButton>
            <span aria-hidden className="mx-1 h-4.5 w-px bg-border-default" />
            {canvasNodeId && <DeviceButton label="Screenshot to Canvas" disabled={!!busy} onClick={() => void screenshot()}><ScanLine /></DeviceButton>}
            <DeviceButton label="Rotate" disabled={!held || !!busy} onClick={() => void rotate()}><RotateCcwSquare /></DeviceButton>
          </div>
        </div>
        {videoError && <div className="flex shrink-0 items-center gap-2 p-2"><p role="alert" className="m-0 flex-1 text-xs text-fg-muted">{videoError}</p><Button size="sm" variant="outline" onClick={() => setVideoAttempt((value) => value + 1)}>Reconnect</Button></div>}
        {held && <div className="flex shrink-0 flex-wrap gap-1 border-t border-border-default p-2">
          <input className={`${fieldClass} flex-1`} aria-label="Text to type on phone" value={text} onChange={(event) => setText(event.target.value)} placeholder="Type on phone" />
          <Button size="sm" variant="outline" disabled={!text || !!busy} onClick={() => void perform('input', async () => { await input('text', { text }); setText(''); })}>Type</Button>
        </div>}
      </>}
      <PhoneActivity task={status?.active?.run_id ? status.active : status?.last_task} />
      <details aria-label="Phone AI task" className="max-h-[45%] shrink-0 overflow-y-auto border-t border-border-default group-data-[full-view=true]/fullview:hidden">
        <summary className="cursor-pointer px-2 py-2 text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">Ask AI to use the phone
          {task && <span role="status" className="ml-2 font-normal text-fg-muted">{({queued: 'Waiting for the phone', running: 'Working on your request', completed: 'Done', failed: 'Could not finish', cancelled: 'Cancelled', awaiting_user: 'Waiting for you'})[taskState(task)] || 'Request received'}</span>}
        </summary>
        <form className="flex flex-col gap-2 p-2" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <label htmlFor={`task-${nodeId}`} className="sr-only">Ask AI to use the phone</label>
          <textarea id={`task-${nodeId}`} className={`${fieldClass} resize-none`} rows={2} maxLength={12000} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="For example: Open Settings and turn on dark mode" />
          <p className="m-0 text-xs text-fg-muted">Uses your global AI model unless this phone has a model override. Only one person or AI can use the phone at a time.</p>
          <div className="flex items-center gap-2"><Button size="sm" type="submit" disabled={!running || !prompt.trim() || !!busy || hasTask}>Run task</Button>
            {hasTask && <Button size="sm" type="button" variant="outline" disabled={!!busy} onClick={() => void perform('cancelling', async () => { setTask(await mobileRequest(`${path}/tasks/${encodeURIComponent(submission!)}/cancel`, {})); })}>Cancel task</Button>}
          </div>
          {task?.error != null && <p role="alert" className="m-0 break-words text-sm text-destructive">{describe(task.error)}</p>}
          {task?.result != null && <pre className="m-0 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg-panel p-2 text-xs">{describe(task.result)}</pre>}
        </form>
      </details>
    </div>
  </FullView>;
}

/** The phone around the live screen: a bezel shaped by the screen's width
 *  and height (it turns with the phone), as large as the view allows. */
function PhoneFrame({ geometry, children }: { geometry?: Geometry | null; children: ReactNode }) {
  const ratio = geometry && geometry.width > 0 && geometry.height > 0 ? geometry.width / geometry.height : null;
  return <div className="flex min-h-0 w-full flex-1 items-center justify-center [container-type:size]">
    <div className="box-border overflow-hidden rounded-(--radius-phone) border-7 border-(--phone-bezel) bg-(--phone-bezel) shadow-(--shadow-phone)"
      style={ratio ? { aspectRatio: String(ratio), width: `min(100cqw, min(100cqh, var(--h-phone-max)) * ${ratio})` } : { width: '100%', height: '100%' }}>
      {children}
    </div>
  </div>;
}

function DeviceButton({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: ReactNode }) {
  return <Button size="icon-sm" variant="quiet" aria-label={label} title={label} disabled={disabled} onClick={onClick} className="size-7.5 rounded-lg [&_svg]:size-4">{children}</Button>;
}

function SetupProgress({ status, ticking }: { status: MobileStatus | null; ticking: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now() / 1000);
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  const progress = status?.setup_progress;
  const phase = status?.setup === 'installing_engine' ? 'Installing agent runtime'
    : status?.setup === 'installing_device' ? 'Preparing Android phone'
    : status?.setup === 'interrupted' ? 'Setup interrupted'
    : status?.setup === 'error' ? 'Setup failed' : status?.setup === 'ready' ? 'Setup complete' : 'Setup progress';
  const duration = (seconds: number) => {
    const value = Math.max(0, Math.floor(seconds));
    return value < 60 ? `${value}s` : `${Math.floor(value / 60)}m ${value % 60}s`;
  };
  const events = progress?.events?.slice(-20) ?? [];
  return <div className="space-y-1 text-xs text-fg-muted">
    <p role="status" className="m-0 font-medium">{phase}</p>
    {progress?.message && <p className="m-0 break-words">{progress.message}</p>}
    <p className="m-0">
      {progress?.started_at != null && <span>Elapsed: {duration((progress.finished_at ?? now) - progress.started_at)}</span>}
      {progress?.started_at != null && progress?.updated_at != null && ' · '}
      {progress?.updated_at != null && <span>Last update: {duration(now - progress.updated_at)} ago</span>}
    </p>
    {events.length > 0 && <details><summary className="cursor-pointer">Recent setup activity</summary>
      <ol className="my-2 max-h-40 list-none space-y-1 overflow-y-auto p-0">
        {events.map((event, index) => <li key={`${event.at}:${index}`} className="break-words"><time>{new Date(event.at * 1000).toLocaleTimeString()}</time> — {event.message}</li>)}
      </ol>
    </details>}
  </div>;
}
