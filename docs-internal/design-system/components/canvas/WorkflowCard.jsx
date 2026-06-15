import * as React from 'react';

/**
 * WorkflowCard — sidebar saved-workflow card. Selected gets a 3px accent
 * left edge; metadata row is mono.
 */
export function WorkflowCard({ name, nodeCount = 0, modified, selected = false, onClick, style }) {
  const [hover, setHover] = React.useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        borderRadius: 'var(--radius-lg, 8px)',
        border: '1px solid ' + (selected ? 'var(--accent)' : 'var(--border-default)'),
        borderLeftWidth: selected ? 3 : 1,
        background: selected ? 'var(--bg-active)' : hover ? 'var(--bg-hover)' : 'var(--bg-app)',
        padding: 12,
        cursor: 'pointer',
        transition: 'background var(--dur-default, 180ms) var(--ease-default)',
        userSelect: 'none',
        ...style,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span
          style={{
            width: 24,
            height: 24,
            flexShrink: 0,
            borderRadius: 'var(--radius-sm, 4px)',
            border: '1px solid ' + (selected ? 'var(--accent)' : 'var(--border-default)'),
            background: selected ? 'color-mix(in srgb, var(--accent) 20%, transparent)' : 'var(--bg-elevated)',
            color: selected ? 'var(--accent)' : 'var(--fg-muted)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline>
          </svg>
        </span>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            fontFamily: 'var(--font-sans)',
            fontSize: 14,
            fontWeight: 500,
            color: selected ? 'var(--accent)' : 'var(--fg-default)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {name}
        </span>
      </div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          color: selected ? 'var(--fg-default)' : 'var(--fg-muted)',
        }}
      >
        <span>{nodeCount} nodes</span>
        <span>{modified}</span>
      </div>
    </div>
  );
}
