> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Empty list / drop zone. Dashed 2px border is the product's empty-state signature.

```jsx
<EmptyState
  icon={<Icon name="FolderOpen" size={32} strokeWidth={1.5} />}
  title="No workflows yet"
  hint="Create your first workflow to get started"
  action={<Button><Icon name="FilePlus" size={14} /> New Workflow</Button>}
/>
```

- Title: terse "No X yet". Hint: one imperative sentence, no period needed.
