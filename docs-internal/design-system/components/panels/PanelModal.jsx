import * as React from 'react';

/**
 * PanelModal — the product's workspace modal: header bar with title
 * absolute-left (icon + display font), centered headerActions (the
 * Run/Save/Cancel ActionButton cluster), X absolute-right. Header sits
 * on --bg-panel one step above the --bg-app body. Source:
 * client/src/components/ui/Modal.tsx.
 */
export function PanelModal({
  open = true,
  title,
  titleIcon,
  headerActions,
  children,
  onClose,
  maxWidth = '90%',
  maxHeight = '88%',
  inline = false,
  style,
}) {
  if (!open) return null;
  const card = (
    <div
      role="dialog"
      aria-modal={!inline}
      className="modal modal-frame"
      style={{
        width: '100%', maxWidth, height: inline ? 'auto' : '100%', maxHeight,
        borderRadius: 'var(--radius-lg, 8px)',
        border: '1px solid var(--border-default)',
        background: 'var(--bg-app)',
        boxShadow: 'var(--shadow-modal)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        ...style,
      }}
    >
      <div style={{
        position: 'relative', display: 'flex', alignItems: 'center', width: '100%',
        borderBottom: '1px solid var(--border-default)', background: 'var(--bg-panel)',
        padding: '10px 20px', minHeight: 48, flexShrink: 0,
      }}>
        <span style={{
          position: 'absolute', left: 20, display: 'flex', alignItems: 'center', gap: 8,
          fontFamily: 'var(--font-display, var(--font-sans))', fontSize: 15, fontWeight: 600,
          letterSpacing: 'var(--type-tracking-display, 0)',
          textTransform: 'var(--type-uppercase, none)',
          color: 'var(--fg-default)',
        }}>
          {titleIcon ? <span style={{ opacity: 0.7, display: 'flex' }}>{titleIcon}</span> : null}
          {title}
        </span>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {headerActions}
        </div>
        <PanelClose onClick={onClose} />
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {children}
      </div>
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

function PanelClose({ onClick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      aria-label="Close"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'absolute', right: 20,
        width: 32, height: 32, padding: 0,
        borderRadius: 'var(--radius-md, 6px)', border: 'none',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: hover ? 'var(--fg-default)' : 'var(--fg-muted)',
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background var(--dur-fast, 90ms), color var(--dur-fast, 90ms)',
      }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>
    </button>
  );
}
