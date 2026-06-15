import * as React from 'react';

/**
 * Toast — status notification: soft tint, status pip, mono timestamp.
 */
const TONES = {
  success: 'var(--success)',
  error: 'var(--destructive)',
  warning: 'var(--warning)',
  info: 'var(--info)',
};

export function Toast({ tone = 'info', title, message, time, onClose, style }) {
  const c = TONES[tone] || TONES.info;
  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10,
        width: '100%', maxWidth: 360,
        borderRadius: 'var(--radius-lg, 8px)',
        border: `1px solid color-mix(in srgb, ${c} 35%, transparent)`,
        background: `color-mix(in srgb, ${c} 8%, var(--bg-elevated))`,
        boxShadow: 'var(--shadow-card)',
        padding: '10px 12px',
        fontFamily: 'var(--font-sans)',
        ...style,
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, boxShadow: `0 0 6px color-mix(in srgb, ${c} 60%, transparent)`, marginTop: 5, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-default)' }}>{title}</span>
          {time ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-faint)', flexShrink: 0 }}>{time}</span> : null}
        </div>
        {message ? <div style={{ fontSize: 12.5, color: 'var(--fg-muted)', marginTop: 2, lineHeight: 1.4 }}>{message}</div> : null}
      </div>
      {onClose ? (
        <button type="button" onClick={onClose} title="Dismiss" style={{ border: 'none', background: 'none', color: 'var(--fg-faint)', cursor: 'pointer', padding: 0, lineHeight: 1, flexShrink: 0 }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>
        </button>
      ) : null}
    </div>
  );
}
