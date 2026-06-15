import * as React from 'react';

/**
 * ComponentItem — draggable palette card: icon tile + name + description
 * + grip. Hover lifts -2px with a foreground ring.
 */
export function ComponentItem({ icon, name, description, onClick, style }) {
  const [hover, setHover] = React.useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 12px',
        borderRadius: 'var(--radius-lg, 8px)',
        border: '1px solid var(--border-default)',
        background: hover ? 'var(--bg-hover)' : 'var(--bg-app)',
        boxShadow: hover
          ? '0 0 0 2px color-mix(in srgb, var(--fg-default) 15%, transparent), var(--shadow-card-hover)'
          : 'none',
        transform: hover ? 'translateY(-2px)' : 'none',
        transition: 'all 150ms var(--ease-default)',
        cursor: 'grab',
        userSelect: 'none',
        ...style,
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          flexShrink: 0,
          borderRadius: 'var(--radius-md, 6px)',
          background: 'var(--bg-elevated)',
          boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--border-default) 40%, transparent)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 18,
        }}
      >
        {icon || '📦'}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--fg-default)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {name}
        </div>
        <div
          style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 11,
            lineHeight: 1.3,
            color: 'var(--fg-muted)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {description}
        </div>
      </div>
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="var(--fg-faint)"
        strokeWidth="2"
        style={{ flexShrink: 0, opacity: hover ? 0.8 : 0.5, transition: 'opacity 150ms' }}
      >
        <circle cx="9" cy="5" r="1"></circle><circle cx="9" cy="12" r="1"></circle><circle cx="9" cy="19" r="1"></circle>
        <circle cx="15" cy="5" r="1"></circle><circle cx="15" cy="12" r="1"></circle><circle cx="15" cy="19" r="1"></circle>
      </svg>
    </div>
  );
}
