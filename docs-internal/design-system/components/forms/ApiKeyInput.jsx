import * as React from 'react';

/**
 * ApiKeyInput — credentials row: mono masked input with eye toggle,
 * Validate button (→ check + "Valid" when stored), optional delete.
 * Source: client/src/components/ui/ApiKeyInput.tsx.
 */
export function ApiKeyInput({
  value,
  defaultValue = '',
  onChange,
  onSave,
  onDelete,
  placeholder = 'Enter API key...',
  loading = false,
  isStored = false,
  disabled = false,
  saveLabel = 'Validate',
  savedLabel = 'Valid',
  style,
}) {
  const [internal, setInternal] = React.useState(defaultValue);
  const [visible, setVisible] = React.useState(false);
  const [focus, setFocus] = React.useState(false);
  const cur = value !== undefined ? value : internal;

  return (
    <div style={{ display: 'flex', alignItems: 'stretch', gap: 4, width: '100%', ...style }}>
      <div style={{ position: 'relative', flex: 1 }}>
        <input
          type={visible ? 'text' : 'password'}
          value={cur}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(e) => { if (value === undefined) setInternal(e.target.value); onChange && onChange(e.target.value); }}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          style={{
            height: 'var(--h-control, 32px)', width: '100%',
            borderRadius: 'var(--radius-lg, 8px)',
            border: `1px solid ${focus ? 'var(--border-focus)' : 'var(--border-default)'}`,
            boxShadow: focus ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
            background: 'var(--bg-input)', color: 'var(--fg-default)',
            fontFamily: 'var(--font-mono)', fontSize: 13,
            padding: '0 34px 0 12px', outline: 'none',
            transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
          }}
        />
        <button
          type="button"
          aria-label={visible ? 'Hide key' : 'Show key'}
          onClick={() => setVisible((v) => !v)}
          style={{
            position: 'absolute', top: '50%', right: 8, transform: 'translateY(-50%)',
            border: 'none', background: 'none', padding: 2, cursor: 'pointer',
            color: 'var(--fg-faint)', display: 'flex',
          }}
        >
          {visible ? (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"></path><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"></path><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"></path><line x1="2" x2="22" y1="2" y2="22"></line></svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"></path><circle cx="12" cy="12" r="3"></circle></svg>
          )}
        </button>
      </div>

      <ValidateButton
        disabled={!String(cur).trim() || disabled || loading}
        loading={loading}
        isStored={isStored}
        onClick={onSave}
        label={isStored ? savedLabel : saveLabel}
      />

      {isStored && onDelete ? <DeleteButton onClick={onDelete} disabled={disabled} /> : null}
    </div>
  );
}

function ValidateButton({ disabled, loading, isStored, onClick, label }) {
  const [hover, setHover] = React.useState(false);
  const bg = isStored
    ? 'color-mix(in srgb, var(--success) 12%, transparent)'
    : hover && !disabled
      ? 'color-mix(in srgb, var(--primary) 85%, var(--bg-app))'
      : 'var(--primary)';
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        height: 'var(--h-control, 32px)', padding: '0 12px',
        borderRadius: 'var(--radius-lg, 8px)',
        border: isStored ? '1px solid color-mix(in srgb, var(--success) 35%, transparent)' : '1px solid transparent',
        background: bg,
        color: isStored ? 'var(--success)' : 'var(--primary-foreground)',
        fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, lineHeight: 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled && !isStored ? 0.5 : 1,
        whiteSpace: 'nowrap', flexShrink: 0,
        transition: 'background var(--dur-default, 180ms)',
      }}
    >
      {loading ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" style={{ animation: 'opencompany-spin 0.9s linear infinite' }}>
          <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round"></path>
        </svg>
      ) : isStored ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><path d="m9 11 3 3L22 4"></path></svg>
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
      )}
      {label}
      <style>{'@keyframes opencompany-spin { to { transform: rotate(360deg); } }'}</style>
    </button>
  );
}

function DeleteButton({ onClick, disabled }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      title="Delete stored key"
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 'var(--h-control, 32px)', height: 'var(--h-control, 32px)', flexShrink: 0,
        borderRadius: 'var(--radius-lg, 8px)', border: '1px solid transparent',
        background: hover ? 'color-mix(in srgb, var(--destructive) 20%, transparent)' : 'color-mix(in srgb, var(--destructive) 10%, transparent)',
        color: 'var(--destructive)', cursor: disabled ? 'not-allowed' : 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background var(--dur-default, 180ms)',
      }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path></svg>
    </button>
  );
}
