> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Bottom system console line — reads like a shell prompt.

```jsx
<StatusBar connection="online" workflowName="WhatsApp Assistant" nodeCount={12} themeName="DARK" />
```

- Always 24px, pinned below the canvas, above nothing.
- Everything mono uppercase; values in `--fg-default`, labels muted.
