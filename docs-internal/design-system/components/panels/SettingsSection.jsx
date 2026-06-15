import * as React from 'react';

/**
 * SettingsSection + SettingsRow — the Settings panel's building blocks.
 * Section = elevated card with a toned 32px icon tile + display-font
 * title over a divider. Row = label + description left, control right.
 * Source: client/src/components/ui/SettingsPanel.tsx.
 */
const SECTION_TONES = {
  agent: 'var(--node-agent)',
  model: 'var(--node-model)',
  workflow: 'var(--node-workflow)',
  tool: 'var(--node-tool)',
  trigger: 'var(--node-trigger)',
};

export function SettingsSection({ title, icon, tone = 'agent', children, style }) {
  const c = SECTION_TONES[tone] || tone;
  return (
    <div style={{
      marginBottom: 16,
      borderRadius: 'var(--radius-md, 6px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-elevated)',
      padding: 16,
      ...style,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        borderBottom: '1px solid var(--border-default)',
        paddingBottom: 12, marginBottom: 12,
      }}>
        <span style={{
          display: 'flex', width: 32, height: 32, alignItems: 'center', justifyContent: 'center',
          borderRadius: 'var(--radius-md, 6px)',
          background: `color-mix(in srgb, ${c} 8%, transparent)`,
          color: c, flexShrink: 0,
        }}>
          {icon}
        </span>
        <span style={{
          fontFamily: 'var(--font-display, var(--font-sans))',
          fontSize: 16, fontWeight: 600,
          letterSpacing: 'var(--type-tracking-display, 0)',
          textTransform: 'var(--type-uppercase, none)',
          color: 'var(--fg-default)',
        }}>
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

export function SettingsRow({ label, description, children, style }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '8px 0', ...style }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--fg-default)' }}>{label}</div>
        {description ? <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--fg-muted)', marginTop: 2, lineHeight: 1.4 }}>{description}</div> : null}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}
