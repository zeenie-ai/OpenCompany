import * as React from 'react';

/**
 * EmptyState — dashed drop-zone style placeholder with icon, title,
 * hint and optional action. Matches "No workflows yet" pattern.
 */
export function EmptyState({ icon, title, hint, action, style }) {
  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        gap: 6, textAlign: 'center',
        border: '2px dashed var(--border-default)',
        borderRadius: 'var(--radius-xl, 12px)',
        background: 'transparent',
        padding: '32px 24px',
        ...style,
      }}
    >
      {icon ? <div style={{ color: 'var(--fg-faint)', marginBottom: 4 }}>{icon}</div> : null}
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--fg-default)' }}>{title}</div>
      {hint ? <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--fg-muted)', maxWidth: 320, lineHeight: 1.45 }}>{hint}</div> : null}
      {action ? <div style={{ marginTop: 10 }}>{action}</div> : null}
    </div>
  );
}
