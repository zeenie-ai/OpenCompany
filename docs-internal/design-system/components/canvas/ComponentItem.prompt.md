> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Component-palette row: what users drag onto the canvas.

```jsx
<ComponentItem icon="🤖" name="AI Agent" description="Native agent with memory and tools" />
<ComponentItem icon="📱" name="WhatsApp Send" description="Send a WhatsApp message" />
```

- Stack in a single-column grid with 8px gap inside a 320px palette panel.
- Description truncates to one line; keep it under ~6 words.
