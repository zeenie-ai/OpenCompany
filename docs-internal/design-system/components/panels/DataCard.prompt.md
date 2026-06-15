Execution-data card for parameter-panel Input/Output columns.

```jsx
<DataCard title="Item 1" badge="from WhatsApp Receive" data={{ message: "What's on my calendar?", from: '+1 555 014 2236' }} />
<DataCard title="Execution Result" tone="error" blockLabel="Error" badge="Failed · 0.4s" data={{ error: 'Rate limited' }} />
```

- 4px status left edge (success green / error red / warning amber).
- JSON block: muted label bar + mono 12px pre, max 300px scroll.
