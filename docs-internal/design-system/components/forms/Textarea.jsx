import * as React from 'react';

/**
 * Textarea — multi-line Input. Mono option for prompts/code.
 */
export function Textarea({ mono = false, rows = 4, style, ...rest }) {
  const [focus, setFocus] = React.useState(false);
  return (
    <textarea
      rows={rows}
      onFocus={(e) => { setFocus(true); rest.onFocus && rest.onFocus(e); }}
      onBlur={(e) => { setFocus(false); rest.onBlur && rest.onBlur(e); }}
      style={{
        width: '100%',
        borderRadius: 'var(--radius-lg, 8px)',
        border: `1px solid ${focus ? 'var(--border-focus)' : 'var(--border-default)'}`,
        boxShadow: focus ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
        background: 'var(--bg-input)',
        color: 'var(--fg-default)',
        fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
        fontSize: mono ? 13 : 14,
        lineHeight: 1.5,
        padding: '8px 12px',
        outline: 'none',
        resize: 'vertical',
        transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
        ...style,
      }}
      {...rest}
    />
  );
}
