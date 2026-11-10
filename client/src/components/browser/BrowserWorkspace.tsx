import { FullView, FullViewButton } from '../workspace/FullView';
import { TakeOverButton } from '../workspace/TakeOverButton';
import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode, Ref } from 'react';
import { ArrowLeft, ArrowRight, ExternalLink, Globe, Lock, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { buildApiUrl } from '@/config/api';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { browserModifiers, browserPoint, decodeBrowserFrame, parseAgentAction, type BrowserAgentAction, type BrowserFrameHeader } from './protocol';
import { AgentCursor, type PictureShape } from './AgentCursor';
import BrowserTasks from './BrowserTasks';
import type { SurfaceControl } from '../workspace/surface';

export interface BrowserWorkspaceProps {
  workflowId?: string | null;
  nodes: { node_id: string; label: string }[];
  visible?: boolean;
  /** The name on the agent's cursor (the employee's). */
  agentName?: string;
  /** A host that runs Take over itself (the Workspace dock, which also stops
   *  the employee and holds Hand back in its banner) puts its button here,
   *  or null to show none. It shows at the end of the row of page controls
   *  while the owner could take control and does not have it (a live
   *  browser, not in a protected login). Left out, the row holds the
   *  browser's own Take over and Hand back. */
  action?: ReactNode;
  /** Dev mode's extras: an address box to go to a page while in control,
   *  Full view, and Browser AI tasks under the page. */
  dev?: boolean;
  /** Dev mode: saves the canvas before Browser AI tasks adds an agent to it. */
  flushCanvas?: () => void;
}
type Phase = 'connecting' | 'idle' | 'live' | 'error';
interface BrowserState {
  state: string;
  sensitive_login?: boolean;
  controller?: string | null;
  request?: { message?: string; reason?: string } | null;
}
interface Tab { target_id: string; title?: string; url?: string; active?: boolean }
const inputClass = 'min-w-0 rounded border border-border-default bg-bg-panel px-2 py-1 text-xs text-fg-default outline-none focus-visible:ring-2 focus-visible:ring-ring';
/** The buttons on the page (Start browser, Reconnect, Close browser, a page
 *  dialog's answers): a neutral outline, a quiet button with a border, since
 *  colour marks a state or a role. */
const SECONDARY = { variant: 'quiet', className: 'border-border-default text-fg-default' } as const;
/** The address, a pill in the row of page controls. */
const ADDRESS = 'h-8.5 min-w-0 flex-1 rounded-pill border border-border-default bg-bg-elevated px-3.5 text-sm';

/** A page the owner's own browser can open: http(s) only. */
function webPage(url: string): boolean {
  if (!URL.canParse(url)) return false;
  const { protocol } = new URL(url);
  return protocol === 'http:' || protocol === 'https:';
}

/** The live browser, the same on Home and in the editor (chat/V2.md §4b):
 *  one row of page controls over the page (back, forward and reload, which
 *  work while the owner has control; the page's domain; Open in new tab;
 *  Take over). `ref`: the Workspace's Take over (SurfaceControl), for the
 *  browser shown. `dev` adds Dev mode's extras. */
export default function BrowserWorkspace({ workflowId, nodes, visible = true, agentName, action, dev = false, flushCanvas, ref }: BrowserWorkspaceProps & { ref?: Ref<SurfaceControl> }) {
  const [selected, setSelected] = useState('');
  const nodeId = nodes.some((n) => n.node_id === selected) ? selected : nodes[0]?.node_id;
  if (!workflowId) return <EmptyBrowser message="Save this workflow to open its browser." />;
  if (!nodeId) return <EmptyBrowser message="Add a Browser node to this workflow to watch it here." />;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      {nodes.length > 1 && (
        <select aria-label="Browser node" className={`${inputClass} mx-2 mt-2 shrink-0`} value={nodeId} onChange={(e) => setSelected(e.target.value)}>
          {nodes.map((node) => <option key={node.node_id} value={node.node_id}>{node.label || 'Browser'}</option>)}
        </select>
      )}
      <BrowserSessionView key={`${workflowId}:${nodeId}`} workflowId={workflowId} nodeId={nodeId} visible={visible} agentName={agentName} action={action} dev={dev} ref={ref} />
      {dev && <BrowserTasks key={`tasks:${workflowId}:${nodeId}`} workflowId={workflowId} browserNodeId={nodeId} visible={visible} onBrowserCreated={setSelected} flushCanvas={flushCanvas} />}
    </div>
  );
}

