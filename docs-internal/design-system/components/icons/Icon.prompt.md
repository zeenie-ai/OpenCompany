> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Lucide icon by name, colored via currentColor — the only icon system in OpenCompany.

```jsx
<span style={{ color: 'var(--action-run-ink)' }}>
  <Icon name="Play" size={14} />
</span>
```

- Requires the lucide UMD script on the page: `<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>`
- Common names: Play, Square (stop), Save, Settings, KeyRound, FolderOpen, FilePlus, Search, GripVertical, ChevronDown, Zap, Clock, PanelLeftClose, PanelRightClose, Code2, X.
- Stroke 2 at 12–16px UI sizes; use strokeWidth={1.5} at 20px+.
