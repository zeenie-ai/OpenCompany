> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Accordion card — Connected Skills, Token Usage, and other optional parameter-panel groups.

```jsx
<CollapsibleSection title="Connected Skills" defaultCollapsed>
  …rows…
</CollapsibleSection>
```

- Chevron points down when open, rotates -90° when collapsed.
- Head sits on --bg-elevated above the --bg-app body (two-tier surface).
