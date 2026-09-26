import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { ArrowLeft, ArrowRight, Globe, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { buildApiUrl } from '@/config/api';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { browserModifiers, browserPoint, decodeBrowserFrame, type BrowserFrameHeader } from './protocol';

export interface BrowserWorkspaceProps {
  workflowId?: string | null;
  nodes: { node_id: string; label: string }[];
  visible?: boolean;
}
type Phase = 'connecting' | 'idle' | 'live' | 'error';
interface BrowserState {
  state: string;
  controller?: string | null;
  request?: { message?: string; reason?: string } | null;
}
interface Tab { target_id: string; title?: string; url?: string; active?: boolean }
const inputClass = 'min-w-0 rounded border border-border-default bg-bg-panel px-2 py-1 text-xs text-fg-default outline-none focus-visible:ring-2 focus-visible:ring-ring';

export default function BrowserWorkspace({ workflowId, nodes, visible = true }: BrowserWorkspaceProps) {
  const [selected, setSelected] = useState('');
  const nodeId = nodes.some((n) => n.node_id === selected) ? selected : nodes[0]?.node_id;
  if (!workflowId) return <EmptyBrowser message="Save this workflow to open its browser." />;
  if (!nodeId) return <EmptyBrowser message="Add a Browser node to this workflow to watch it here." />;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      {nodes.length > 1 && (
        <select aria-label="Browser node" className={inputClass} value={nodeId} onChange={(e) => setSelected(e.target.value)}>
          {nodes.map((node) => <option key={node.node_id} value={node.node_id}>{node.label || 'Browser'}</option>)}
        </select>
      )}
      <BrowserSessionView key={`${workflowId}:${nodeId}`} workflowId={workflowId} nodeId={nodeId} visible={visible} />
    </div>
  );
}

function EmptyBrowser({ message }: { message: string }) {
  return <div className="m-auto flex max-w-80 flex-col items-center gap-3 p-6 text-center text-sm text-fg-muted"><Globe aria-hidden className="size-6" />{message}</div>;
}

