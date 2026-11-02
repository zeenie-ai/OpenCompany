> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Tab strip used by the multi-tab console dock (Chat / Console / Terminal) and settings panels.

```jsx
const [tab, setTab] = React.useState('console');
<Tabs tabs={[{id:'chat',label:'Chat'},{id:'console',label:'Console'},{id:'terminal',label:'Terminal'}]} active={tab} onChange={setTab} />
```
