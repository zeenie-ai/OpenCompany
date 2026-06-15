import * as React from 'react';

/**
 * Switch — 36×20 toggle. On = primary blue track.
 */
export function Switch({ checked, defaultChecked = false, onChange, disabled = false, style, ...rest }) {
  const [internal, setInternal] = React.useState(defaultChecked);
  const isOn = checked !== undefined ? checked : internal;
  const toggle = () => {
    if (disabled) return;
    const next = !isOn;
    if (checked === undefined) setInternal(next);
    onChange && onChange(next);
  };
  return (
    <button
      type="button"
      role="switch"
      aria-checked={isOn}
      onClick={toggle}
      disabled={disabled}
      style={{
        position: 'relative',
        width: 36,
        height: 20,
        borderRadius: 'var(--radius-pill, 999px)',
        border: '1px solid ' + (isOn ? 'transparent' : 'var(--border-default)'),
        background: isOn ? 'var(--primary)' : 'var(--bg-hover)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        padding: 0,
        transition: 'background var(--dur-default, 180ms) var(--ease-default)',
        flexShrink: 0,
        ...style,
      }}
      {...rest}
    >
      <span
        style={{
          position: 'absolute',
          top: 2,
          left: isOn ? 17 : 2,
          width: 14,
          height: 14,
          borderRadius: '50%',
          background: isOn ? 'var(--primary-foreground)' : 'var(--fg-faint)',
          transition: 'left var(--dur-default, 180ms) var(--ease-default), background var(--dur-default, 180ms)',
        }}
      />
    </button>
  );
}
