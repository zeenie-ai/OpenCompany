/**
 * Console Panel - n8n-style debug output panel with chat input
 *
 * Displays console log entries from Console nodes during workflow execution,
 * beside the Chat pane (ConsoleChat: the chat shared with Home, talking to
 * the open workflow's chat triggers). Shows in a collapsible bottom bar
 * section with clear and filter options.
 *
 * Design-handoff hybrid layout: split view (default) docks Chat as a
 * resizable pane beside the Console/Terminal tabs; tab mode makes Chat
 * the first of three tabs (Chat / Console / Terminal). Toggled via the
 * Columns2 button in the tab row, persisted in consolePrefs.splitView.
 * Supports resizing by dragging the top edge.
 */

import React, { useState, useCallback, useMemo, useRef, useEffect } from 'react';
import { Node } from 'reactflow';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { z } from 'zod';
import Prism from 'prismjs';
import 'prismjs/components/prism-json';
import { ChevronDown, Columns2 } from 'lucide-react';
import { useWebSocket, ConsoleLogEntry } from '../../contexts/WebSocketContext';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { useChatThread, type ChatPaneHandle } from '@/features/chat';
import { resolveNodeDescription } from '../../lib/nodeSpec';
import { useAppStore } from '../../store/useAppStore';
import { usePanelResize } from '../../hooks/usePanelResize';
import { ConsoleChat } from './ConsoleChat';

// ---------------------------------------------------------------------------
// Persisted prefs (localStorage)
// ---------------------------------------------------------------------------

const consolePrefsSchema = z.object({
  panelHeight: z.number().min(80).max(2000).default(250),
  chatWidthPercent: z.number().min(20).max(80).default(50),
  fontSize: z.number().min(8).max(40).default(12),
  autoScroll: z.boolean().default(true),
  prettyPrint: z.boolean().default(true),
  // Design-handoff hybrid: Chat is a first-class tab alongside Console /
  // Terminal. Persisted pre-hybrid values ('console' | 'terminal') stay
  // valid under the widened enum.
  consoleTab: z.enum(['chat', 'console', 'terminal']).default('console'),
  // true (default) = Chat docked as a resizable pane beside the
  // Console/Terminal tabs (simultaneous chat + logs — the pre-hybrid
  // layout, preserved for existing users); false = Chat is the first tab.
  splitView: z.boolean().default(true),
});
type ConsolePrefs = z.infer<typeof consolePrefsSchema>;
const CONSOLE_PREFS_KEY = 'console_panel_prefs_v1';

function loadConsolePrefs(): ConsolePrefs {
  try {
    const raw = localStorage.getItem(CONSOLE_PREFS_KEY);
    if (raw) {
      const parsed = consolePrefsSchema.safeParse(JSON.parse(raw));
      if (parsed.success) return parsed.data;
    }
  } catch { /* fall through to defaults */ }
  return consolePrefsSchema.parse({});
}

function saveConsolePrefs(prefs: ConsolePrefs): void {
  try {
    localStorage.setItem(CONSOLE_PREFS_KEY, JSON.stringify(prefs));
  } catch { /* ignore */ }
}

// Font size band — matches the previous theme.fontSize.xs..xl*2 derivation.
const MIN_FONT_SIZE = 10;
const MAX_FONT_SIZE = 32;
const DEFAULT_FONT_SIZE = 12;

// Drag-resize hook shared with the Canvas dock — extracted verbatim to
// hooks/usePanelResize.ts; behavior unchanged.

// ---------------------------------------------------------------------------

interface ConsolePanelProps {
  isOpen: boolean;
  onToggle: () => void;
  defaultHeight?: number;
  minHeight?: number;
  maxHeight?: number;
  nodes?: Node[];
}

