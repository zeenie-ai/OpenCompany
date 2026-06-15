// MachinaOS UI kit — Settings, Node Configuration, and Credentials modals.
// Faithful recreations of SettingsPanel.tsx / ParameterPanel.tsx, composed
// from the DS panel components.
const DS_PM = window.MachinaOSDesignSystem_2559cf;

function SettingsModal({ open, onClose }) {
  const { PanelModal, SettingsSection, SettingsRow, Switch, Input, Slider, Button, ActionButton, Icon } = DS_PM;
  const [ratio, setRatio] = React.useState(80);
  if (!open) return null;
  return (
    <PanelModal
      title="Settings"
      titleIcon={<Icon name="Settings" size={14} />}
      maxWidth="720px" maxHeight="88%"
      onClose={onClose}
      headerActions={
        <div style={{ display: 'flex', gap: 8 }}>
          <ActionButton intent="config" title="Reset to default settings"><Icon name="RotateCcw" size={12} /> Reset</ActionButton>
          <ActionButton intent="run" title="Save settings"><Icon name="Save" size={12} /> Save</ActionButton>
          <ActionButton intent="stop" onClick={onClose} title="Close settings"><Icon name="X" size={12} /> Close</ActionButton>
        </div>
      }
    >
      <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
        <SettingsSection title="UI Defaults" icon={<Icon name="Monitor" size={16} />} tone="agent">
          <SettingsRow label="Sidebar Open by Default" description="Show the sidebar panel when the application starts">
            <Switch defaultChecked />
          </SettingsRow>
          <SettingsRow label="Component Palette Open by Default" description="Show the component palette when the application starts">
            <Switch defaultChecked />
          </SettingsRow>
          <SettingsRow label="Console Panel Open by Default" description="Show the console/chat panel at the bottom when the application starts">
            <Switch defaultChecked />
          </SettingsRow>
          <SettingsRow label="Auto-add Skill for Connected Tools" description="When a tool node is connected to an AI agent, automatically enable the matching skill in the agent's Master Skill">
            <Switch defaultChecked />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title="Auto-save" icon={<Icon name="Save" size={16} />} tone="model">
          <SettingsRow label="Enable Auto-save" description="Automatically save the workflow at regular intervals">
            <Switch defaultChecked />
          </SettingsRow>
          <SettingsRow label="Auto-save Interval" description="How often to auto-save (10-300 seconds)">
            <div style={{ position: 'relative', width: 96 }}>
              <Input type="number" defaultValue={30} min={10} max={300} step={5} style={{ paddingRight: 24 }} />
              <span style={{ position: 'absolute', top: '50%', right: 8, transform: 'translateY(-50%)', fontSize: 12, color: 'var(--fg-muted)', pointerEvents: 'none' }}>s</span>
            </div>
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title="Memory & Compaction" icon={<Icon name="Brain" size={16} />} tone="agent">
          <SettingsRow label="Default Window Size" description="Number of message pairs to keep in short-term memory (1-100)">
            <Input type="number" defaultValue={100} min={1} max={100} style={{ width: 80 }} />
          </SettingsRow>
          <div style={{ borderBottom: '1px solid var(--border-default)', margin: '4px 0' }}></div>
          <div style={{ padding: '8px 0' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--fg-default)' }}>Compaction Ratio</div>
                <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>Fraction of context window that triggers memory compaction</div>
              </div>
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--node-model)', minWidth: 42, textAlign: 'right' }}>{ratio}%</span>
            </div>
            <Slider min={5} max={95} step={5} value={ratio} onChange={setRatio} color="var(--node-model)" style={{ margin: '12px 0 6px' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--fg-muted)' }}>
              <span>5%</span><span>50%</span><span>95%</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6, lineHeight: 1.45 }}>
              Lower = compact sooner (saves tokens, loses detail). Higher = compact later (preserves context, uses more tokens).
            </div>
          </div>
        </SettingsSection>

        <SettingsSection title="Audio" icon={<Icon name="Volume2" size={16} />} tone="tool">
          <SettingsRow label="Sound Effects" description="Play per-theme click / hover / save / error sounds. Each theme ships a different pack (parchment, terminal, marble, ink, clockwork, ...)">
            <Switch defaultChecked />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title="Help" icon={<Icon name="HelpCircle" size={16} />} tone="model" style={{ marginBottom: 0 }}>
          <SettingsRow label="Replay Welcome Guide" description="Show the onboarding wizard again to review platform features">
            <Button size="sm"><Icon name="HelpCircle" size={13} /> Replay</Button>
          </SettingsRow>
        </SettingsSection>
      </div>
    </PanelModal>
  );
}

