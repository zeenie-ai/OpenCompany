import * as React from 'react';

/**
 * CollapsibleSection — accordion card: elevated trigger head with
 * display-font title + rotating chevron over an app-surface body.
 * Source: client/src/components/ui/CollapsibleSection.tsx.
 */
export function CollapsibleSection({ title, defaultCollapsed = false, collapsed, onToggle, children, style }) {
  const [internal, setInternal] = React.useState(defaultCollapsed);
  const isCollapsed = collapsed !== undefined ? collapsed : internal;
  const [hover, setHover] = React.useState(false);
  const toggle = () => {
    if (collapsed === undefined) setInternal((c) => !c);
    onToggle && onToggle(!isCollapsed);
  };
  return (
    <div style={{
      overflow: 'hidden',
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-app)',
      ...style,
    }}>
      <button
        type="button"
        onClick={toggle}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          display: 'flex', width: '100%', alignItems: 'center', justifyContent: 'space-between', gap: 8,
          border: 'none', cursor: 'pointer',
          background: hover ? 'var(--bg-hover)' : 'var(--bg-elevated)',
          padding: '12px 16px',
          color: 'var(--fg-default)',
          transition: 'background var(--dur-fast, 90ms)',
          textAlign: 'left',
        }}
      >
        <span style={{
          fontFamily: 'var(--font-display, var(--font-sans))',
          fontSize: 14.5, fontWeight: 500, flex: 1, minWidth: 0,
          letterSpacing: 'var(--type-tracking-display, 0)',
          textTransform: 'var(--type-uppercase, none)',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          {title}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--fg-muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, transform: isCollapsed ? 'rotate(-90deg)' : 'none', transition: 'transform var(--dur-default, 180ms) var(--ease-default)' }}>
          <path d="m6 9 6 6 6-6"></path>
        </svg>
      </button>
      {!isCollapsed ? (
        <div style={{ padding: 12 }}>
          {children}
        </div>
      ) : null}
    </div>
  );
}
