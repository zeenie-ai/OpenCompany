import * as React from 'react';

/**
 * Button — shadcn-derived general button. Solid primary, outline,
 * secondary, ghost, destructive (soft red tint), link.
 */
export function Button({
  variant = 'default',
  size = 'default',
  children,
  disabled = false,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);

  const sizes = {
    sm: { height: 28, padding: '0 10px', fontSize: 13 },
    default: { height: 32, padding: '0 12px', fontSize: 14 },
    lg: { height: 36, padding: '0 16px', fontSize: 14 },
    icon: { height: 32, width: 32, padding: 0, fontSize: 14 },
    'icon-sm': { height: 28, width: 28, padding: 0, fontSize: 13 },
  };

  const variants = {
    default: {
      background: hover ? 'color-mix(in srgb, var(--primary) 85%, var(--bg-app))' : 'var(--primary)',
      color: 'var(--primary-foreground)',
      border: '1px solid transparent',
    },
    outline: {
      background: hover ? 'var(--bg-hover)' : 'var(--bg-elevated)',
      color: 'var(--fg-default)',
      border: '1px solid var(--border-default)',
    },
    secondary: {
      background: hover ? 'var(--bg-active)' : 'var(--bg-hover)',
      color: 'var(--fg-default)',
      border: '1px solid transparent',
    },
    ghost: {
      background: hover ? 'var(--bg-hover)' : 'transparent',
      color: 'var(--fg-default)',
      border: '1px solid transparent',
    },
    destructive: {
      background: hover
        ? 'color-mix(in srgb, var(--destructive) 20%, transparent)'
        : 'color-mix(in srgb, var(--destructive) 10%, transparent)',
      color: 'var(--destructive)',
      border: '1px solid transparent',
    },
    link: {
      background: 'transparent',
      color: 'var(--primary)',
      border: '1px solid transparent',
      textDecoration: hover ? 'underline' : 'none',
    },
  };

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
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        borderRadius: 'var(--radius-lg, 8px)',
        fontFamily: 'var(--font-sans)',
        fontWeight: 500,
        lineHeight: 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transform: active && !disabled ? 'translateY(1px)' : 'none',
        transition: 'background var(--dur-default, 180ms) var(--ease-default), transform var(--dur-fast, 90ms)',
        userSelect: 'none',
        whiteSpace: 'nowrap',
        ...sizes[size],
        ...variants[variant],
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
