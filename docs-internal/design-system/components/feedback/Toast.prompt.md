> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Notification card — deploy results, validation errors, agent events.

```jsx
<Toast tone="success" title="Workflow deployed" message="Listening for incoming messages" time="12:01:33" onClose={dismiss} />
<Toast tone="error" title="API key invalid" message="Check your Anthropic credentials" />
```

- Titles are terse outcomes ("Workflow deployed", "Saved"); message adds one detail line.
- Stack bottom-right with 8px gap.