function BrowserSessionView({ workflowId, nodeId, visible }: { workflowId: string; nodeId: string; visible: boolean }) {
  const { sendRequest, isReady } = useWebSocketActions();
  const socketRef = useRef<WebSocket | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const frameRef = useRef<{ header: BrowserFrameHeader; width: number; height: number } | null>(null);
  const [phase, setPhase] = useState<Phase>('connecting');
  const [state, setState] = useState<BrowserState>({ state: 'idle' });
  const [error, setErrorText] = useState('');
  const streamErrorRef = useRef(false);
  const setError = useCallback((message: string, streamError = false) => {
    streamErrorRef.current = streamError;
    setErrorText(message);
  }, []);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [address, setAddress] = useState('');
  const [hasFrame, setHasFrame] = useState(false);
  const [starting, setStarting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [dialog, setDialog] = useState<{ kind: string; message: string } | null>(null);
  const [promptText, setPromptText] = useState('');
  const [takingControl, setTakingControl] = useState(false);
  const control = phase === 'live' && state.state === 'user' && state.controller === 'you';
  const controlRef = useRef(control);
  controlRef.current = control;
  const mountedRef = useRef(true);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }, []);

  const releaseControl = useCallback(() => {
    controlRef.current = false;
    setTakingControl(false);
    setState((previous) => ({ ...previous, controller: null }));
    send({ type: 'control_release' });
  }, [send]);

  useEffect(() => {
    if (!isReady) { setPhase('connecting'); return; }
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let pendingImage: HTMLImageElement | null = null;
    const urls = new Set<string>();
    let chain = Promise.resolve();
    let queuedFrames = 0;
    // Opt-in numeric summaries only: no page, frame, session or input payloads.
    const debug = new URLSearchParams(window.location.search).get('browserStreamDebug') === '1';
    const startedAt = performance.now();
    let paintedFrames = 0, decodeTotal = 0, drawTotal = 0;
    let terminalStreamError = false;
    let streamEpoch = 0;
    const viewport = () => {
      const rect = surfaceRef.current?.getBoundingClientRect();
      return { width: Math.max(80, rect?.width || 800), height: Math.max(60, rect?.height || 500), dpr: window.devicePixelRatio || 1 };
    };
    const effectiveVisible = () => visibleRef.current && document.visibilityState !== 'hidden';
    const transmit = (message: Record<string, unknown>) => {
      if (!disposed && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
    };
    setPhase('connecting'); setError(''); setHasFrame(false); frameRef.current = null;
    setState({ state: 'idle' }); setTabs([]); setAddress(''); setDialog(null); setTakingControl(false);
    const url = new URL(buildApiUrl('/ws/browser'), window.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url.toString());
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;
    socket.onopen = () => transmit({ type: 'attach', target: { kind: 'node', workflow_id: workflowId, node_id: nodeId }, viewport: viewport(), visible: effectiveVisible(), max_fps: 30 });
    socket.onmessage = (event) => {
      if (disposed) return;
      if (typeof event.data === 'string') {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'attached') setPhase(message.running ? 'live' : 'idle');
          if (message.type === 'state') { setState(message); if (!terminalStreamError) setPhase('live'); setTakingControl(false); }
          if (message.type === 'idle') { streamEpoch += 1; setPhase('idle'); setState({ state: 'idle' }); setHasFrame(false); frameRef.current = null; setDialog(null); }
          if (message.type === 'page') setAddress(message.url || '');
          if (message.type === 'tabs') setTabs(message.tabs || []);
          if (message.type === 'dialog') { setDialog(message); setPromptText(''); }
          if (message.type === 'error') {
            setError(message.message || 'Browser request failed.', message.code === 'screencast');
            if (message.code === 'screencast' && message.retrying === false) {
              terminalStreamError = true; streamEpoch += 1;
              setPhase('error'); setHasFrame(false); frameRef.current = null;
            }
          }
          if (message.type === 'control') {
            setTakingControl(false);
            if (!message.granted) setError(message.message || (message.reason === 'taken_over_elsewhere' ? 'Control moved to another viewer.' : 'Control could not be granted. Try again.'));
          }
        } catch { setError('The browser sent an invalid status message.'); }
        return;
      }
      // Serial decoding keeps older frames from painting over newer ones. Every
      // frame is acknowledged, including hidden frames and decode failures.
      const data = event.data as ArrayBuffer;
      const frameEpoch = streamEpoch;
      // The server normally permits two outstanding frames. Keep that bound
      // locally too if a faulty sender exceeds its credit; ACK dropped frames.
      if (queuedFrames >= 2) {
        let seq: number | undefined;
        try { seq = decodeBrowserFrame(data).header.seq; } catch { /* ACK malformed input too. */ }
        transmit({ type: 'ack', seq });
        return;
      }
      queuedFrames += 1;
      chain = chain.then(async () => {
        let seq: number | undefined;
        let objectUrl: string | undefined;
        try {
          if (disposed) return;
          const frame = decodeBrowserFrame(data); seq = frame.header.seq;
          if (frameEpoch !== streamEpoch) return;
          if (!effectiveVisible()) return;
          objectUrl = URL.createObjectURL(frame.jpeg); urls.add(objectUrl);
          const decodeStarted = performance.now();
          const image = new Image(); pendingImage = image;
          await new Promise<void>((resolve, reject) => {
            image.onload = () => resolve(); image.onerror = () => reject(new Error('Could not decode browser frame.'));
            image.src = objectUrl!;
          });
          if (disposed || frameEpoch !== streamEpoch || !effectiveVisible()) return;
          const decodedAt = performance.now();
          const canvas = canvasRef.current;
          if (!canvas) return;
          const context = canvas.getContext('2d');
          if (!context) throw new Error('This browser cannot display the live view.');
          if (canvas.width !== image.naturalWidth) canvas.width = image.naturalWidth;
          if (canvas.height !== image.naturalHeight) canvas.height = image.naturalHeight;
          context.drawImage(image, 0, 0);
          if (debug) {
            paintedFrames += 1;
            decodeTotal += decodedAt - decodeStarted;
            drawTotal += performance.now() - decodedAt;
            if (paintedFrames === 1 || paintedFrames % 60 === 0) {
              console.debug('[browser-stream]', {
                frames: paintedFrames,
                ...(paintedFrames === 1 ? { firstFrameMs: performance.now() - startedAt } : {}),
                meanDecodeMs: decodeTotal / paintedFrames, meanDrawMs: drawTotal / paintedFrames,
              });
            }
          }
          frameRef.current = { header: frame.header, width: image.naturalWidth, height: image.naturalHeight };
          setHasFrame(true); setPhase('live');
          terminalStreamError = false;
          if (streamErrorRef.current) setError('');
        } catch (cause) {
          if (!disposed) setError(cause instanceof Error ? cause.message : 'Could not read browser frame.', true);
        } finally {
          queuedFrames -= 1;
          pendingImage = null;
          if (objectUrl) { URL.revokeObjectURL(objectUrl); urls.delete(objectUrl); }
          transmit({ type: 'ack', seq });
        }
      });
    };
    socket.onerror = () => { if (!disposed) setError('Browser connection interrupted.'); };
    socket.onclose = (event) => {
      if (disposed) return;
      streamEpoch += 1;
      setPhase('error'); setHasFrame(false); frameRef.current = null; setTakingControl(false);
      setError(event.reason || 'Browser connection lost.');
      // Authentication/ownership failures need user action, not a retry loop.
      if (![4001, 4002, 4003, 4004].includes(event.code)) retryTimer = setTimeout(() => setAttempt((n) => n + 1), 2000);
    };
    const observer = new ResizeObserver(() => transmit({ type: 'viewport', ...viewport() }));
    if (surfaceRef.current) observer.observe(surfaceRef.current);
    const onVisibility = () => {
      transmit({ type: 'visibility', visible: effectiveVisible() });
      if (!effectiveVisible()) releaseControl();
    };
    window.addEventListener('blur', releaseControl);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      disposed = true; window.removeEventListener('blur', releaseControl); clearTimeout(retryTimer); observer.disconnect(); document.removeEventListener('visibilitychange', onVisibility);
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'control_release' }));
      socket.close(); if (socketRef.current === socket) socketRef.current = null;
      if (pendingImage) { pendingImage.onerror?.(new Event('error')); pendingImage.src = ''; }
      for (const objectUrl of urls) URL.revokeObjectURL(objectUrl);
      urls.clear();
    };
  }, [workflowId, nodeId, isReady, attempt, setError, releaseControl]);

  useEffect(() => {
    send({ type: 'visibility', visible: visible && document.visibilityState !== 'hidden' });
    if (!visible) releaseControl();
  }, [visible, send, releaseControl]);

  // Installer work outlives the finite open request; report that state and let
  // the owner retry once ready. Do not launch a browser merely by viewing it.
  useEffect(() => {
    if (!isReady || !visible || (!starting && !installing && phase !== 'idle')) return;
    let disposed = false;
    let inFlight = false;
    const check = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const status = await sendRequest<{ chrome?: { phase?: string }; cli?: { phase?: string } }>('browser_runtime_status');
        if (!disposed) setInstalling([status.chrome?.phase, status.cli?.phase].some((s) => s === 'installing' || s === 'downloading' || s === 'extracting'));
      } catch { /* The open request supplies actionable errors. */ }
      finally { inFlight = false; }
    };
    void check(); const timer = setInterval(() => void check(), 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, [starting, installing, phase, visible, isReady, sendRequest]);

  const start = async () => {
    setStarting(true); setError('');
    try {
      const result = await sendRequest<{ success: boolean; error?: string }>('browser_session_open', { workflow_id: workflowId, node_id: nodeId }, 60_000);
      if (!result.success) throw new Error(result.error || 'Could not start the browser.');
      if (mountedRef.current) setAttempt((n) => n + 1);
    } catch (cause) {
      if (mountedRef.current) setError(cause instanceof Error ? cause.message : 'Could not start the browser.');
    } finally { if (mountedRef.current) setStarting(false); }
  };
  const point = (clientX: number, clientY: number) => {
    const rect = surfaceRef.current?.getBoundingClientRect(); const frame = frameRef.current;
    return rect && frame ? browserPoint(clientX - rect.left, clientY - rect.top, rect.width, rect.height, frame.width, frame.height, frame.header) : null;
  };
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending: { x: number; y: number; delta_x: number; delta_y: number; modifiers: number } | null = null;
    const wheel = (event: WheelEvent) => {
      if (!controlRef.current) return;
      const frame = frameRef.current; if (!frame) return;
      const rect = surface.getBoundingClientRect();
      const coords = browserPoint(event.clientX - rect.left, event.clientY - rect.top, rect.width, rect.height, frame.width, frame.height, frame.header);
      if (!coords) return;
      event.preventDefault(); event.stopPropagation();
      const factor = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
      pending = { ...coords, delta_x: (pending?.delta_x || 0) + event.deltaX * factor, delta_y: (pending?.delta_y || 0) + event.deltaY * factor, modifiers: browserModifiers(event) };
      if (timer) return;
      timer = setTimeout(() => { if (pending && controlRef.current) send({ type: 'wheel', ...pending }); pending = null; timer = undefined; }, 25);
    };
    // React wheel listeners are passive; a native listener prevents scrolling
    // the surrounding app while the user scrolls the remote page.
    surface.addEventListener('wheel', wheel, { passive: false });
    return () => { surface.removeEventListener('wheel', wheel); clearTimeout(timer); };
  }, [send]);
  const lastMove = useRef(0);
  const lastPointer = useRef<{ x: number; y: number } | null>(null);
  const pointer = (event: PointerEvent<HTMLDivElement>, action: 'move' | 'down' | 'up') => {
    if (!control) return;
    const coords = point(event.clientX, event.clientY) || (action === 'up' ? lastPointer.current : null);
    if (!coords) return;
    lastPointer.current = coords;
    if (action === 'move' && Date.now() - lastMove.current < 25) return;
    lastMove.current = Date.now(); event.preventDefault();
    if (action === 'down') { event.currentTarget.focus(); event.currentTarget.setPointerCapture?.(event.pointerId); }
    if (action === 'up' && event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    send({ type: 'mouse', action, ...coords, button: ['left', 'middle', 'right'][event.button] || 'left', buttons: event.buttons, click_count: Math.max(1, event.detail), modifiers: browserModifiers(event) });
  };
  const key = (event: KeyboardEvent<HTMLDivElement>, action: 'down' | 'up') => {
    if (!control || event.nativeEvent.isComposing) return;
    // Let the browser's paste event expose clipboard text; do not also send V.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') return;
    event.preventDefault(); event.stopPropagation();
    send({ type: 'key', action, key: event.key, code: event.code, key_code: event.keyCode, location: event.location, repeat: event.repeat, modifiers: browserModifiers(event) });
  };
  const navigate = (action: string) => send({ type: 'navigate', action, ...(action === 'goto' ? { url: address } : {}) });
  const statusLabel = phase === 'connecting' ? 'Connecting…' : phase === 'idle' ? 'Browser is stopped' : phase === 'error' ? 'Disconnected' : control ? 'You have control' : state.state === 'agent' ? 'Employee is browsing' : state.state === 'awaiting_user' ? 'Your help is needed' : state.controller === 'other' ? 'Another viewer has control' : 'Live browser';

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span role="status" className="text-fg-muted">{installing ? 'Installing browser…' : starting ? 'Starting browser…' : statusLabel}</span>
        {phase === 'live' && <Button size="sm" variant="outline" disabled={takingControl} onClick={() => {
          setError('');
          if (control) send({ type: 'control_release' });
          else { setTakingControl(true); send({ type: 'control_request', ...(state.controller === 'other' ? { force: true } : {}) }); }
        }}>{control ? 'Hand back' : takingControl ? 'Taking control…' : state.controller === 'other' ? 'Take over here' : 'Take control'}</Button>}
      </div>
      {state.request && <div className="rounded border border-border-default p-2 text-sm text-fg-default">{state.request.message || state.request.reason || 'The employee needs your help.'}</div>}
      {error && <div role="alert" className="flex items-start gap-2 text-xs text-destructive"><span className="min-w-0 flex-1 break-words">{error}</span><button aria-label="Dismiss browser error" onClick={() => setError('')}>×</button></div>}
      <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); if (control) navigate('goto'); }}>
        <Button type="button" size="icon-sm" variant="ghost" aria-label="Back" disabled={!control} onClick={() => navigate('back')}><ArrowLeft className="size-4" /></Button>
        <Button type="button" size="icon-sm" variant="ghost" aria-label="Forward" disabled={!control} onClick={() => navigate('forward')}><ArrowRight className="size-4" /></Button>
        <Button type="button" size="icon-sm" variant="ghost" aria-label="Reload page" disabled={!control} onClick={() => navigate('reload')}><RotateCw className="size-4" /></Button>
        <input aria-label="Browser address" className={`${inputClass} flex-1`} value={address} disabled={!control} placeholder="Enter a website" onChange={(e) => setAddress(e.target.value)} />
      </form>
      {tabs.length > 1 && <select aria-label="Browser tab" className={inputClass} disabled={!control} value={tabs.find((tab) => tab.active)?.target_id || ''} onChange={(e) => send({ type: 'tab', action: 'activate', target_id: e.target.value })}>
        {tabs.map((tab) => <option key={tab.target_id} value={tab.target_id}>{tab.title || tab.url || 'New tab'}</option>)}
      </select>}
      <div ref={surfaceRef} role="group" aria-label="Live browser page" tabIndex={control ? 0 : -1}
        className="relative flex min-h-40 flex-1 items-center justify-center overflow-hidden rounded border border-border-default bg-bg-app outline-none focus-visible:ring-2 focus-visible:ring-ring"
        style={{ touchAction: control ? 'none' : 'auto' }}
        onPointerDown={(e) => pointer(e, 'down')} onPointerMove={(e) => pointer(e, 'move')} onPointerUp={(e) => pointer(e, 'up')} onPointerCancel={releaseControl}
        onContextMenu={(e) => { if (control) e.preventDefault(); }} onKeyDown={(e) => key(e, 'down')} onKeyUp={(e) => key(e, 'up')}
        onPaste={(e) => { if (control) { e.preventDefault(); send({ type: 'insert_text', text: e.clipboardData.getData('text/plain') }); } }}
        onCompositionEnd={(e) => { if (control && e.data) send({ type: 'insert_text', text: e.data }); }}>
        <canvas ref={canvasRef} aria-label="Browser screenshot" className="absolute inset-0 h-full w-full object-contain" style={{ visibility: hasFrame ? 'visible' : 'hidden' }} />
        {!hasFrame && <div className="relative flex max-w-80 flex-col items-center gap-3 p-6 text-center text-sm text-fg-muted"><Globe aria-hidden className="size-6" />
          <span>{phase === 'live' ? 'Waiting for the browser picture…' : phase === 'idle' ? 'The browser will appear when the employee uses it. You can also open it now.' : statusLabel}</span>
          {phase === 'idle' && <Button variant="outline" disabled={starting || installing || !isReady} onClick={() => void start()}>{starting ? 'Starting…' : installing ? 'Installing…' : 'Start browser'}</Button>}
          {phase === 'error' && <Button variant="outline" onClick={() => setAttempt((n) => n + 1)}>Reconnect</Button>}
        </div>}
      </div>
      {dialog && <div role="dialog" aria-label="Browser page dialog" className="flex flex-col gap-2 rounded border border-border-default bg-bg-panel p-3 text-sm">
        <p className="break-words text-fg-default">{dialog.message}</p>
        {dialog.kind === 'prompt' && <input aria-label="Dialog response" className={inputClass} value={promptText} onChange={(e) => setPromptText(e.target.value)} disabled={!control} />}
        <div className="flex gap-2">{[true, false].map((accept) => <Button key={String(accept)} size="sm" variant="outline" disabled={!control} onClick={() => { send({ type: 'dialog_reply', accept, prompt_text: promptText }); setDialog(null); }}>{accept ? 'OK' : 'Cancel'}</Button>)}</div>
        {!control && <span className="text-xs text-fg-muted">Take control to respond.</span>}
      </div>}
    </div>
  );
}
