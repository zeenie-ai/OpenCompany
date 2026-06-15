Normal/Dev segmented toggle from the toolbar (Normal = AI components only; Dev = everything).

```jsx
const [mode, setMode] = React.useState('normal');
<ModeToggle mode={mode} onChange={setMode} />
```
