> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Initials tile for providers, agents, users. Soft-tint rule applies.

```jsx
<Avatar name="Claude" color="var(--dracula-orange)" />
<Avatar name="AI Agent" color="var(--node-agent)" square />
```

- Real provider logos (OpenAI, Claude, Ollama…) come from @lobehub/icons in production — Avatar is the prototype stand-in.
