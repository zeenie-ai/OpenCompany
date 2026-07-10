"""Component-palette group metadata (Wave 10.B).

Each group referenced in any node's `group:` array gets a registration
here with its palette icon, label, color, and visibility bucket. The
frontend ComponentPalette reads the index from
`/api/schemas/nodes/groups` and renders section headers directly from
this data — no `CATEGORY_ICONS` / `labelMap` / `colorMap` /
`SIMPLE_MODE_CATEGORIES` tables on the frontend anymore.

`visibility`:
  - "normal": shown in the default simple-mode palette
  - "dev":    shown only when the user toggles pro/dev mode on
  - "all":    always shown (effectively same as "normal")
"""

from __future__ import annotations

from services.node_registry import register_group


# ---------------------------------------------------------------------------
# Normal-mode groups (visible in simple mode — the core agent building blocks)
# ---------------------------------------------------------------------------

register_group(key="agent", metadata={"label": "AI Agents", "icon": "🤖", "color": "#bd93f9", "visibility": "normal"})
register_group(key="model", metadata={"label": "AI Models", "icon": "🧬", "color": "#8be9fd", "visibility": "normal"})
register_group(key="skill", metadata={"label": "AI Skills", "icon": "🎯", "color": "#50fa7b", "visibility": "normal"})
register_group(key="tool", metadata={"label": "AI Tools", "icon": "🛠️", "color": "#50fa7b", "visibility": "normal"})


# ---------------------------------------------------------------------------
# Dev-mode groups (shown when pro/dev mode is toggled on)
# ---------------------------------------------------------------------------

register_group(key="workflow", metadata={"label": "Workflows", "icon": "⚡", "color": "#ffb86c", "visibility": "dev"})
register_group(key="trigger", metadata={"label": "Triggers", "icon": "🕐", "color": "#ff79c6", "visibility": "dev"})
register_group(key="ai", metadata={"label": "AI", "icon": "🤖", "color": "#bd93f9", "visibility": "dev"})
register_group(key="location", metadata={"label": "Location", "icon": "📍", "color": "#ff5555", "visibility": "dev"})
register_group(key="social", metadata={"label": "Social", "icon": "📱", "color": "#50fa7b", "visibility": "dev"})
register_group(key="android", metadata={"label": "Android", "icon": "📱", "color": "#8be9fd", "visibility": "dev"})
register_group(key="chat", metadata={"label": "Chat", "icon": "💭", "color": "#f1fa8c", "visibility": "dev"})
register_group(key="code", metadata={"label": "Code", "icon": "💻", "color": "#ffb86c", "visibility": "dev"})
register_group(key="document", metadata={"label": "Documents", "icon": "🗄️", "color": "#ff79c6", "visibility": "dev"})
register_group(key="utility", metadata={"label": "Utilities", "icon": "🔧", "color": "#bd93f9", "visibility": "dev"})
register_group(key="api", metadata={"label": "API & Scraping", "icon": "🕷️", "color": "#ffb86c", "visibility": "dev"})
register_group(key="search", metadata={"label": "Search", "icon": "🔍", "color": "#8be9fd", "visibility": "dev"})
register_group(key="google", metadata={"label": "Google Workspace", "icon": "asset:google", "color": "#4285F4", "visibility": "dev"})
register_group(key="scheduler", metadata={"label": "Schedulers", "icon": "📅", "color": "#ff79c6", "visibility": "dev"})
register_group(key="proxy", metadata={"label": "Proxy", "icon": "🛡", "color": "#bd93f9", "visibility": "dev"})
register_group(key="whatsapp", metadata={"label": "WhatsApp", "icon": "💬", "color": "#25D366", "visibility": "dev"})
register_group(key="email", metadata={"label": "Email", "icon": "✉️", "color": "#8be9fd", "visibility": "dev"})
register_group(key="payments", metadata={"label": "Payments", "icon": "asset:stripe", "color": "#635BFF", "visibility": "dev"})
register_group(key="deployment", metadata={"label": "Deployment", "icon": "lobehub:Vercel", "color": "#666666", "visibility": "dev"})
register_group(key="vcs", metadata={"label": "Version Control", "icon": "lobehub:Github", "color": "#F05133", "visibility": "dev"})
register_group(key="browser", metadata={"label": "Browser", "icon": "🌐", "color": "#ff79c6", "visibility": "dev"})
register_group(key="scraper", metadata={"label": "Scrapers", "icon": "🕸", "color": "#ff79c6", "visibility": "dev"})
register_group(key="filesystem", metadata={"label": "Filesystem", "icon": "📁", "color": "#8be9fd", "visibility": "dev"})
register_group(key="service", metadata={"label": "Services", "icon": "⚙️", "color": "#50fa7b", "visibility": "dev"})
register_group(key="text", metadata={"label": "Text", "icon": "📝", "color": "#bd93f9", "visibility": "dev"})
register_group(key="memory", metadata={"label": "Memory", "icon": "💾", "color": "#f1fa8c", "visibility": "dev"})
