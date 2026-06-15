import * as React from 'react';

/**
 * Tabs — console-dock style tab strip (Chat / Console / Terminal).
 * Active tab gets accent text + 2px underline.
 */
export function Tabs({ tabs, active, onChange, style }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 2,
        borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-panel)',
        padding: '0 8px',
        ...style,
      }}
    >
      {tabs.map((tab) => {
        const id = typeof tab === 'string' ? tab : tab.id;
        const label = typeof tab === 'string' ? tab : tab.label;
        const isActive = active === id;
        return (
          <TabButton key={id} isActive={isActive} onClick={() => onChange && onChange(id)}>
            {label}
          </TabButton>
        );
      })}
    </div>
  );
}

function TabButton({ isActive, onClick, children }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        appearance: 'none',
        background: hover && !isActive ? 'var(--bg-hover)' : 'transparent',
        border: 'none',
        borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
        color: isActive ? 'var(--accent)' : 'var(--fg-muted)',
        fontFamily: 'var(--font-sans)',
        fontSize: 13,
        fontWeight: isActive ? 600 : 500,
        padding: '8px 12px',
        cursor: 'pointer',
        transition: 'color var(--dur-fast, 90ms), background var(--dur-fast, 90ms)',
        marginBottom: -1,
      }}
    >
      {children}
    </button>
  );
}
