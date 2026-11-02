> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Standard 32px text field; search fields get a leading Search icon.

```jsx
<Input placeholder="Search..." icon={<Icon name="Search" size={14} />} />
<Input type="password" placeholder="sk-..." />
```

- Placeholder copy is terse, often with trailing ellipsis: "Search...", "Select model...".
- Focus = blue border + 3px soft ring.
