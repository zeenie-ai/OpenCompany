> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Base surface card. Quiet shadow; personality comes from content, not the card.

```jsx
<Card>Static content</Card>
<Card interactive onClick={open}>Clickable — lifts on hover</Card>
<Card interactive selected>Selected — accent left edge</Card>
```
