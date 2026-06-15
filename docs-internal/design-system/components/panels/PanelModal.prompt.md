Workspace modal shell — Node Configuration, Settings, AI Result all use this exact header anatomy: title left, centered ActionButton cluster, X right.

```jsx
<PanelModal
  title="Node Configuration"
  titleIcon={<Icon name="Settings" size={15} />}
  maxWidth="95%" maxHeight="95%"
  onClose={close}
  headerActions={
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 600 }}>🤖 AI Agent <span style={{ color: 'var(--warning)' }}>*</span></span>
      <div style={{ display: 'flex', gap: 8 }}>
        <ActionButton intent="run"><Icon name="Play" size={12} /> Run</ActionButton>
        <ActionButton intent="tools"><Icon name="Save" size={12} /> Save</ActionButton>
        <ActionButton intent="stop"><Icon name="X" size={12} /> Cancel</ActionButton>
      </div>
    </div>
  }
>
  …panel body…
</PanelModal>
```

- The unsaved-changes marker is a warning-colored `*` after the node name.
- Big panels (parameter, settings) use maxWidth/maxHeight "95%"; body owns its own scroll regions.
- For small alert dialogs, use Modal (feedback/) instead.
