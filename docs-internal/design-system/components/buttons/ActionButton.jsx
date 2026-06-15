import * as React from 'react';

/**
 * ActionButton — the signature MachinaOS "soft tinted" toolbar button.
 * `intent` is a semantic role, not a color: run / stop / save / config /
 * secret / tools. Soft tint fill (15%), tinted border (60%), accent text;
 * hover deepens the fill to 25%; press nudges down 1px.
 */
export function ActionButton({
  intent = 'save',
  children,
  disabled = false,
  iconOnly = false,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);
  const base = `var(--action-${intent}`;
  return (
    <button
      type="button"
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => { setHover(false); setActive(false); }}
      onMouseDown={() => setActive(true)}
      onMouseUp={() => setActive(false)}
      style={{
        display: 'inline-flex',
        height: 'var(--h-control, 32px)',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        borderRadius: 'var(--radius-md, 6px)',
        padding: iconOnly ? 0 : '0 14px',
        width: iconOnly ? 'var(--h-control, 32px)' : undefined,
        fontFamily: 'var(--font-sans)',
        fontSize: 13,
        fontWeight: 600,
        lineHeight: 1,
        border: `1px solid ${base}-border)`,
        background: hover && !disabled ? `${base}-hover)` : `${base}-soft)`,
        color: `${base}-ink)`,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transform: active && !disabled ? 'translateY(1px)' : 'none',
        transition: 'background var(--dur-default, 180ms) var(--ease-default), transform var(--dur-fast, 90ms)',
        userSelect: 'none',
        whiteSpace: 'nowrap',
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
