Canvas workflow node — the heart of the OpenCompany visual identity. Color comes from the node's role.

```jsx
<SquareNode icon="🤖" label="AI Agent" color="var(--node-agent)" status="success" showToolOutput />
<SquareNode icon="🧠" label="Simple Memory" color="var(--node-agent)" />
<SquareNode icon={<Icon name="Search" size={26} />} label="Web Search Tool" color="var(--node-tool)" executing status="executing" />
```

- Role colors: agent=purple, model=cyan, tool/skill=green, trigger=pink, workflow=orange.
- Production icons are emoji-style colorful glyphs or brand SVGs — emoji strings are faithful here.
- `executing` adds the signature pulsing glow; pip shows idle/waiting/success/error.
- **Trigger nodes** (`trigger`): no input handle, a lightning ⚡ badge, and with `status="listening"` a continuous *armed* breathing glow (slower/gentler than the execution pulse) — the node is waiting for events. WhatsApp Receive, Cron, Webhook, Start are triggers.
- **Glow visibility:** the pulse/glow uses `pulseColor` (defaults to `color`). Pass a theme-contrast accent so the glow reads on the active background (lapis on vellum, cyan on void, REC-red on grey) — never the node's own faint role tint on a matching surface.
- Place on a dot-grid canvas (`background-image: radial-gradient(...)`) and join nodes with 2px dashed accent edges.

```jsx
<SquareNode icon="💬" label="WhatsApp Receive" color="var(--node-trigger)"
  trigger status="listening" pulseColor="var(--node-pulse-color)" showInput={false} />
```
