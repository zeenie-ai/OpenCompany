// OpenCompany UI kit — workflow sidebar (280px) + component palette (320px).
const DS_PN = window.OpenCompanyDesignSystem_2559cf;

function SidebarPanel({ workflows, currentId, onSelect }) {
  const { WorkflowCard, Icon } = DS_PN;
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', width: 'var(--w-sidebar, 280px)',
      borderRight: '1px solid var(--border-default)', background: 'var(--bg-panel)',
      overflow: 'hidden', flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid var(--border-default)', background: 'var(--bg-app)', padding: '18px 16px' }}>
        <span style={{ width: 36, height: 36, borderRadius: 'var(--radius-md)', background: 'color-mix(in srgb, var(--accent) 20%, transparent)', color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name="FolderOpen" size={16} />
        </span>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-default)' }}>Workflows</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-muted)' }}>{workflows.length} saved</div>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {workflows.map((wf) => (
          <WorkflowCard
            key={wf.id}
            name={wf.name}
            nodeCount={wf.nodeCount}
            modified={wf.modified}
            selected={wf.id === currentId}
            onClick={() => onSelect(wf.id)}
          />
        ))}
      </div>
    </div>
  );
}

const PALETTE_SECTIONS = [
  {
    id: 'ai', label: 'AI', icon: '🤖', color: 'var(--node-agent)', visibility: 'normal',
    items: [
      { icon: '🤖', name: 'AI Agent', description: 'LangGraph agent with memory and tools' },
      { icon: '🧠', name: 'Simple Memory', description: 'Conversation memory store' },
      { icon: '📚', name: 'Skill', description: 'Teach your agent a capability' },
    ],
  },
  {
    id: 'messaging', label: 'Messaging', icon: '💬', color: 'var(--node-trigger)', visibility: 'normal',
    items: [
      { icon: '💬', name: 'WhatsApp Receive', description: 'Trigger on incoming message' },
      { icon: '📤', name: 'WhatsApp Send', description: 'Send a WhatsApp message' },
      { icon: '✈️', name: 'Telegram', description: 'Bot send and receive' },
    ],
  },
  {
    id: 'android', label: 'Android', icon: '📱', color: 'var(--node-tool)', visibility: 'normal',
    items: [
      { icon: '📱', name: 'Android Toolkit', description: '16 device services' },
      { icon: '🔋', name: 'Battery Monitor', description: 'Read battery status' },
      { icon: '🚀', name: 'App Launcher', description: 'Launch apps on device' },
    ],
  },
  {
    id: 'web', label: 'Web', icon: '🔍', color: 'var(--node-model)', visibility: 'normal',
    items: [
      { icon: '🔍', name: 'Web Search Tool', description: 'DuckDuckGo, Brave, Serper' },
      { icon: '🌐', name: 'Browser', description: 'Accessibility-tree navigation' },
    ],
  },
  {
    id: 'code', label: 'Code', icon: '⚙️', color: 'var(--node-workflow)', visibility: 'dev',
    items: [
      { icon: '🐍', name: 'Python', description: 'Run sandboxed Python' },
      { icon: '📜', name: 'JavaScript', description: 'Run sandboxed JS/TS' },
      { icon: '🖥️', name: 'Process Manager', description: 'Own long-running tasks' },
    ],
  },
];

function PalettePanel({ mode, onDropNode }) {
  const { Input, Badge, ComponentItem, Icon } = DS_PN;
  const [query, setQuery] = React.useState('');
  const [collapsed, setCollapsed] = React.useState({});

  const sections = PALETTE_SECTIONS
    .filter((s) => mode === 'dev' || s.visibility === 'normal')
    .map((s) => ({
      ...s,
      items: s.items.filter((it) =>
        !query.trim() ||
        it.name.toLowerCase().includes(query.toLowerCase()) ||
        it.description.toLowerCase().includes(query.toLowerCase())
      ),
    }))
    .filter((s) => s.items.length > 0);

  const total = sections.reduce((acc, s) => acc + s.items.length, 0);

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', width: 'var(--w-palette, 320px)',
      borderLeft: '1px solid var(--border-default)', background: 'var(--bg-panel)',
      overflow: 'hidden', flexShrink: 0,
    }}>
      <div style={{ borderBottom: '1px solid var(--border-default)', background: 'var(--bg-app)', padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-default)' }}>Components</span>
          <Badge mono>{total}</Badge>
        </div>
        <Input placeholder="Search..." icon={<Icon name="Search" size={14} />} value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
        {sections.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--fg-muted)' }}>
            <Icon name="Search" size={40} style={{ opacity: 0.5 }} />
            <p style={{ fontSize: 13, marginTop: 12 }}>No components found matching "{query}"</p>
          </div>
        ) : sections.map((section) => (
          <div key={section.id} style={{ marginBottom: 12 }}>
            <button
              type="button"
              onClick={() => setCollapsed((c) => ({ ...c, [section.id]: !c[section.id] }))}
              style={{
                display: 'flex', width: '100%', alignItems: 'center', justifyContent: 'space-between',
                background: 'none', border: 'none', padding: '6px 4px', cursor: 'pointer',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{
                  display: 'flex', width: 28, height: 28, alignItems: 'center', justifyContent: 'center',
                  borderRadius: 'var(--radius-md)', fontSize: 14,
                  background: `color-mix(in srgb, ${section.color} 8%, transparent)`,
                }}>{section.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-default)' }}>{section.label}</span>
              </span>
              <Badge mono>{section.items.length}</Badge>
            </button>
            {!collapsed[section.id] ? (
              <div style={{ display: 'grid', gap: 8, paddingTop: 8 }}>
                {section.items.map((it) => (
                  <ComponentItem key={it.name} icon={it.icon} name={it.name} description={it.description} onClick={() => onDropNode && onDropNode(it, section)} />
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, { SidebarPanel, PalettePanel });
