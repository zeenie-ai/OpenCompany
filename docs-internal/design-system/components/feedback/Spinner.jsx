import * as React from 'react';

/**
 * Spinner — rotating arc in an accent color. Pair with mono label.
 */
export function Spinner({ size = 16, color = 'var(--primary)', style }) {
  return (
    <span style={{ display: 'inline-flex', ...style }}>
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ animation: 'opencompany-spin 0.9s linear infinite' }}>
        <circle cx="12" cy="12" r="9" stroke={`color-mix(in srgb, ${color} 20%, transparent)`} strokeWidth="3"></circle>
        <path d="M21 12a9 9 0 0 0-9-9" stroke={color} strokeWidth="3" strokeLinecap="round"></path>
      </svg>
      <style>{'@keyframes opencompany-spin { to { transform: rotate(360deg); } }'}</style>
    </span>
  );
}
