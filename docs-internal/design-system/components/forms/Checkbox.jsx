import * as React from 'react';

/**
 * Checkbox — 16px square, primary blue when checked, with label support.
 */
export function Checkbox({ checked, defaultChecked = false, onChange, disabled = false, label, style }) {
  const [internal, setInternal] = React.useState(defaultChecked);
  const isOn = checked !== undefined ? checked : internal;
  const toggle = () => {
    if (disabled) return;
    const next = !isOn;
    if (checked === undefined) setInternal(next);
    onChange && onChange(next);
  };
  return (
    <label
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        userSelect: 'none',
        ...style,
      }}
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={isOn}
        onClick={toggle}
        disabled={disabled}
        style={{
          width: 16,
          height: 16,
          borderRadius: 'var(--radius-sm, 4px)',
          border: '1px solid ' + (isOn ? 'var(--primary)' : 'var(--border-strong)'),
          background: isOn ? 'var(--primary)' : 'var(--bg-input)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
          cursor: 'inherit',
          transition: 'background var(--dur-fast, 90ms), border-color var(--dur-fast, 90ms)',
          flexShrink: 0,
        }}
      >
        {isOn ? (
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--primary-foreground)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 6 9 17l-5-5"></path>
          </svg>
        ) : null}
      </button>
      {label ? <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--fg-default)' }}>{label}</span> : null}
    </label>
  );
}
