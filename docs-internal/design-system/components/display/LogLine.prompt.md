> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Console/event row — the dock's Console tab is a stack of these.

```jsx
<LogLine time="12:01:33" tone="success">Deployed — listening for incoming messages</LogLine>
<LogLine time="12:04:03" tone="agent">AI Agent → delegating to Web Search Tool</LogLine>
```

- Node-event tones reuse the node role colors; "→" joins actor and action.
