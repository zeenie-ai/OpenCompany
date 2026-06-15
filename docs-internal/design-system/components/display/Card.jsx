import * as React from 'react';

/**
 * Card — base surface. Optional hover lift (for clickable cards) and
 * selected state (3px accent left edge, like WorkflowSidebar cards).
 */
export function Card({ interactive = false, selected = false, children, style, ...rest }) {
  const [hover, setHover] = React.useState(false);
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        borderRadius: 'var(--radius-lg, 8px)',
        border: '1px solid var(--border-default)',
        borderLeft: selected ? '3px solid var(--accent)' : '1px solid var(--border-default)',
        background: selected
          ? 'var(--bg-active)'
          : interactive && hover
            ? 'var(--bg-hover)'
            : 'var(--surface-card)',
        boxShadow: interactive && hover ? 'var(--shadow-card-hover)' : 'var(--shadow-card)',
        transform: interactive && hover ? 'translateY(-1px)' : 'none',
        transition: 'all var(--dur-default, 180ms) var(--ease-default)',
        cursor: interactive ? 'pointer' : 'default',
        padding: 12,
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
}
