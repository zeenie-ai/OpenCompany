Tab strip used by the multi-tab console dock (Chat / Console / Terminal) and settings panels.

```jsx
const [tab, setTab] = React.useState('console');
<Tabs tabs={[{id:'chat',label:'Chat'},{id:'console',label:'Console'},{id:'terminal',label:'Terminal'}]} active={tab} onChange={setTab} />
```
