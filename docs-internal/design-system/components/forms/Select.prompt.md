Dropdown for model pickers, provider choices, theme selection.

```jsx
<Select placeholder="Select model..." options={['claude-sonnet-4-5', 'gpt-4o', 'llama3.1:8b']} onChange={(v) => setModel(v)} />
```

- Same 32px metrics + focus ring as Input.
- Placeholder copy is terse with trailing ellipsis ("Select model...").
