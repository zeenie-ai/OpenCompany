> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Count and label chip — palette section counts, node tags, provider labels.

```jsx
<Badge mono>42</Badge>
<Badge variant="accent" color="var(--dracula-purple)">Agent</Badge>
<Badge variant="outline">MIT</Badge>
```

- Counts are always `mono`.
- `accent` follows the soft-tint rule: 12% fill, 30% border, full-strength text.
