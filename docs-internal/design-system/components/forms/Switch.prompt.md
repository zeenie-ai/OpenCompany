> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Settings toggle (sound effects, theme options, node flags).

```jsx
<label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
  <Switch defaultChecked onChange={(on) => console.log(on)} />
  <span style={{ fontSize: 14 }}>Sound effects</span>
</label>
```
