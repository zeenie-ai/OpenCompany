// OpenCompany UI kit — app shell wiring all panels together.
const DS_APP = window.OpenCompanyDesignSystem_2559cf;
const KIT = window.OpenCompanyKit;

const WORKFLOWS = [
  { id: 'wa', name: 'WhatsApp Assistant', nodeCount: 6, modified: 'Jun 11, 09:14' },
  { id: 'digest', name: 'Daily Email Digest', nodeCount: 5, modified: 'Jun 9, 18:30' },
  { id: 'support', name: 'Customer Support Bot', nodeCount: 9, modified: 'Jun 5, 11:02' },
];

function App() {
  const { StatusBar } = DS_APP;
  const { Toolbar, SidebarPanel, PalettePanel, CanvasView, ConsoleDock, SettingsModal, NodeConfigModal, CredentialsModal } = KIT;

  const [dark, setDark] = React.useState(true);
  const [sidebarVisible, setSidebarVisible] = React.useState(true);
  const [paletteVisible, setPaletteVisible] = React.useState(true);
  const [consoleOpen, setConsoleOpen] = React.useState(true);
  const [mode, setMode] = React.useState('normal');
  const [running, setRunning] = React.useState(false);
  const [saved, setSaved] = React.useState(true);
  const [currentWf, setCurrentWf] = React.useState('wa');
  const [selectedId, setSelectedId] = React.useState(null);
  const [extraNodes, setExtraNodes] = React.useState([]);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [credsOpen, setCredsOpen] = React.useState(false);
  const [configNode, setConfigNode] = React.useState(null);

  React.useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
  }, [dark]);

  const wf = WORKFLOWS.find((w) => w.id === currentWf);

  const handleDropNode = (item, section) => {
    const id = 'extra-' + Date.now();
    setExtraNodes((ns) => [
      ...ns,
      {
        id,
        icon: item.icon,
        label: item.name,
        color: section.color,
        x: 620 + ((ns.length * 60) % 180),
        y: 60 + ((ns.length * 50) % 120),
      },
    ]);
    setSaved(false);
    setSelectedId(id);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-app)' }}>
      <Toolbar
        workflowName={wf ? wf.name : 'Untitled Workflow'}
        sidebarVisible={sidebarVisible}
        paletteVisible={paletteVisible}
        mode={mode}
        dark={dark}
        running={running}
        saved={saved}
        onToggleSidebar={() => setSidebarVisible((v) => !v)}
        onTogglePalette={() => setPaletteVisible((v) => !v)}
        onModeChange={setMode}
        onToggleTheme={() => setDark((d) => !d)}
        onRun={() => setRunning((r) => !r)}
        onSave={() => setSaved(true)}
        onSettings={() => setSettingsOpen(true)}
        onCredentials={() => setCredsOpen(true)}
      />
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {sidebarVisible ? (
          <SidebarPanel workflows={WORKFLOWS} currentId={currentWf} onSelect={(id) => { setCurrentWf(id); setSelectedId(null); }} />
        ) : null}
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
          <CanvasView running={running} extraNodes={extraNodes} selectedId={selectedId} onSelect={(id) => setSelectedId((s) => (s === id ? null : id))} onConfigure={(n) => setConfigNode(n)} />
          <ConsoleDock open={consoleOpen} onToggle={() => setConsoleOpen((o) => !o)} />
        </div>
        {paletteVisible ? <PalettePanel mode={mode} onDropNode={handleDropNode} /> : null}
      </div>
      <StatusBar
        connection="online"
        workflowName={wf ? wf.name : '—'}
        nodeCount={6 + extraNodes.length}
        themeName={dark ? 'DARK' : 'LIGHT'}
      />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <CredentialsModal open={credsOpen} onClose={() => setCredsOpen(false)} />
      <NodeConfigModal node={configNode} onClose={() => setConfigNode(null)} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
