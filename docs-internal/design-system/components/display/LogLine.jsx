import * as React from 'react';

/**
 * LogLine — console output row: faint [timestamp] + toned message,
 * all mono. Tones map to event kinds.
 */
const LOG_TONES = {
  muted: 'var(--fg-muted)',
  success: 'var(--success)',
  error: 'var(--destructive)',
  warning: 'var(--warning)',
  agent: 'var(--node-agent)',
  model: 'var(--node-model)',
  tool: 'var(--node-tool)',
  trigger: 'var(--node-trigger)',
};

export function LogLine({ time, tone = 'muted', children, style }) {
  return (
    <div style={{ display: 'flex', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.5, ...style }}>
      {time ? <span style={{ color: 'var(--fg-faint)', flexShrink: 0 }}>[{time}]</span> : null}
      <span style={{ color: LOG_TONES[tone] || tone, minWidth: 0, overflowWrap: 'break-word' }}>{children}</span>
    </div>
  );
}
