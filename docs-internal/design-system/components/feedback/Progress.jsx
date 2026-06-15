import * as React from 'react';

/**
 * Progress — slim bar, accent fill, optional mono value label.
 */
export function Progress({ value = 0, color = 'var(--primary)', label, style }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div style={{ width: '100%', ...style }}>
      {label ? (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500, color: 'var(--fg-muted)' }}>{label}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-muted)' }}>{pct}%</span>
        </div>
      ) : null}
      <div style={{ height: 6, borderRadius: 'var(--radius-pill, 999px)', background: 'var(--bg-hover)', border: '1px solid var(--border-default)', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: pct + '%',
          borderRadius: 'inherit',
          background: color,
          boxShadow: `0 0 8px color-mix(in srgb, ${color} 50%, transparent)`,
          transition: 'width var(--dur-slow, 320ms) var(--ease-default)',
        }} />
      </div>
    </div>
  );
}
