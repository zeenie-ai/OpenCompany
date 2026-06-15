Notification card — deploy results, validation errors, agent events.

```jsx
<Toast tone="success" title="Workflow deployed" message="Listening for incoming messages" time="12:01:33" onClose={dismiss} />
<Toast tone="error" title="API key invalid" message="Check your Anthropic credentials" />
```

- Titles are terse outcomes ("Workflow deployed", "Saved"); message adds one detail line.
- Stack bottom-right with 8px gap.
