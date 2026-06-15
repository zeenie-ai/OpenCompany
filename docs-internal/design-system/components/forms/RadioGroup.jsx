import * as React from 'react';

/**
 * RadioGroup — vertical or horizontal radio list. 16px circles,
 * primary blue dot when selected.
 */
export function RadioGroup({ options = [], value, defaultValue, onChange, direction = 'column', disabled = false, style }) {
  const [internal, setInternal] = React.useState(defaultValue);
  const current = value !== undefined ? value : internal;
  const norm = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o));
  const pick = (v) => {
    if (disabled) return;
    if (value === undefined) setInternal(v);
    onChange && onChange(v);
  };
  return (
    <div role="radiogroup" style={{ display: 'flex', flexDirection: direction, gap: direction === 'column' ? 8 : 16, opacity: disabled ? 0.5 : 1, ...style }}>
      {norm.map((o) => {
        const on = o.value === current;
        return (
          <label key={o.value} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: disabled ? 'not-allowed' : 'pointer', userSelect: 'none' }}>
            <button
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled}
              onClick={() => pick(o.value)}
              style={{
                width: 16, height: 16, borderRadius: '50%', padding: 0, flexShrink: 0,
                border: `1px solid ${on ? 'var(--primary)' : 'var(--border-strong)'}`,
                background: 'var(--bg-input)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'inherit',
                transition: 'border-color var(--dur-fast, 90ms)',
              }}
            >
              {on ? <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--primary)' }} /> : null}
            </button>
            <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--fg-default)' }}>{o.label}</span>
          </label>
        );
      })}
    </div>
  );
}
