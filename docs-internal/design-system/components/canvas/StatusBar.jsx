import * as React from 'react';

/**
 * StatusBar — 24px shell-prompt footer: connection pip, workflow name,
 * node count, theme, live clock. All mono, uppercase, 0.04em tracking.
 */
export function StatusBar({
  connection = 'online',
  workflowName = '—',
  nodeCount = 0,
  themeName = 'DARK',
  clock,
  style,
}) {
  const [time, setTime] = React.useState(() => new Date());
  React.useEffect(() => {
    if (clock !== undefined) return;
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, [clock]);
  const timeText =
    clock !== undefined
      ? clock
      : time.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  const tones = {
    online: { color: 'var(--success)', label: 'ONLINE' },
    connecting: { color: 'var(--warning)', label: 'CONNECTING' },
    offline: { color: 'var(--destructive)', label: 'OFFLINE' },
  };
  const tone = tones[connection] || tones.online;
  const sep = <span style={{ opacity: 0.4 }}>|</span>;

  return (
    <div
      role="contentinfo"
      style={{
        display: 'flex',
        height: 'var(--h-statusbar, 24px)',
        alignItems: 'center',
        gap: 12,
        borderTop: '1px solid var(--border-default)',
        background: 'var(--bg-panel)',
        padding: '0 14px',
        fontFamily: 'var(--font-mono)',
        fontSize: 11,
        letterSpacing: '0.04em',
        color: 'var(--fg-muted)',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, color: tone.color }}>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: tone.color,
            display: 'inline-block',
            animation: connection === 'online' ? 'machina-pip-blink 2s ease-in-out infinite' : 'none',
          }}
        />
        {tone.label}
      </span>
      {sep}
      <span>
        WF: <span style={{ color: 'var(--fg-default)' }}>{workflowName}</span>
      </span>
      {sep}
      <span>
        NODES: <span style={{ color: 'var(--fg-default)' }}>{nodeCount}</span>
      </span>
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span>
          THEME: <span style={{ color: 'var(--fg-default)' }}>{themeName}</span>
        </span>
        {sep}
        <span style={{ fontVariantNumeric: 'tabular-nums' }}>{timeText}</span>
      </span>
      <style>{'@keyframes machina-pip-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }'}</style>
    </div>
  );
}
