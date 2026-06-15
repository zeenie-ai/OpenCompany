import * as React from 'react';

/**
 * ModeToggle — segmented Normal/Dev control from the top toolbar.
 * Active segment gets the role's soft tint (green=Normal, purple=Dev).
 */
export function ModeToggle({ mode = 'normal', onChange, style }) {
  const seg = (id, label, color, dotPath) => {
    const isActive = mode === id;
    return (
      <button
        type="button"
        onClick={() => !isActive && onChange && onChange(id)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          borderRadius: 'var(--radius-sm, 4px)',
          border: '1px solid ' + (isActive ? `color-mix(in srgb, ${color} 30%, transparent)` : 'transparent'),
          background: isActive ? `color-mix(in srgb, ${color} 8%, transparent)` : 'transparent',
          color: isActive ? color : 'var(--fg-muted)',
          fontFamily: 'var(--font-sans)',
          fontSize: 12,
          fontWeight: 600,
          padding: '4px 10px',
          cursor: isActive ? 'default' : 'pointer',
          transition: 'all var(--dur-default, 180ms) var(--ease-default)',
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          {dotPath}
        </svg>
        {label}
      </button>
    );
  };
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        borderRadius: 'var(--radius-md, 6px)',
        border: '1px solid var(--border-default)',
        background: 'var(--surface-card)',
        padding: 2,
        gap: 2,
        ...style,
      }}
    >
      {seg('normal', 'Normal', 'var(--node-tool)', (
        <g><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></g>
      ))}
      {seg('dev', 'Dev', 'var(--node-agent)', (
        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
      ))}
    </div>
  );
}
