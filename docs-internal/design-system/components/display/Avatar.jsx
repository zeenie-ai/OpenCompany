import * as React from 'react';

/**
 * Avatar — initials in a soft accent tile. Used for providers/agents
 * in credential rows and chat.
 */
export function Avatar({ name = '', color = 'var(--accent)', size = 28, square = false, style }) {
  const initials = String(name)
    .split(/\s+/)
    .map((w) => w.charAt(0))
    .slice(0, 2)
    .join('')
    .toUpperCase();
  return (
    <span
      title={name}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: size, height: size, flexShrink: 0,
        borderRadius: square ? 'var(--radius-md, 6px)' : '50%',
        border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
        background: `color-mix(in srgb, ${color} 14%, transparent)`,
        color: color,
        fontFamily: 'var(--font-sans)',
        fontSize: Math.round(size * 0.38),
        fontWeight: 600,
        letterSpacing: '0.02em',
        userSelect: 'none',
        ...style,
      }}
    >
      {initials || '?'}
    </span>
  );
}