function EmptyBrowser({ message }: { message: string }) {
  return <div className="m-auto flex max-w-80 flex-col items-center gap-3 p-6 text-center text-sm text-fg-muted"><Globe aria-hidden className="size-6" />{message}</div>;
}

/** A round quiet button in the row of page controls. */
function PageButton({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: ReactNode }) {
  return <Button size="icon-sm" variant="quiet" aria-label={label} title={label} disabled={disabled} onClick={onClick} className="size-7.5 rounded-full">{children}</Button>;
}

function BrowserSessionView({ workflowId, nodeId, visible, agentName, action, dev, ref }: { workflowId: string; nodeId: string; visible: boolean; agentName?: string; action?: ReactNode; dev: boolean; ref?: Ref<SurfaceControl> }) {
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
  // The page the browser shows, as the server last said: the address box can
  // hold what the owner is typing instead.
  const [pageUrl, setPageUrl] = useState('');
  const [hasFrame, setHasFrame] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [dialog, setDialog] = useState<{ kind: string; message: string } | null>(null);
  const [promptText, setPromptText] = useState('');
  const [takingControl, setTakingControl] = useState(false);
  // Where the agent last acted (`agent_action`), on the picture it acted on;
  // cleared with the picture.
  const [agentAction, setAgentAction] = useState<{ action: BrowserAgentAction; picture: PictureShape } | null>(null);
  const actionIds = useRef(0);
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
    send({ type: 'control_release', outcome: 'control_lost' });
  }, [send]);

  // The Workspace's Take over asks for control and waits for the server's
  // answer (`control`, which it always sends); a closed socket answers no.
  const claimRef = useRef<((granted: boolean) => void) | null>(null);
  const answerClaim = useCallback((granted: boolean) => {
    claimRef.current?.(granted);
    claimRef.current = null;
  }, []);
  useImperativeHandle(ref, () => {
    // Hiding the view already gives control back, so after that there is nothing to do.
    const giveBack = () => {
      if (controlRef.current) send({ type: 'control_release' });
    };
    return {
      claim: () => new Promise<(() => void) | null>((resolve) => {
        if (controlRef.current) { resolve(giveBack); return; }
        const socket = socketRef.current;
        if (!socket || socket.readyState !== WebSocket.OPEN) { resolve(null); return; }
        answerClaim(false);
        claimRef.current = (granted) => resolve(granted ? giveBack : null);
        setError(''); setTakingControl(true);
        socket.send(JSON.stringify({ type: 'control_request', force: true }));
      }),
    };
  }, [answerClaim, send, setError]);

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
    setPhase('connecting'); setError(''); setHasFrame(false); frameRef.current = null; setAgentAction(null);
    setState({ state: 'idle' }); setTabs([]); setAddress(''); setPageUrl(''); setDialog(null); setTakingControl(false);
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
          // A stopped browser answers every request, a control request too, with `idle`.
          if (message.type === 'idle') { streamEpoch += 1; setPhase('idle'); setState({ state: 'idle' }); setHasFrame(false); frameRef.current = null; setAgentAction(null); setDialog(null); setTakingControl(false); answerClaim(false); }
          if (message.type === 'page') { setAddress(message.url || ''); setPageUrl(message.url || ''); }
          if (message.type === 'agent_action') {
            // Drawn on the picture shown: none before the first one, none for another tab.
            const picture = frameRef.current;
            const action = parseAgentAction(message, (actionIds.current += 1));
            if (picture && action && (!action.targetId || !picture.header.target_id || action.targetId === picture.header.target_id)) {
              setAgentAction({ action, picture });
            }
          }
          if (message.type === 'tabs') setTabs(message.tabs || []);
          if (message.type === 'dialog') { setDialog(message); setPromptText(''); }
          if (message.type === 'sensitive') {
            streamEpoch += 1; setHasFrame(false); frameRef.current = null; setAgentAction(null); setDialog(null); setTabs([]); setAddress(''); setPageUrl('');
            setState((previous) => ({ ...previous, sensitive_login: !!message.enabled }));
          }
          if (message.type === 'error') {
            setError(message.message || 'Browser request failed.', message.code === 'screencast');
            if (message.code === 'screencast' && message.retrying === false) {
              terminalStreamError = true; streamEpoch += 1;
              setPhase('error'); setHasFrame(false); frameRef.current = null; setAgentAction(null);
            }
          }
          if (message.type === 'control') {
            answerClaim(Boolean(message.granted));
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
      answerClaim(false);
      streamEpoch += 1;
      setPhase('error'); setHasFrame(false); frameRef.current = null; setAgentAction(null); setTakingControl(false);
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
      disposed = true; answerClaim(false); window.removeEventListener('blur', releaseControl); clearTimeout(retryTimer); observer.disconnect(); document.removeEventListener('visibilitychange', onVisibility);
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'control_release', outcome: 'control_lost' }));
      socket.close(); if (socketRef.current === socket) socketRef.current = null;
      if (pendingImage) { pendingImage.onerror?.(new Event('error')); pendingImage.src = ''; }
      for (const objectUrl of urls) URL.revokeObjectURL(objectUrl);
      urls.clear();
    };
  }, [workflowId, nodeId, isReady, attempt, setError, releaseControl, answerClaim]);

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
  const closeProtectedBrowser = async () => {
    setStopping(true); setError('');
    try {
      const result = await sendRequest<{ success: boolean; error?: string }>('browser_session_stop', { workflow_id: workflowId, node_id: nodeId }, 60_000);
      if (!result.success) throw new Error(result.error || 'Could not close the browser.');
      if (mountedRef.current) {
        setHasFrame(false); frameRef.current = null; setAgentAction(null); setTabs([]); setAddress(''); setPageUrl(''); setDialog(null);
        setState({ state: 'idle' }); setPhase('idle');
      }
    } catch (cause) {
      if (mountedRef.current) setError(cause instanceof Error ? cause.message : 'Could not close the browser.');
    } finally { if (mountedRef.current) setStopping(false); }
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
  // What the page says while there is no picture and no browser to start: connecting, or the connection lost.
  const connection = phase === 'connecting' ? 'Connecting…' : 'Disconnected';
  const tabPicker = tabs.length > 1 && <select aria-label="Browser tab" className={`${inputClass} w-full`} disabled={!control} value={tabs.find((tab) => tab.active)?.target_id || ''} onChange={(e) => send({ type: 'tab', action: 'activate', target_id: e.target.value })}>
    {tabs.map((tab) => <option key={tab.target_id} value={tab.target_id}>{tab.title || tab.url || 'New tab'}</option>)}
  </select>;
  // The employee's request, errors, the page and its dialogs.
  const body = <>
    {state.request && <div className="max-h-[25%] shrink-0 overflow-y-auto border-b border-border-default p-2 text-sm text-fg-default">{state.request.message || state.request.reason || 'The employee needs your help.'}</div>}
    {error && <div role="alert" className="flex max-h-[25%] shrink-0 items-start gap-2 overflow-y-auto p-2 text-xs text-destructive"><span className="min-w-0 flex-1 break-words">{error}</span><button aria-label="Dismiss browser error" onClick={() => setError('')}>×</button></div>}
    <div ref={surfaceRef} role="group" aria-label="Live browser page" tabIndex={control ? 0 : -1}
      className="relative flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-hidden bg-bg-app outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      style={{ touchAction: control ? 'none' : 'auto' }}
      onPointerDown={(e) => pointer(e, 'down')} onPointerMove={(e) => pointer(e, 'move')} onPointerUp={(e) => pointer(e, 'up')} onPointerCancel={releaseControl}
      onContextMenu={(e) => { if (control) e.preventDefault(); }} onKeyDown={(e) => key(e, 'down')} onKeyUp={(e) => key(e, 'up')}
      onPaste={(e) => { if (control) { e.preventDefault(); send({ type: 'insert_text', text: e.clipboardData.getData('text/plain') }); } }}
      onCompositionEnd={(e) => { if (control && e.data) send({ type: 'insert_text', text: e.data }); }}>
      <canvas ref={canvasRef} aria-label="Browser screenshot" className="absolute inset-0 h-full w-full object-contain" style={{ visibility: hasFrame ? 'visible' : 'hidden' }} />
      {hasFrame && agentAction && (
        <AgentCursor action={agentAction.action} picture={agentAction.picture} name={agentName} hidden={control || state.controller === 'other'} />
      )}
      {!hasFrame && <div className="relative flex max-w-80 flex-col items-center gap-3 p-6 text-center text-sm text-fg-muted"><Globe aria-hidden className="size-6" />
        <span>{state.sensitive_login ? 'Browser observations are paused during protected login.' : phase === 'live' ? 'Waiting for the browser picture…' : phase === 'idle' ? 'The browser will appear when the employee uses it. You can also open it now.' : connection}</span>
        {state.sensitive_login && <Button {...SECONDARY} disabled={stopping || !isReady} onClick={() => void closeProtectedBrowser()}>{stopping ? 'Closing…' : 'Close browser for manual login'}</Button>}
        {phase === 'idle' && <Button {...SECONDARY} disabled={starting || installing || !isReady} onClick={() => void start()}>{starting ? 'Starting…' : installing ? 'Installing…' : 'Start browser'}</Button>}
        {phase === 'error' && <Button {...SECONDARY} onClick={() => setAttempt((n) => n + 1)}>Reconnect</Button>}
      </div>}
    </div>
    {dialog && <div role="dialog" aria-label="Browser page dialog" className="flex max-h-[40%] shrink-0 flex-col gap-2 overflow-y-auto border-t border-border-default bg-bg-panel p-3 text-sm">
      <p className="break-words text-fg-default">{dialog.message}</p>
      {dialog.kind === 'prompt' && <input aria-label="Dialog response" className={inputClass} value={promptText} onChange={(e) => setPromptText(e.target.value)} disabled={!control} />}
      <div className="flex gap-2">{[true, false].map((accept) => <Button key={String(accept)} size="sm" {...SECONDARY} disabled={!control} onClick={() => { send({ type: 'dialog_reply', accept, prompt_text: promptText }); setDialog(null); }}>{accept ? 'OK' : 'Cancel'}</Button>)}</div>
      {!control && <span className="text-xs text-fg-muted">Take over to respond.</span>}
    </div>}
  </>;

  // A web page shows as its domain; any other address as it is.
  const page = webPage(pageUrl) ? new URL(pageUrl) : null;
  // The owner could take control: a live browser, not in a protected login.
  const canTake = phase === 'live' && !state.sensitive_login;
  // Without a host's Take over, the browser's own: it takes control here and
  // hands it back, and leaves whoever drives the browser running.
  const ownTakeOver = (
    <TakeOverButton
      disabled={takingControl}
      onClick={() => {
        setError('');
        if (control) send({ type: 'control_release' });
        else { setTakingControl(true); send({ type: 'control_request', ...(state.controller === 'other' ? { force: true } : {}) }); }
      }}
    >
      {control ? 'Hand back' : takingControl ? 'Taking over…' : 'Take over'}
    </TakeOverButton>
  );
  return (
    <FullView>
      <div className="flex h-13 shrink-0 items-center gap-2 border-b border-border-default px-2.5">
        <div className="flex h-8.5 shrink-0 items-center rounded-pill border border-border-default bg-bg-elevated px-0.5">
          <PageButton label="Back" disabled={!control} onClick={() => navigate('back')}><ArrowLeft aria-hidden className="size-3.5" strokeWidth={1.75} /></PageButton>
          <PageButton label="Forward" disabled={!control} onClick={() => navigate('forward')}><ArrowRight aria-hidden className="size-3.5" strokeWidth={1.75} /></PageButton>
          <span aria-hidden className="h-4 w-px bg-border-default" />
          <PageButton label="Reload page" disabled={!control} onClick={() => navigate('reload')}><RotateCw aria-hidden className="size-3.5" strokeWidth={1.75} /></PageButton>
        </div>
        {dev && control ? (
          // Dev mode: while the owner has control, the address is a box to go to a page from.
          <form className="flex min-w-0 flex-1" onSubmit={(e) => { e.preventDefault(); navigate('goto'); }}>
            <input
              aria-label="Browser address"
              className={`${ADDRESS} text-fg-default outline-none focus-visible:ring-2 focus-visible:ring-ring`}
              value={address}
              placeholder="Enter a website"
              onChange={(e) => setAddress(e.target.value)}
            />
          </form>
        ) : (
          <span title={pageUrl || undefined} className={`${ADDRESS} flex items-center justify-center gap-1.5 text-fg-muted`}>
            {page?.protocol === 'https:' && <Lock aria-hidden className="size-2.75 shrink-0 text-fg-faint" strokeWidth={2.2} />}
            <span className="truncate">{page ? page.hostname : pageUrl}</span>
          </span>
        )}
        {page && (
          <Button asChild size="icon-sm" variant="quiet" className="size-7.5 rounded-full">
            <a href={pageUrl} target="_blank" rel="noopener noreferrer" aria-label="Open in new tab" title="Open in new tab">
              <ExternalLink aria-hidden className="size-3.5" strokeWidth={1.75} />
            </a>
          </Button>
        )}
        {dev && <FullViewButton label="Browser" />}
        {canTake && (action === undefined ? ownTakeOver : !control && action)}
      </div>
      {tabPicker && <div className="shrink-0 border-b border-border-default px-2.5 py-2">{tabPicker}</div>}
      {body}
    </FullView>
  );
}
