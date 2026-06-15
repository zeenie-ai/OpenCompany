import * as React from 'react';

/**
 * ChatBubble — dock Chat tab message. User = right, primary tint,
 * square corner bottom-right; agent = left, card surface.
 */
export function ChatBubble({ role = 'agent', time, children, style }) {
  const user = role === 'user';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: user ? 'flex-end' : 'flex-start', gap: 3, ...style }}>
      <div
        style={{
          maxWidth: 420,
          borderRadius: user ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
          border: user
            ? '1px solid color-mix(in srgb, var(--primary) 35%, transparent)'
            : '1px solid var(--border-default)',
          background: user
            ? 'color-mix(in srgb, var(--primary) 18%, transparent)'
            : 'var(--surface-card)',
          color: 'var(--fg-default)',
          fontFamily: 'var(--font-sans)', fontSize: 13.5, lineHeight: 1.45,
          padding: '8px 12px',
        }}
      >
        {children}
      </div>
      {time ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-faint)', padding: '0 2px' }}>{time}</span> : null}
    </div>
  );
}