function NodeConfigModal({ node, onClose }) {
  const { PanelModal, DataCard, CollapsibleSection, Select, Textarea, Slider, Checkbox, ActionButton, EmptyState, Icon } = DS_PM;
  const [temp, setTemp] = React.useState(70);
  const [ran, setRan] = React.useState(false);
  if (!node) return null;
  const colHead = (icon, text) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderBottom: '1px solid var(--border-default)', background: 'var(--bg-panel)', fontSize: 13, fontWeight: 600, color: 'var(--fg-default)', flexShrink: 0 }}>
      <Icon name={icon} size={14} /> {text}
    </div>
  );
  return (
    <PanelModal
      title="Node Configuration"
      titleIcon={<Icon name="Settings" size={14} />}
      maxWidth="95%" maxHeight="92%"
      onClose={onClose}
      headerActions={
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 600, color: 'var(--fg-default)' }}>
            <span style={{ fontSize: 18 }}>{node.icon}</span> {node.label}
            <span style={{ color: 'var(--warning)' }}>*</span>
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <ActionButton intent="run" title="Execute this node" onClick={() => setRan(true)}><Icon name="Play" size={12} /> Run</ActionButton>
            <ActionButton intent="tools" title="Save parameters"><Icon name="Save" size={12} /> Save</ActionButton>
            <ActionButton intent="stop" onClick={onClose}><Icon name="X" size={12} /> Cancel</ActionButton>
          </div>
        </div>
      }
    >
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div style={{ flex: 0.7, minWidth: 0, display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border-default)' }}>
          {colHead('Link2', 'Input Data (1 item)')}
          <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
            <DataCard
              title="Item 1"
              badge="from WhatsApp Receive"
              data={{ message: "What's on my calendar today?", from: '+1 555 014 2236', timestamp: '2026-06-12T09:14:02Z' }}
            />
          </div>
          <div style={{ padding: '8px 14px', borderTop: '1px solid var(--border-default)', fontSize: 11, color: 'var(--fg-muted)', flexShrink: 0 }}>
            Shows actual data received by this node during execution
          </div>
        </div>

        <div style={{ flex: 1.6, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          {colHead('SlidersHorizontal', 'Parameters')}
          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-default)' }}>Model</span>
              <Select defaultValue="claude-sonnet-4-5" options={['claude-sonnet-4-5', 'gpt-4o', 'llama3.1:8b', 'gemini-2.0-flash']} />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-default)' }}>System Prompt</span>
              <Textarea rows={4} defaultValue="You are a personal assistant with access to my calendar, email, and messages. Answer briefly and take action when asked." />
            </label>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-default)' }}>Temperature</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--node-model)' }}>{(temp / 100).toFixed(2)}</span>
              </div>
              <Slider min={0} max={100} step={5} value={temp} onChange={setTemp} color="var(--node-model)" />
            </div>
            <CollapsibleSection title={<><Icon name="Wrench" size={13} /> Connected Skills</>}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <Checkbox defaultChecked label="Web Search — search the web via DuckDuckGo" />
                <Checkbox defaultChecked label="Android Toolkit — 16 device services" />
                <Checkbox label="Code Execution — sandboxed Python" />
              </div>
            </CollapsibleSection>
          </div>
        </div>

        <div style={{ flex: 0.7, minWidth: 0, display: 'flex', flexDirection: 'column', borderLeft: '1px solid var(--border-default)' }}>
          {colHead('ArrowRightFromLine', 'Output')}
          <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
            {ran ? (
              <DataCard
                title="Execution Result"
                badge="Success · 1.9s"
                blockLabel="Response"
                data={{ response: 'You have 2 events today: standup at 10:00 and a dentist appointment at 15:30.' }}
              />
            ) : (
              <EmptyState
                icon={<Icon name="Play" size={26} strokeWidth={1.5} />}
                title="No output yet"
                hint="Run this node to see its output"
                style={{ padding: '24px 16px' }}
              />
            )}
          </div>
        </div>
      </div>
    </PanelModal>
  );
}

function CredentialsModal({ open, onClose }) {
  const { PanelModal, ApiKeyInput, Avatar, ActionButton, Icon } = DS_PM;
  if (!open) return null;
  const providers = [
    { name: 'Anthropic', color: 'var(--dracula-orange)', stored: true, val: 'sk-ant-api03-xxxxxxxxxxxxxxxx' },
    { name: 'Open AI', color: 'var(--dracula-cyan)', stored: false, val: '' },
    { name: 'Groq', color: 'var(--dracula-pink)', stored: false, val: '' },
    { name: 'Google', color: 'var(--dracula-green)', stored: true, val: 'AIzaSyxxxxxxxxxxxxxxxx' },
  ];
  return (
    <PanelModal
      title="API Credentials"
      titleIcon={<Icon name="KeyRound" size={14} />}
      maxWidth="640px" maxHeight="80%"
      onClose={onClose}
      headerActions={
        <ActionButton intent="stop" onClick={onClose}><Icon name="X" size={12} /> Close</ActionButton>
      }
    >
      <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ fontSize: 12.5, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
          Bring your own keys — they're stored locally and never leave your machine.
        </div>
        {providers.map((p) => (
          <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Avatar name={p.name} color={p.color} square />
            <span style={{ width: 86, fontSize: 13, fontWeight: 500, color: 'var(--fg-default)', flexShrink: 0 }}>{p.name}</span>
            <ApiKeyInput defaultValue={p.val} isStored={p.stored} onSave={() => {}} onDelete={p.stored ? () => {} : undefined} placeholder="Enter API key..." />
          </div>
        ))}
      </div>
    </PanelModal>
  );
}

window.MachinaKit = Object.assign(window.MachinaKit || {}, { SettingsModal, NodeConfigModal, CredentialsModal });
