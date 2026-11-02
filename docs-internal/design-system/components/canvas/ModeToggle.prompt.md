> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Normal/Dev segmented toggle from the toolbar and the Home header: switches between Normal mode (Home) and Dev mode (the workflow editor). With Normal mode turned off (`VITE_NORMAL_MODE=false`) it filters the palette instead (Normal = AI components only; Dev = everything). The app's version is `client/src/components/shell/ModeToggle.tsx`.

```jsx
const [mode, setMode] = React.useState('normal');
<ModeToggle mode={mode} onChange={setMode} />
```
