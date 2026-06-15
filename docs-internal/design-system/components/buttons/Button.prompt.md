General button (dialogs, forms, menus). Toolbar actions should use ActionButton instead.

```jsx
<Button>New Workflow</Button>
<Button variant="outline">Cancel</Button>
<Button variant="destructive" size="sm"><Icon name="X" size={12} /> Delete</Button>
<Button variant="ghost" size="icon-sm" title="Close"><Icon name="X" size={14} /></Button>
```

- destructive is a soft red tint (10% fill), not solid red.
- Press state translates down 1px; disabled is opacity .5.
