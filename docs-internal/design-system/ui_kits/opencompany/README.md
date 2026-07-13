# OpenCompany app UI kit

Interactive recreation of the OpenCompany workflow canvas (`localhost:3000`), built from the real client code (`client/src/Dashboard.tsx`, `ui/TopToolbar.tsx`, `ui/ComponentPalette.tsx`, `ui/WorkflowSidebar.tsx`, `ui/StatusBar.tsx`, `SquareNode.tsx`).

**What's interactive:** toggle sidebar/palette panels, Normal↔Dev mode (filters palette sections), search the palette, click palette items to add nodes, select canvas nodes, Start/Stop (runs an executing-pulse sequence through the flow), Save (Modified→Saved), light/dark theme toggle, console dock tabs + collapse.

**Files**
- `index.html` — entry; loads the DS bundle + lucide and mounts the app.
- `Toolbar.jsx` — 48px top toolbar (file menu, workflow name, mode toggle, action buttons, save state).
- `Panels.jsx` — workflow sidebar (280px) + component palette (320px) with fake node catalogue.
- `CanvasView.jsx` — dot-grid canvas, dashed SVG edges, SquareNodes + rectangular AgentNode.
- `ConsoleDock.jsx` — Chat / Console / Terminal tabs.
- `App.jsx` — state wiring.

The demo workflow mirrors the official screenshot: WhatsApp Receive → AI Agent (memory + tools) → WhatsApp Send, with Simple Memory, Android Toolkit, and Web Search Tool as tool nodes.
