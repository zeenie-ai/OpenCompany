import * as React from 'react';

/**
 * Tooltip — hover label. Dark capsule, full-sentence copy allowed.
 */
export function Tooltip({ label, side = 'top', children, style }) {
  const [show, setShow] = React.useState(false);
  const pos = side === 'bottom'
    ? { top: 'calc(100% + 6px)', left: '50%', transform: 'translateX(-50%)' }
    : { bottom: 'calc(100% + 6px)', left: '50%', transform: 'translateX(-50%)' };
  return (
    <span
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      style={{ position: 'relative', display: 'inline-flex', ...style }}
    >
      {children}
      {show ? (
        <span
          role="tooltip"
          style={{
            position: 'absolute', zIndex: 60, ...pos,
            background: 'var(--fg-default)',
            color: 'var(--bg-app)',
            fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: 500, lineHeight: 1.35,
            borderRadius: 'var(--radius-md, 6px)',
            padding: '5px 9px',
            whiteSpace: 'nowrap',
            maxWidth: 260,
            boxShadow: 'var(--shadow-card-hover)',
            pointerEvents: 'none',
          }}
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}
