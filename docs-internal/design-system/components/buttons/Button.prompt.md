> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

General button (dialogs, forms, menus). Toolbar actions should use ActionButton instead.

```jsx
<Button>New Workflow</Button>
<Button variant="outline">Cancel</Button>
<Button variant="destructive" size="sm"><Icon name="X" size={12} /> Delete</Button>
<Button variant="ghost" size="icon-sm" title="Close"><Icon name="X" size={14} /></Button>
```

- destructive is a soft red tint (10% fill), not solid red.
- Press state translates down 1px; disabled is opacity .5.
