import * as React from 'react';

/**
 * Badge — small count / label chip. Mono font for numeric counts.
 */
export function Badge({ variant = 'secondary', mono = false, color, children, style }) {
  const variants = {
    default: { background: 'var(--primary)', color: 'var(--primary-foreground)', border: '1px solid transparent' },
    secondary: { background: 'var(--bg-hover)', color: 'var(--fg-muted)', border: '1px solid transparent' },
    outline: { background: 'transparent', color: 'var(--fg-muted)', border: '1px solid var(--border-default)' },
    accent: {
      background: `color-mix(in srgb, ${color || 'var(--accent)'} 12%, transparent)`,
      color: color || 'var(--accent)',
      border: `1px solid color-mix(in srgb, ${color || 'var(--accent)'} 30%, transparent)`,
    },
  };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        height: 18,
        borderRadius: 'var(--radius-md, 6px)',
        padding: '0 6px',
        fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
        fontSize: 11,
        fontWeight: 500,
        lineHeight: 1,
        whiteSpace: 'nowrap',
        ...variants[variant],
        ...style,
      }}
    >
      {children}
    </span>
  );
}
