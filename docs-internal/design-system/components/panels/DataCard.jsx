import * as React from 'react';

/**
 * DataCard — execution-data card from the parameter panel's Input/Output
 * columns: status left-edge, icon + title + source badge header, Copy
 * action, labeled mono JSON block.
 * Source: client/src/components/ui/InputNodesPanel.tsx / OutputDisplayPanel.tsx.
 */
export function DataCard({
  title,
  badge,
  tone = 'success',
  blockLabel = 'Received Data',
  data,
  onCopy,
  style,
}) {
  const c = tone === 'error' ? 'var(--destructive)' : tone === 'warning' ? 'var(--warning)' : 'var(--success)';
  const json = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  return (
    <div style={{
      borderRadius: 'var(--radius-md, 6px)',
      border: '1px solid var(--border-default)',
      borderLeft: `4px solid ${c}`,
      background: 'var(--surface-card)',
      padding: 12,
      ...style,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M3 5V19A9 3 0 0 0 21 19V5"></path><path d="M3 12A9 3 0 0 0 21 12"></path>
          </svg>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--fg-default)', whiteSpace: 'nowrap' }}>{title}</span>
          {badge ? (
            <span style={{
              display: 'inline-flex', alignItems: 'center', height: 18, padding: '0 6px',
              borderRadius: 'var(--radius-md, 6px)', fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap',
              background: `color-mix(in srgb, ${c} 12%, transparent)`,
              border: `1px solid color-mix(in srgb, ${c} 30%, transparent)`,
              color: c,
              overflow: 'hidden', textOverflow: 'ellipsis',
            }}>{badge}</span>
          ) : null}
        </span>
        <CopyButton onCopy={onCopy} json={json} />
      </div>
      <div style={{ overflow: 'hidden', borderRadius: 'var(--radius-md, 6px)', border: '1px solid var(--border-default)' }}>
        <div style={{
          borderBottom: '1px solid var(--border-default)', background: 'var(--bg-hover)',
          padding: '6px 12px', fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: 600, color: 'var(--fg-muted)',
        }}>{blockLabel}</div>
        <pre style={{
          margin: 0, maxHeight: 300, overflow: 'auto',
          whiteSpace: 'pre-wrap', overflowWrap: 'break-word',
          background: 'color-mix(in srgb, var(--bg-hover) 40%, transparent)',
          padding: 12, fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.4,
          color: 'var(--fg-default)',
        }}>{json}</pre>
      </div>
    </div>
  );
}

function CopyButton({ onCopy, json }) {
  const [hover, setHover] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const handle = () => {
    if (onCopy) onCopy(json);
    else if (navigator.clipboard) navigator.clipboard.writeText(json).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <button
      type="button"
      onClick={handle}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        border: 'none', borderRadius: 'var(--radius-md, 6px)',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: 'var(--fg-muted)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
        padding: '5px 8px', cursor: 'pointer', flexShrink: 0,
        transition: 'background var(--dur-fast, 90ms)',
      }}
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>
      </svg>
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}
