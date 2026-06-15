Settings panel building blocks — stack Sections inside a PanelModal; each Row pairs a label + description with one control.

```jsx
<SettingsSection title="Auto-save" icon={<Icon name="Save" size={16} />} tone="model">
  <SettingsRow label="Enable Auto-save" description="Automatically save the workflow at regular intervals">
    <Switch defaultChecked />
  </SettingsRow>
  <SettingsRow label="Auto-save Interval" description="How often to auto-save (10-300 seconds)">
    <Input type="number" defaultValue={30} style={{ width: 80 }} />
  </SettingsRow>
</SettingsSection>
```

- Section tones reuse node role colors; pick by topic (UI=agent, saving=model, processes=workflow, audio=tool).
- Descriptions are full sentences and may be long — they wrap under the label, never beside the control.
- `SettingsRow` is exported from this same file.
