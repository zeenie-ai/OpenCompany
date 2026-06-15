Settings toggle (sound effects, theme options, node flags).

```jsx
<label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
  <Switch defaultChecked onChange={(on) => console.log(on)} />
  <span style={{ fontSize: 14 }}>Sound effects</span>
</label>
```
