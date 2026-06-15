import * as React from 'react';

/**
 * Kbd — keyboard shortcut chip. Mono, bordered, bottom-weighted edge.
 */
export function Kbd({ children, style }) {
  return (
    <kbd
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        minWidth: 20, height: 20, padding: '0 5px',
        borderRadius: 'var(--radius-sm, 4px)',
        border: '1px solid var(--border-default)',
        borderBottomWidth: 2,
        background: 'var(--bg-panel)',
        color: 'var(--fg-muted)',
        fontFamily: 'var(--font-mono)',
        fontSize: 10.5,
        lineHeight: 1,
        ...style,
      }}
    >
      {children}
    </kbd>
  );
}
