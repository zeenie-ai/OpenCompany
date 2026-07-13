// OpenCompany UI kit — top toolbar (48px, bg-panel).
// Composes ActionButton / Button / ModeToggle / Icon from the DS bundle.
const DS_TB = window.OpenCompanyDesignSystem_2559cf;

function Divider() {
  return <div style={{ width: 1, height: 24, background: 'var(--border-default)', margin: '0 4px', flexShrink: 0 }}></div>;
}

function Toolbar({
  workflowName, sidebarVisible, paletteVisible, mode, dark, running, saved,
  onToggleSidebar, onTogglePalette, onModeChange, onToggleTheme, onRun, onSave,
  onSettings, onCredentials,
}) {
  const { ActionButton, Icon, ModeToggle } = DS_TB;
  return (
    <div style={{
      display: 'flex', height: 'var(--h-toolbar, 48px)', alignItems: 'center',
      justifyContent: 'space-between', gap: 12, borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-panel)', padding: '0 12px', flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <ActionButton intent="save" iconOnly title={sidebarVisible ? 'Hide sidebar' : 'Show sidebar'} onClick={onToggleSidebar}>
          <Icon name={sidebarVisible ? 'PanelLeftClose' : 'PanelLeftOpen'} size={14} />
        </ActionButton>
        <Divider />
        <ActionButton intent="save">
          <Icon name="FileText" size={13} /> File <Icon name="ChevronDown" size={12} />
        </ActionButton>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, justifyContent: 'center', minWidth: 0 }}>
        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--fg-default)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{workflowName}</span>
        <Icon name="Pencil" size={11} style={{ color: 'var(--fg-muted)' }} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--node-model)', whiteSpace: 'nowrap' }}>Mode:</span>
        <ModeToggle mode={mode} onChange={onModeChange} />
        <Divider />
        <ActionButton intent="config" iconOnly title="Settings" onClick={onSettings}><Icon name="Settings" size={14} /></ActionButton>
        <ActionButton intent="secret" iconOnly title="API Credentials" onClick={onCredentials}><Icon name="KeyRound" size={14} /></ActionButton>
        <ActionButton intent="tools" iconOnly title="Toggle theme" onClick={onToggleTheme}>
          <Icon name={dark ? 'Sun' : 'Moon'} size={14} />
        </ActionButton>
        <Divider />
        {!running ? (
          <ActionButton intent="run" title="Start workflow" onClick={onRun}>
            <Icon name="Play" size={12} /> Start
          </ActionButton>
        ) : (
          <ActionButton intent="stop" title="Stop workflow" onClick={onRun}>
            <Icon name="Square" size={12} /> Stop
          </ActionButton>
        )}
        <ActionButton intent="save" disabled={saved} title={saved ? 'No changes to save' : 'Save changes'} onClick={onSave}>
          <Icon name="Save" size={12} /> Save
        </ActionButton>
        <span style={{
          display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
          fontFamily: 'var(--font-mono)', fontSize: 12,
          color: saved ? 'var(--success)' : 'var(--warning)',
        }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: saved ? 'var(--success)' : 'var(--warning)' }}></span>
          {saved ? 'Saved' : 'Modified'}
        </span>
        <Divider />
        <ActionButton intent="tools" iconOnly title={paletteVisible ? 'Hide components' : 'Show components'} onClick={onTogglePalette}>
          <Icon name={paletteVisible ? 'PanelRightClose' : 'PanelRightOpen'} size={14} />
        </ActionButton>
      </div>
    </div>
  );
}

window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, { Toolbar });
