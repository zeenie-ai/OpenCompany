/**
 * StatusBar — fixed-bottom system console line.
 *
 * Lives below the canvas, shows workflow name, node count, WebSocket
 * connection state, active theme, and a live clock. Designed to read
 * as a "shell prompt" line under Cyber and a "manuscript footer" under
 * Renaissance via the new-contract typography tokens (font-mono,
 * tracking, uppercase).
 *
 * Per the handoff this is the `.statusbar` surface — it always exists,
 * height is 24px, sits above the ConsolePanel toggle bar.
 */

import * as React from 'react';
import { useEffect, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useTheme, type ThemeName } from '../../contexts/ThemeContext';
import { cn } from '@/lib/utils';

const THEME_LABEL: Record<ThemeName, string> = {
  light:        'LIGHT',
  dark:         'DARK',
  renaissance:  'RENAISSANCE',
  greek:        'GREEK',
  edo:          'EDO',
  steampunk:    'STEAMPUNK',
  atomic:       'ATOMIC',
  cyber:        'CYBER',
  wasteland:    'WASTELAND',
  rot:          'ROT',
  plague:       'PLAGUE',
  surveillance: 'SURVEILLANCE',
};

interface StatusBarProps {
  workflowName?: string;
  nodeCount?: number;
}

const useClock = (): string => {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return time.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
};

const Sep: React.FC = () => <span className="opacity-40">|</span>;

export const StatusBar: React.FC<StatusBarProps> = ({ workflowName, nodeCount }) => {
  const { isReady, isConnected } = useWebSocket();
  const { theme } = useTheme();
  const clock = useClock();

  const wsStatus = isReady ? 'ONLINE' : isConnected ? 'CONNECTING' : 'OFFLINE';
  const wsTone = isReady ? 'text-success' : isConnected ? 'text-warning' : 'text-destructive';

  return (
    // Fixed-bottom strip: bg-bg-panel + 1px top rule. font-mono carries
    // through to IM Fell English under Renaissance and JetBrains Mono
    // under Cyber, system mono under light/dark.
    <div
      className={cn(
        // `statusbar` is the handoff structural hook for per-theme
        // decorations (gauge readouts on Steampunk, REC blink on
        // Surveillance, illuminated footer on Renaissance).
        'statusbar flex h-6 items-center gap-3 border-t border-border-default bg-bg-panel px-3.5',
        'font-mono text-[11px] tracking-[0.04em] text-fg-muted',
        '[text-transform:var(--type-uppercase)]',
      )}
      role="contentinfo"
      aria-label="Status bar"
    >
      <span className={cn('flex items-center gap-1.5 font-medium', wsTone)}>
        {/* `pip` is the handoff hook for the per-theme blinking dot
            (Surveillance fires `surv-blink`, Cyber fires `cyber-blink`). */}
        <span
          className={cn(
            'pip inline-block h-1.5 w-1.5 rounded-full',
            isReady ? 'bg-success animate-pulse' : isConnected ? 'bg-warning' : 'bg-destructive',
          )}
        />
        {wsStatus}
      </span>

      <Sep />

      <span title={workflowName ?? 'No workflow'}>
        WF: <span className="text-fg-default">{workflowName ?? '—'}</span>
      </span>

      <Sep />

      <span>NODES: <span className="text-fg-default">{nodeCount ?? 0}</span></span>

      <span className="ml-auto flex items-center gap-3">
        <span>
          THEME: <span className="text-fg-default">{THEME_LABEL[theme]}</span>
        </span>
        <Sep />
        <span className="tabular-nums">{clock}</span>
      </span>
    </div>
  );
};

export default StatusBar;
