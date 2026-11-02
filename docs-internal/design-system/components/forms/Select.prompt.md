> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Dropdown for model pickers, provider choices, theme selection.

```jsx
<Select placeholder="Select model..." options={['claude-sonnet-4-5', 'gpt-4o', 'llama3.1:8b']} onChange={(v) => setModel(v)} />
```

- Same 32px metrics + focus ring as Input.
- Placeholder copy is terse with trailing ellipsis ("Select model...").
