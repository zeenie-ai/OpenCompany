import * as React from 'react';

/**
 * Modal — overlay dialog. Flat rgba scrim (no blur), elevated card with
 * --shadow-modal, 18px title, ghost X. `inline` renders just the dialog
 * card (for specimens / embedding).
 */
export function Modal({ open = true, title, children, footer, onClose, width = 440, inline = false, style }) {
  if (!open) return null;
  const card = (
    <div
      role="dialog"
      aria-modal={!inline}
      style={{
        width: '100%', maxWidth: width,
        borderRadius: 'var(--radius-xl, 12px)',
        border: '1px solid var(--border-default)',
        background: 'var(--bg-elevated)',
        boxShadow: 'var(--shadow-modal)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        ...style,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '14px 16px', borderBottom: '1px solid var(--border-default)' }}>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 18, fontWeight: 600, color: 'var(--fg-default)' }}>{title}</span>
        <CloseButton onClick={onClose} />
      </div>
      <div style={{ padding: 16, color: 'var(--fg-default)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.5 }}>{children}</div>
      {footer ? (
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--border-default)', background: 'var(--bg-panel)' }}>
          {footer}
        </div>
      ) : null}
    </div>
  );
  if (inline) return card;
  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'var(--bg-overlay)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 24,
      }}
    >
      {card}
    </div>
  );
}

function CloseButton({ onClick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      title="Close"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 26, height: 26, padding: 0, flexShrink: 0,
        borderRadius: 'var(--radius-md, 6px)', border: 'none',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: 'var(--fg-muted)', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background var(--dur-fast, 90ms)',
      }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>
    </button>
  );
}