const ConsolePanel: React.FC<ConsolePanelProps> = ({
  isOpen,
  onToggle,
  // defaultHeight + maxHeight props are no longer read — defaults live in
  // consolePrefsSchema; max is computed from window.innerHeight at resize.
  // Kept on the prop type for backwards compatibility with existing callers.
  minHeight = 80,
  nodes = [],
}) => {
  const { consoleLogs, clearConsoleLogs, terminalLogs, clearTerminalLogs } = useWebSocket();
  // The open workflow's chat (ConsoleChat reads the same thread).
  const chatSessionId = useAppStore((s) => s.currentWorkflow?.id || 'default');
  const chatCount = useChatThread(chatSessionId, 'live').data?.messages.length ?? 0;

  // Console nodes in the workflow, for the console filter.
  const consoleNodes = useMemo(() =>
    nodes.filter(n => {
      const def = n.type ? resolveNodeDescription(n.type) : undefined;
      return def?.uiHints?.isConsoleSink
        ?? (n.type ? ['console'].includes(n.type) : false);
    }),
    [nodes]
  );

  const [selectedConsoleId, setSelectedConsoleId] = useState<string>('');
  const [filter, setFilter] = useState('');
  const [terminalFilter, setTerminalFilter] = useState('');
  const [terminalLogLevel, setTerminalLogLevel] = useState<'all' | 'error' | 'warning' | 'info' | 'debug'>('all');

  const [prefs, setPrefs] = useState<ConsolePrefs>(loadConsolePrefs);
  const setPref = useCallback(<K extends keyof ConsolePrefs>(key: K, value: ConsolePrefs[K]) => {
    setPrefs(prev => {
      const next = { ...prev, [key]: value };
      saveConsolePrefs(next);
      return next;
    });
  }, []);
  const { autoScroll, prettyPrint, consoleTab, splitView, panelHeight, chatWidthPercent, fontSize: consoleFontSize } = prefs;
  // In split view Chat is always visible as the docked pane, so a
  // persisted 'chat' tab selection falls back to Console for the tabbed
  // side; in tab mode all three tabs are selectable.
  const activeTab = splitView && consoleTab === 'chat' ? 'console' : consoleTab;

  const logsEndRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<ChatPaneHandle>(null);

  // Focus the chat input when requested (the toolbar's `focusChat`).
  // Each request is consumed once so later manual panel toggles don't
  // re-steal focus.
  const chatFocusRequest = useAppStore((s) => s.chatFocusRequest);
  const handledFocusRequestRef = useRef(0);
  useEffect(() => {
    if (chatFocusRequest === handledFocusRequestRef.current || !isOpen) return;
    handledFocusRequestRef.current = chatFocusRequest;
    // In tab mode the chat's message box only exists while the Chat tab is
    // active — switch to it before focusing.
    if (!splitView) setPref('consoleTab', 'chat');
    const frame = requestAnimationFrame(() => chatRef.current?.focusComposer());
    return () => cancelAnimationFrame(frame);
  }, [chatFocusRequest, isOpen, splitView, setPref]);

  const containerRef = useRef<HTMLDivElement>(null);

  const verticalResize = usePanelResize({
    axis: 'y',
    cursor: 'ns-resize',
    getStartValue: () => panelHeight,
    onMove: (delta, start) => {
      const dynamicMax = window.innerHeight - 90;
      const next = Math.min(dynamicMax, Math.max(minHeight, start - delta));
      setPref('panelHeight', next);
    },
  });
  const isResizing = verticalResize.isResizing;
  const handleResizeStart = verticalResize.start;

  // Horizontal split (chat sidebar / main pane) -- matches the original
  // absolute-position approach from main: chat-width % is derived from
  // the mouse's offset within the container on every move, not from a
  // delta against a captured start value. The delta-based variant
  // closed over the live `chatWidthPercent` and compounded each move,
  // making the slider race and feel non-linear.
  const [isHorizontalResizing, setIsHorizontalResizing] = useState(false);
  const handleHorizontalResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsHorizontalResizing(true);
  }, []);
  useEffect(() => {
    if (!isHorizontalResizing) return;
    const handleMove = (e: MouseEvent) => {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      setPref('chatWidthPercent', Math.min(80, Math.max(20, pct)));
    };
    const handleUp = () => setIsHorizontalResizing(false);
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    return () => {
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isHorizontalResizing, setPref]);

  // Clamp font size on first mount so out-of-band saved values can't escape
  // the [min, max] band.
  useEffect(() => {
    if (consoleFontSize < MIN_FONT_SIZE || consoleFontSize > MAX_FONT_SIZE) {
      setPref('fontSize', DEFAULT_FONT_SIZE);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------ Filtering ------------------------------

  const filteredLogs = useMemo(() => {
    let logs = consoleLogs;
    if (selectedConsoleId) {
      logs = logs.filter(log => log.node_id === selectedConsoleId);
    }
    if (filter) {
      const lowerFilter = filter.toLowerCase();
      logs = logs.filter(log =>
        log.label.toLowerCase().includes(lowerFilter) ||
        log.formatted.toLowerCase().includes(lowerFilter) ||
        log.node_id.toLowerCase().includes(lowerFilter)
      );
    }
    return logs;
  }, [consoleLogs, filter, selectedConsoleId]);

  const filteredTerminalLogs = useMemo(() => {
    let filtered = terminalLogs;
    if (terminalLogLevel !== 'all') {
      const levelPriority: Record<string, number> = { error: 0, warning: 1, info: 2, debug: 3 };
      const selectedPriority = levelPriority[terminalLogLevel] ?? 2;
      filtered = filtered.filter(log => (levelPriority[log.level] ?? 2) <= selectedPriority);
    }
    if (terminalFilter) {
      const lowerFilter = terminalFilter.toLowerCase();
      filtered = filtered.filter(log =>
        log.message.toLowerCase().includes(lowerFilter) ||
        (log.source?.toLowerCase().includes(lowerFilter))
      );
    }
    return filtered;
  }, [terminalLogs, terminalFilter, terminalLogLevel]);

  // ------------------------------ Auto-scroll ------------------------------

  useEffect(() => {
    if (autoScroll && isOpen && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [filteredLogs.length, autoScroll, isOpen]);

  // ------------------------------ Actions ------------------------------

  const handleClearConsole = useCallback(() => clearConsoleLogs(), [clearConsoleLogs]);
  // ------------------------------ Formatting ------------------------------

  const formatTimestamp = useCallback((timestamp: string) => {
    try {
      const date = new Date(timestamp);
      const timeStr = date.toLocaleTimeString('en-US', {
        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
      const ms = date.getMilliseconds().toString().padStart(3, '0');
      return `${timeStr}.${ms}`;
    } catch {
      return timestamp;
    }
  }, []);

  // Returns a Tailwind text class for the log entry body. Themes that change
  // semantic tokens automatically rotate these.
  const getFormatTextClass = useCallback((format: ConsoleLogEntry['format']): string => {
    switch (format) {
      case 'json':
      case 'json_compact':
        return 'text-info';
      case 'table':
        return 'text-success';
      case 'text':
      default:
        return 'text-foreground';
    }
  }, []);

  const formatForDisplay = useCallback((text: string): { formatted: string; isJson: boolean } => {
    if (!prettyPrint) return { formatted: text, isJson: false };
    let formatted = text.replace(/\\n/g, '\n').replace(/\\t/g, '\t');
    const trimmed = formatted.trim();
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        const parsed = JSON.parse(trimmed);
        formatted = JSON.stringify(parsed, null, 2);
        return { formatted, isJson: true };
      } catch { /* not valid JSON */ }
    }
    return { formatted, isJson: false };
  }, [prettyPrint]);

  const highlightJson = useCallback(
    (code: string): string => Prism.highlight(code, Prism.languages.json, 'json'),
    []
  );

  // ------------------------------ Render ------------------------------

  // One chat for both layouts: the split-view docked pane and the Chat tab.
  const chatSection = <ConsoleChat ref={chatRef} fontSize={consoleFontSize} />;

  return (
    // `chat` co-class is the design-handoff structural hook for per-theme
    // panel decorations (parchment vellum on Renaissance, neon cyan
    // top-glow on Cyber, washi noise on Edo, etc.).
    <div
      className="chat relative"
      onWheel={e => {
        // Prevent scroll from propagating to the canvas/page when the cursor
        // is over the panel header, resize handle, or non-scrollable areas.
        const target = e.target as HTMLElement;
        const scrollable = target.closest('[data-scrollable]');
        if (!scrollable) e.stopPropagation();
      }}
    >
      {/* Top resize handle - only visible when open */}
      {isOpen && (
        <div
          onMouseDown={handleResizeStart}
          className={cn(
            'absolute top-0 right-0 left-0 z-10 h-1.5 cursor-ns-resize transition-colors',
            isResizing ? 'bg-node-agent transition-none' : 'hover:bg-node-agent-soft'
          )}
        />
      )}

      {/* Panel Header */}
      <div
        onClick={onToggle}
        className="flex cursor-pointer items-center justify-between border-t border-border-default bg-bg-elevated px-3 py-1.5 select-none"
      >
        <div className="flex items-center gap-2">
          <ChevronDown
            className={cn(
              'h-3 w-3 text-muted-foreground transition-transform',
              !isOpen && 'rotate-180'
            )}
          />
          <span className="flex items-center gap-1.5 font-display text-sm font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
            Chat / Console
            {(consoleLogs.length > 0 || chatCount > 0) && (
              <Badge variant="secondary" className="text-xs">
                {consoleLogs.length + chatCount}
              </Badge>
            )}
          </span>
        </div>
      </div>

      {/* Panel Content - resizable horizontal split */}
      <div
        ref={containerRef}
        style={{ height: isOpen ? `${panelHeight}px` : '0px' }}
        className={cn(
          'flex flex-row overflow-hidden bg-bg-app',
          'max-h-[calc(100vh-90px)]',
          (isResizing || isHorizontalResizing) ? '[transition:none]' : 'transition-[height] duration-200 ease-in-out'
        )}
      >
        {/* ===================== Chat (split view: docked left pane) ===================== */}
        {splitView && (
          <>
            <div
              style={{ width: `${chatWidthPercent}%` }}
              className="relative flex flex-col overflow-hidden"
            >
              {chatSection}
            </div>

            {/* Horizontal resize handle */}
            <div
              onMouseDown={handleHorizontalResizeStart}
              className={cn(
                'w-1.5 shrink-0 cursor-ew-resize transition-colors',
                isHorizontalResizing
                  ? 'bg-node-agent transition-none'
                  : 'bg-border hover:bg-node-agent-soft'
              )}
            />
          </>
        )}

        {/* ===================== Console / Terminal Section (right) ===================== */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Header with tabs + filters */}
          <div className="flex min-h-[32px] items-center justify-between border-b border-border-default bg-bg-elevated px-3 py-1.5">
            {/* Tab buttons — design-handoff hybrid: Chat is the first tab
                in tab mode; in split view it lives in the docked pane. */}
            <div className="flex items-center gap-1">
              {!splitView && (
                <button
                  onClick={() => setPref('consoleTab', 'chat')}
                  className={cn(
                    'flex items-center gap-1 rounded-sm px-2 py-0.5 text-xs transition-colors',
                    activeTab === 'chat'
                      ? 'bg-node-trigger-soft font-semibold text-node-trigger'
                      : 'text-muted-foreground hover:bg-muted'
                  )}
                >
                  Chat
                  {chatCount > 0 && (
                    <Badge variant="success" className="ml-1 px-1 text-xs">{chatCount}</Badge>
                  )}
                </button>
              )}
              <button
                onClick={() => setPref('consoleTab', 'console')}
                className={cn(
                  'flex items-center gap-1 rounded-sm px-2 py-0.5 text-xs transition-colors',
                  activeTab === 'console'
                    ? 'bg-node-agent-soft font-semibold text-node-agent'
                    : 'text-muted-foreground hover:bg-muted'
                )}
              >
                Console
                {consoleLogs.length > 0 && (
                  <Badge variant="secondary" className="ml-1 px-1 text-xs">{consoleLogs.length}</Badge>
                )}
              </button>
              <button
                onClick={() => setPref('consoleTab', 'terminal')}
                className={cn(
                  'flex items-center gap-1 rounded-sm px-2 py-0.5 text-xs transition-colors',
                  activeTab === 'terminal'
                    ? 'bg-node-model-soft font-semibold text-node-model'
                    : 'text-muted-foreground hover:bg-muted'
                )}
              >
                Terminal
                {terminalLogs.length > 0 && (
                  <Badge variant="info" className="ml-1 px-1 text-xs">{terminalLogs.length}</Badge>
                )}
              </button>
              <button
                onClick={() => setPref('splitView', !splitView)}
                aria-pressed={splitView}
                title={splitView ? 'Switch to tabs (Chat becomes a tab)' : 'Dock Chat as a split pane'}
                className={cn(
                  'ml-1 flex items-center rounded-sm px-1.5 py-0.5 text-xs transition-colors',
                  splitView
                    ? 'bg-node-tool-soft text-node-tool'
                    : 'text-muted-foreground hover:bg-muted'
                )}
              >
                <Columns2 className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="flex items-center gap-1.5">
              {activeTab === 'terminal' && (
                <Select
                  value={terminalLogLevel}
                  onValueChange={(v) => setTerminalLogLevel(v as 'all' | 'error' | 'warning' | 'info' | 'debug')}
                >
                  <SelectTrigger
                    className="h-6 max-w-[120px] text-xs"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Levels</SelectItem>
                    <SelectItem value="error">Error</SelectItem>
                    <SelectItem value="warning">Warning+</SelectItem>
                    <SelectItem value="info">Info+</SelectItem>
                    <SelectItem value="debug">Debug+</SelectItem>
                  </SelectContent>
                </Select>
              )}
              {activeTab === 'console' && consoleNodes.length > 0 && (
                <Select
                  value={selectedConsoleId || '__all__'}
                  onValueChange={(v) => setSelectedConsoleId(v === '__all__' ? '' : v)}
                >
                  <SelectTrigger
                    className="h-6 max-w-[120px] text-xs"
                    onClick={(e) => e.stopPropagation()}
                    title="Filter logs by Console node"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">All Consoles</SelectItem>
                    {consoleNodes.map(node => (
                      <SelectItem key={node.id} value={node.id}>
                        {node.data?.label || node.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <Input
                type="text"
                placeholder="Filter..."
                value={activeTab === 'console' ? filter : terminalFilter}
                onChange={(e) => activeTab === 'console' ? setFilter(e.target.value) : setTerminalFilter(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="h-6 w-[100px] text-xs"
              />
              <label className="flex cursor-pointer items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs text-muted-foreground">
                <Checkbox
                  checked={autoScroll}
                  onCheckedChange={(checked) => setPref('autoScroll', checked === true)}
                  className="h-3 w-3"
                />
                Auto
              </label>
              {activeTab === 'console' && (
                <label
                  className={cn(
                    'flex cursor-pointer items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs text-muted-foreground transition-colors',
                    prettyPrint && 'bg-node-model-soft text-node-model'
                  )}
                  title="Format JSON and convert escaped newlines"
                >
                  <Checkbox
                    checked={prettyPrint}
                    onCheckedChange={(checked) => setPref('prettyPrint', checked === true)}
                    className="h-3 w-3"
                  />
                  Pretty
                </label>
              )}
              <Input
                type="number"
                value={consoleFontSize}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (!Number.isNaN(v)) setPref('fontSize', v);
                }}
                min={MIN_FONT_SIZE}
                max={MAX_FONT_SIZE}
                className="h-7 w-14 text-xs"
              />
              {((activeTab === 'console' && consoleLogs.length > 0) || (activeTab === 'terminal' && terminalLogs.length > 0)) && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={activeTab === 'console' ? handleClearConsole : clearTerminalLogs}
                  className="border-destructive/40 text-destructive hover:bg-destructive/10"
                >
                  Clear
                </Button>
              )}
            </div>
          </div>

          {/* Body: Chat tab (tab mode only) reuses the shared chat
              section; Console / Terminal render the logs body. */}
          {activeTab === 'chat' ? (
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              {chatSection}
            </div>
          ) : (
          <div
            data-scrollable
            style={{ fontSize: consoleFontSize }}
            className="flex-1 overflow-auto font-mono"
          >
            {activeTab === 'console' ? (
              filteredLogs.length === 0 ? (
                <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
                  {consoleLogs.length === 0
                    ? 'Add a Console node to see debug output'
                    : 'No logs match the filter'}
                </div>
              ) : (
                filteredLogs.slice().reverse().map((log, index) => (
                  <div
                    key={`${log.node_id}-${log.timestamp}-${index}`}
                    className="flex items-start gap-3 border-b border-border px-3 py-1"
                  >
                    <span className="min-w-[90px] text-xs whitespace-nowrap text-muted-foreground">
                      {formatTimestamp(log.timestamp)}
                    </span>
                    <span
                      className="min-w-[80px] max-w-[120px] truncate text-sm font-medium text-warning"
                      title={log.label || log.node_id}
                    >
                      {log.label || log.node_id}
                    </span>
                    {log.source_node_label && (
                      <span
                        className="min-w-[80px] max-w-[120px] truncate font-mono text-xs text-node-trigger opacity-85"
                        title={`Source: ${log.source_node_type} (${log.source_node_id})`}
                      >
                        {log.source_node_label}
                      </span>
                    )}
                    {(() => {
                      const { formatted, isJson } = formatForDisplay(log.formatted);
                      if (isJson && prettyPrint) {
                        return (
                          <pre
                            className="code-editor-container m-0 flex-1 overflow-auto whitespace-pre-wrap break-words"
                            dangerouslySetInnerHTML={{ __html: highlightJson(formatted) }}
                          />
                        );
                      } else if (!isJson && prettyPrint) {
                        return (
                          <div className={cn('chat-markdown flex-1 overflow-auto leading-snug', getFormatTextClass(log.format))}>
                            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                              {formatted}
                            </ReactMarkdown>
                          </div>
                        );
                      }
                      return (
                        <pre
                          className={cn(
                            'm-0 flex-1 overflow-auto leading-tight font-[inherit] text-[length:inherit] whitespace-pre-wrap break-words',
                            getFormatTextClass(log.format)
                          )}
                        >
                          {formatted}
                        </pre>
                      );
                    })()}
                  </div>
                ))
              )
            ) : (
              filteredTerminalLogs.length === 0 ? (
                <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
                  {terminalLogs.length === 0
                    ? 'Server logs will appear here'
                    : 'No logs match the filter'}
                </div>
              ) : (
                <div className="min-w-max">
                  {filteredTerminalLogs.slice().reverse().map((log, index) => (
                    <div
                      key={`${log.timestamp}-${index}`}
                      className={cn(
                        'border-b border-border px-3 py-0.5 whitespace-nowrap',
                        log.level === 'error' && 'bg-destructive/10',
                        log.level === 'warning' && 'bg-warning/10'
                      )}
                    >
                      <span className="text-xs text-muted-foreground">{formatTimestamp(log.timestamp)}</span>
                      {log.source && (
                        <span className="ml-2 text-xs text-info">[{log.source}]</span>
                      )}
                      <span className="ml-2 text-sm text-foreground">
                        {log.message}
                        {log.details && (
                          <span className="ml-2 text-muted-foreground">
                            {typeof log.details === 'string' ? log.details : JSON.stringify(log.details)}
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )
            )}
            <div ref={logsEndRef} />
          </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ConsolePanel;
