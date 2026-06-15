import * as React from 'react';

/**
 * Input — 32px text field. Optional leading icon (pass a ReactNode).
 * Focus ring is the solarized blue --border-focus.
 */
export function Input({ icon, style, wrapperStyle, ...rest }) {
  const [focus, setFocus] = React.useState(false);
  return (
    <div style={{ position: 'relative', display: 'flex', alignItems: 'center', width: '100%', ...wrapperStyle }}>
      {icon ? (
        <span
          style={{
            position: 'absolute',
            left: 10,
            display: 'flex',
            alignItems: 'center',
            color: 'var(--fg-faint)',
            pointerEvents: 'none',
          }}
        >
          {icon}
        </span>
      ) : null}
      <input
        onFocus={(e) => { setFocus(true); rest.onFocus && rest.onFocus(e); }}
        onBlur={(e) => { setFocus(false); rest.onBlur && rest.onBlur(e); }}
        style={{
          height: 'var(--h-control, 32px)',
          width: '100%',
          borderRadius: 'var(--radius-lg, 8px)',
          border: `1px solid ${focus ? 'var(--border-focus)' : 'var(--border-default)'}`,
          boxShadow: focus ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
          background: 'var(--bg-input)',
          color: 'var(--fg-default)',
          fontFamily: 'var(--font-sans)',
          fontSize: 14,
          padding: icon ? '0 12px 0 32px' : '0 12px',
          outline: 'none',
          transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
          ...style,
        }}
        {...rest}
      />
    </div>
  );
}
