import * as React from 'react';

/**
 * Select — 32px dropdown matching Input. Custom menu card with
 * check on the selected option. Placeholder copy ends with "...".
 */
export function Select({ options = [], value, defaultValue, onChange, placeholder = 'Select...', disabled = false, style, wrapperStyle }) {
  const [internal, setInternal] = React.useState(defaultValue);
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  const current = value !== undefined ? value : internal;
  const norm = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o));
  const sel = norm.find((o) => o.value === current);

  React.useEffect(() => {
    if (!open) return;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const pick = (v) => {
    if (value === undefined) setInternal(v);
    onChange && onChange(v);
    setOpen(false);
  };

  return (
    <div ref={ref} style={{ position: 'relative', width: '100%', ...wrapperStyle }}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
          height: 'var(--h-control, 32px)', width: '100%',
          borderRadius: 'var(--radius-lg, 8px)',
          border: `1px solid ${open ? 'var(--border-focus)' : 'var(--border-default)'}`,
          boxShadow: open ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
          background: 'var(--bg-input)',
          color: sel ? 'var(--fg-default)' : 'var(--fg-faint)',
          fontFamily: 'var(--font-sans)', fontSize: 14, padding: '0 10px 0 12px',
          cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
          transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
          textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden',
          ...style,
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{sel ? sel.label : placeholder}</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--fg-muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform var(--dur-default, 180ms) var(--ease-default)' }}>
          <path d="m6 9 6 6 6-6"></path>
        </svg>
      </button>
      {open ? (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 50,
          borderRadius: 'var(--radius-lg, 8px)', border: '1px solid var(--border-default)',
          background: 'var(--bg-elevated)', boxShadow: 'var(--shadow-modal)',
          padding: 4, maxHeight: 220, overflowY: 'auto',
        }}>
          {norm.map((o) => (
            <SelectOption key={o.value} option={o} selected={o.value === current} onPick={pick} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SelectOption({ option, selected, onPick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onClick={() => onPick(option.value)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
        width: '100%', border: 'none', textAlign: 'left',
        borderRadius: 'var(--radius-md, 6px)',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: 'var(--fg-default)', fontFamily: 'var(--font-sans)', fontSize: 13.5,
        padding: '7px 10px', cursor: 'pointer',
      }}
    >
      {option.label}
      {selected ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5"></path></svg>
      ) : null}
    </button>
  );
}
