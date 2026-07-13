// OpenCompany UI kit — multi-tab console dock (Chat / Console / Terminal).
const DS_CD = window.OpenCompanyDesignSystem_2559cf;

const CONSOLE_LINES = [
  { t: '12:01:31', tone: 'var(--fg-muted)', text: 'Workflow "WhatsApp Assistant" loaded · 6 nodes' },
  { t: '12:01:33', tone: 'var(--success)', text: 'Deployed — listening for incoming messages' },
  { t: '12:04:02', tone: 'var(--dracula-cyan)', text: 'WhatsApp Receive → message from +1 (555) 014-2236' },
  { t: '12:04:03', tone: 'var(--dracula-purple)', text: 'AI Agent → delegating to Web Search Tool' },
  { t: '12:04:05', tone: 'var(--success)', text: 'WhatsApp Send → reply delivered (1.9s)' },
];

function ConsoleDock({ open, onToggle }) {
  const { Tabs, Icon, Input } = DS_CD;
  const [tab, setTab] = React.useState('console');
  return (
    <div style={{ borderTop: '1px solid var(--border-default)', background: 'var(--bg-panel)', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <Tabs
          tabs={[{ id: 'chat', label: 'Chat' }, { id: 'console', label: 'Console' }, { id: 'terminal', label: 'Terminal' }]}
          active={tab}
          onChange={(id) => { setTab(id); if (!open) onToggle(); }}
          style={{ flex: 1, borderBottom: open ? '1px solid var(--border-default)' : 'none' }}
        />
        <button
          type="button"
          onClick={onToggle}
          title={open ? 'Collapse console' : 'Expand console'}
          style={{ background: 'none', border: 'none', color: 'var(--fg-muted)', cursor: 'pointer', padding: '6px 12px' }}
        >
          <Icon name={open ? 'ChevronDown' : 'ChevronUp'} size={14} />
        </button>
      </div>
      {open ? (
        <div style={{ height: 150, overflowY: 'auto', padding: '10px 14px', background: 'var(--bg-app)' }}>
          {tab === 'console' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {CONSOLE_LINES.map((l, i) => (
                <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, display: 'flex', gap: 10 }}>
                  <span style={{ color: 'var(--fg-faint)' }}>[{l.t}]</span>
                  <span style={{ color: l.tone }}>{l.text}</span>
                </div>
              ))}
            </div>
          ) : tab === 'chat' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%' }}>
              <div style={{ alignSelf: 'flex-end', maxWidth: 420, background: 'color-mix(in srgb, var(--primary) 18%, transparent)', border: '1px solid color-mix(in srgb, var(--primary) 35%, transparent)', borderRadius: '10px 10px 2px 10px', padding: '8px 12px', fontSize: 13, color: 'var(--fg-default)' }}>
                Summarize my unread emails every weekday at 9 AM.
              </div>
              <div style={{ alignSelf: 'flex-start', maxWidth: 420, background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: '10px 10px 10px 2px', padding: '8px 12px', fontSize: 13, color: 'var(--fg-default)' }}>
                Done — I scheduled a daily digest. Want it on WhatsApp too?
              </div>
              <div style={{ marginTop: 'auto' }}>
                <Input placeholder="Message your agent..." />
              </div>
            </div>
          ) : (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg-default)' }}>
              <div><span style={{ color: 'var(--success)' }}>$</span> company start</div>
              <div style={{ color: 'var(--fg-muted)', marginTop: 4 }}>OpenCompany running at http://localhost:3000</div>
              <div style={{ color: 'var(--fg-muted)' }}>Temporal · Python backend · WhatsApp service — all up</div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, { ConsoleDock });
