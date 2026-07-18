# New Service Integration Guide

> **⚠️ HISTORICAL — do not follow these steps.** This guide predates
> Wave 11 and is preserved only as background for reading old code.
>
> **Current path for a new service integration:**
> - Node authoring recipe + decision tree → [node_creation.md](./node_creation.md)
> - Full plugin reference (credentials, `Connection` facade, six `register_*` hooks, Wave 12 event framework) → [plugin_system.md](./plugin_system.md)
> - Live OAuth reference implementation → [server/nodes/google/](../server/nodes/google/) (`_oauth.py`, `_router.py`, `_option_loaders.py`, `_auth_helper.py`)
> - CLI-managed-auth variant (Stripe / Vercel / GitHub shape) → [node_creation.md → CLI-managed auth](./node_creation.md) + [stripe_service.md](./stripe_service.md)

> **⚠️ Pre-Wave-11 — historical reference only.**
> Node authoring now happens on the backend: each node is a Python plugin in a self-contained folder under `server/nodes/<category>/<plugin>/__init__.py` that emits a `NodeSpec`. The frontend reads specs via [client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts) + [adapters/nodeSpecToDescription.ts](../client/src/adapters/nodeSpecToDescription.ts). See [plugin_system.md](./plugin_system.md) and [server/nodes/README.md](../server/nodes/README.md) for the current model. The snippets below that reference `client/src/nodeDefinitions/*` are kept for historical context.
>
> **Path-correction note (post-extraction).** Several code locations named below moved when each plugin became a self-contained folder. When following this guide, substitute:
> - `server/services/{service}_oauth.py` → `server/nodes/<plugin>/_oauth.py` (e.g. [server/nodes/google/_oauth.py](../server/nodes/google/_oauth.py), [server/nodes/twitter/_oauth.py](../server/nodes/twitter/_oauth.py))
> - `server/services/handlers/{service}.py` → the plugin's own folder (Google's handler bodies are inlined into the six service plugins under [server/nodes/google/](../server/nodes/google/) plus shared helpers `_base.py` / `_gmail.py` / `_auth_helper.py`)
> - `server/services/node_option_loaders/` → per-plugin `_option_loaders.py` registered via `register_option_loader(...)` (e.g. [server/nodes/google/_option_loaders.py](../server/nodes/google/_option_loaders.py), [server/nodes/android/_option_loaders.py](../server/nodes/android/_option_loaders.py), [server/nodes/whatsapp/_option_loaders.py](../server/nodes/whatsapp/_option_loaders.py))
> - `server/routers/{service}.py` → the plugin's `_router.py`, mounted via `register_router(...)` (e.g. [server/nodes/google/_router.py](../server/nodes/google/_router.py))
> - Handler registration in `server/services/node_executor.py` → automatic via `BaseNode.__init_subclass__`; no manual registry entry.
> `server/services/handlers/` now contains only cross-cutting orchestration — `tools.py`, `triggers.py`, `todo.py`.

This guide provides a comprehensive walkthrough for integrating new external services (like Google Workspace, Slack, Notion, etc.) into OpenCompany. It covers OAuth authentication, database models, API handlers, frontend nodes, and AI Agent tool integration.

> **Backend-first is canonical (post-Wave-6 / Wave-11).** Parameter schemas, validation, conditional visibility, dynamic-option dispatch, icons, and colors all live on the backend. The legacy frontend `nodeDefinitions/*.ts` files no longer exist. Follow the [canonical plugin recipe](./node_creation.md) — every node is one Python file (or self-contained folder) under `server/nodes/<group>/<plugin>/`. The frontend reads `NodeSpec` via [`client/src/lib/nodeSpec.ts`](../client/src/lib/nodeSpec.ts) and adapts via [`adapters/nodeSpecToDescription.ts`](../client/src/adapters/nodeSpecToDescription.ts). Credential icons are also backend-served via `GET /api/schemas/credentials/{provider}/icon` (F7); plugin icons via `GET /api/schemas/nodes/{type}/icon` (Phase 6 / RFC §6.5).

## Overview

A complete service integration includes:

1. **OAuth Service** - Handle authentication with the external provider
2. **Database Models** - Store OAuth tokens and connection state
3. **API Handlers** - Execute service operations (CRUD)
4. **Pydantic input model + NODE_METADATA entry** - The Wave 6 recipe replaces the bulk of the legacy frontend node definition work. See [node_creation.md Wave 6 section](./node_creation.md#wave-6-recommended-recipe-backend-first).
5. **Dynamic-option loaders** - If your service has dropdown fields needing live data (label list, channel list, calendar list), add a loader to your plugin folder's `_option_loaders.py` and register it from the plugin's `__init__.py` via `register_option_loader(method_name, fn)` (from [server/services/ws_handler_registry.py](../server/services/ws_handler_registry.py)). One-line registration; see [server/nodes/google/_option_loaders.py](../server/nodes/google/_option_loaders.py) as the reference (`gmailLabels`, `googleCalendarList`, `googleDriveFolders`, `googleTasklists`).
6. **Frontend visual-component routing** - Add to the appropriate `*_NODE_TYPES` list or routing branch in [client/src/Dashboard.tsx](../client/src/Dashboard.tsx) so React Flow knows which component to render.
7. **AI Tool Schemas** - Enable LLM tool calling
8. **Credentials Modal** - UI for managing connections
9. **Pricing Configuration** - Cost tracking for API usage

The sections below detail each step using Google Workspace as the reference integration. Sections 1-3 + 7-9 are unchanged in Wave 6; section 4-6 are where the backend-first recipe materially cuts the work.

## Architecture Pattern

> **Historical diagram.** The "Node Definitions (calendarNodes)" frontend box below no longer exists — node schemas are backend NodeSpec (rendered via `useNodeSpec`), and the credentials modal renders from `server/config/credential_providers.json`.

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend                                 │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ CredentialsModal│  │ Node Definitions │                   │
│  │ (OAuth UI)      │  │ (calendarNodes) │                   │
│  └────────┬────────┘  └────────┬────────┘                   │
│           │                    │                             │
└───────────│────────────────────│─────────────────────────────┘
            │ WebSocket          │ WebSocket
            ▼                    ▼
┌───────────────────────────────────────────────────────────────┐
│                     Backend                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ OAuth Router │  │ Node Executor│  │ WebSocket Handlers   │ │
│  │ (google.py)  │  │ (registry)   │  │ (oauth_login/status) │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘ │
│         │                 │                      │             │
│         ▼                 ▼                      │             │
│  ┌──────────────┐  ┌──────────────┐             │             │
│  │ OAuth Service│  │ Handlers     │             │             │
│  │ (google_oauth│  │ (calendar.py)│◄────────────┘             │
│  └──────┬───────┘  └──────┬───────┘                           │
│         │                 │                                    │
│         ▼                 ▼                                    │
│  ┌──────────────────────────────────┐                         │
│  │ Database (SQLModel)              │                         │
│  │ - GoogleConnection               │                         │
│  │ - Token storage via auth_service │                         │
│  └──────────────────────────────────┘                         │
└───────────────────────────────────────────────────────────────┘
            │
            ▼
┌───────────────────────────────────────────────────────────────┐
│                  External Service API                          │
│  (Google Calendar API, Slack API, Notion API, etc.)           │
└───────────────────────────────────────────────────────────────┘
```

---

## Step 1: OAuth Service

Create `server/nodes/{service}/_oauth.py` (post-extraction location; the pre-Wave-11 path was `server/services/{service}_oauth.py`):

```python
"""
{Service} OAuth 2.0 using appropriate library.

Two access modes:
1. Owner Mode - Your own account (Credentials Modal)
2. Customer Mode - Customer's account (database storage)
"""

import time
from typing import Any, Dict, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)

# Define scopes for all service capabilities
SERVICE_SCOPES = [
    "scope1",
    "scope2",
    # Add all scopes needed for your service
]

# In-memory state store (use Redis in production)
_oauth_states: Dict[str, Dict[str, Any]] = {}


class ServiceOAuth:
    """OAuth 2.0 handler for {Service}."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "http://localhost:3010/api/{service}/callback",
        scopes: Optional[List[str]] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes or SERVICE_SCOPES

    def generate_authorization_url(
        self,
        state_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Generate OAuth authorization URL."""
        # Generate unique state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Store state data for callback verification
        _oauth_states[state] = {
            "created_at": time.time(),
            "data": state_data or {"mode": "owner"},
        }

        # Build authorization URL
        auth_url = f"https://provider.com/oauth/authorize?..."

        return {"url": auth_url, "state": state}

    def exchange_code(self, code: str, state: str) -> Dict[str, Any]:
        """Exchange authorization code for tokens."""
        # Verify state
        oauth_state = _oauth_states.pop(state, None)
        if not oauth_state:
            return {"success": False, "error": "Invalid or expired state"}

        try:
            # Exchange code for tokens
            # Get user info

            return {
                "success": True,
                "access_token": "...",
                "refresh_token": "...",
                "email": "user@example.com",
                "name": "User Name",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def refresh_credentials(
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> Dict[str, Any]:
        """Refresh expired credentials."""
        try:
            # Refresh token
            return {"success": True, "access_token": "..."}
        except Exception as e:
            return {"success": False, "error": str(e)}


def get_pending_state(state: str) -> Optional[Dict[str, Any]]:
    """Get pending state without removing it."""
    return _oauth_states.get(state)
```

**Key Points:**
- Use in-memory state store for CSRF protection
- Support both owner mode (single account) and customer mode (multi-tenant)
- Implement token refresh for long-running connections
- Log operations at appropriate levels

---

## Step 2: Database Models

### 2.1 Connection Model (`server/models/database.py`)

```python
class ServiceConnection(SQLModel, table=True):
    """OAuth connections for customer access mode."""

    __tablename__ = "service_connections"

    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: str = Field(index=True, max_length=255)
    email: str = Field(max_length=255)
    name: Optional[str] = Field(default=None, max_length=255)
    access_token: str = Field(max_length=2000)
    refresh_token: str = Field(max_length=2000)
    token_expiry: Optional[datetime] = Field(default=None)
    scopes: str = Field(max_length=1000)
    is_active: bool = Field(default=True)
    last_used_at: Optional[datetime] = Field(default=None)
    connected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )
```

### 2.2 Database CRUD Methods (`server/core/database.py`)

```python
# Add import
from models.database import ServiceConnection

# Add CRUD methods
async def save_service_connection(
    self,
    customer_id: str,
    email: str,
    access_token: str,
    refresh_token: str,
    scopes: str,
    name: Optional[str] = None,
) -> bool:
    """Save or update a service connection for a customer."""
    # Implementation...

async def get_service_connection(self, customer_id: str) -> Optional[ServiceConnection]:
    """Get service connection for a customer."""
    # Implementation...

async def delete_service_connection(self, customer_id: str) -> bool:
    """Delete service connection for a customer."""
    # Implementation...

async def update_service_last_used(self, customer_id: str) -> bool:
    """Update last_used_at timestamp."""
    # Implementation...
```

### 2.3 Database Migration

Add migration logic in `_migrate_user_settings()`:

```python
# In server/core/database.py - _migrate_user_settings()

# Check if old table exists and migrate
result = await conn.execute(text(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='old_table_name'"
))
old_exists = result.fetchone() is not None

if old_exists:
    await conn.execute(text("ALTER TABLE old_table_name RENAME TO new_table_name"))
    logger.info("Migrated old_table_name to new_table_name")
```

---

## Step 3: API Router

Create `server/nodes/{service}/_router.py` and register it from the plugin's `__init__.py` via `register_router(router, name="{service}")` (post-extraction location; the pre-Wave-11 path was `server/routers/{service}.py`):

```python
"""
{Service} OAuth 2.0 callback and API routes.
"""

from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from core.container import container
from core.logging import get_logger
from services.{service}_oauth import ServiceOAuth, get_pending_state

logger = get_logger(__name__)
router = APIRouter(prefix="/api/{service}", tags=["{service}"])


@router.get("/callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """Handle OAuth callback from provider."""
    # Handle errors
    if error:
        return HTMLResponse(content=_callback_html(success=False, error=error_description or error))

    # Verify state
    pending_state = get_pending_state(state)
    if not pending_state:
        return HTMLResponse(content=_callback_html(success=False, error="Invalid state"))

    # Exchange code for tokens
    auth_service = container.auth_service()
    client_id = await auth_service.get_api_key("{service}_client_id")
    client_secret = await auth_service.get_api_key("{service}_client_secret")

    oauth = ServiceOAuth(client_id=client_id, client_secret=client_secret)
    result = oauth.exchange_code(code=code, state=state)

    if not result.get("success"):
        return HTMLResponse(content=_callback_html(success=False, error=result.get("error")))

    # Store tokens
    mode = pending_state.get("data", {}).get("mode", "owner")
    if mode == "customer":
        database = container.database()
        await database.save_service_connection(...)
    else:
        await auth_service.store_api_key(provider="{service}_access_token", api_key=result["access_token"], models=[], session_id="default")
        if result.get("refresh_token"):
            await auth_service.store_api_key(provider="{service}_refresh_token", api_key=result["refresh_token"], models=[], session_id="default")

    # Broadcast completion
    from services.status_broadcaster import get_status_broadcaster
    broadcaster = get_status_broadcaster()
    await broadcaster.broadcast({
        "type": "{service}_oauth_complete",
        "data": {"success": True, "email": result["email"]},
    })

    return HTMLResponse(content=_callback_html(success=True, email=result["email"]))


@router.get("/status")
async def get_status():
    """Get connection status."""
    auth_service = container.auth_service()
    access_token = await auth_service.get_api_key("{service}_access_token")

    if not access_token:
        return {"connected": False}

    return {"connected": True, "email": "..."}


@router.post("/logout")
async def logout():
    """Disconnect service."""
    auth_service = container.auth_service()
    await auth_service.remove_api_key("{service}_access_token")
    await auth_service.remove_api_key("{service}_refresh_token")
    return {"success": True}


def _callback_html(success: bool, email: str = None, error: str = None) -> str:
    """Generate OAuth callback HTML page."""
    # Return success/error HTML with auto-close script
    ...
```

### Register Router

**No `main.py` edit needed (post-extraction).** Register the router from the plugin's `__init__.py`; `main.py` mounts every plugin-registered router via its plugin-router loop:

```python
# server/nodes/{service}/__init__.py
from services.ws_handler_registry import register_router
from . import _router

register_router(_router.router, name="{service}")
```

For OAuth callback paths also call `register_oauth_callback_path("{service}", "/api/{service}/callback")` (see [server/nodes/google/__init__.py](../server/nodes/google/__init__.py) for the full self-registration set).

---

## Step 4: WebSocket Handlers

Add handlers in your plugin folder's `_handlers.py` and register them from `__init__.py` via `register_ws_handlers(WS_HANDLERS)` (post-extraction; the pre-Wave-11 path was `server/routers/websocket.py`). See [server/nodes/google/_handlers.py](../server/nodes/google/_handlers.py) for the `google_oauth_login` / `google_oauth_status` / `google_logout` reference:

```python
async def handle_{service}_oauth_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Initiate OAuth flow."""
    import webbrowser
    from ._oauth import ServiceOAuth

    auth_service = container.auth_service()
    client_id = await auth_service.get_api_key("{service}_client_id")
    client_secret = await auth_service.get_api_key("{service}_client_secret")

    if not client_id or not client_secret:
        return {"success": False, "error": "Service not configured"}

    oauth = ServiceOAuth(client_id=client_id, client_secret=client_secret)
    auth_data = oauth.generate_authorization_url()
    webbrowser.open(auth_data["url"])

    return {"success": True, "state": auth_data["state"]}


@ws_handler()
async def handle_{service}_oauth_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Check connection status."""
    auth_service = container.auth_service()
    access_token = await auth_service.get_api_key("{service}_access_token")

    if not access_token:
        return {"connected": False}

    return {"connected": True, "email": "..."}


# Plugin-owned dict, registered from __init__.py
WS_HANDLERS = {
    "{service}_oauth_login": handle_{service}_oauth_login,
    "{service}_oauth_status": handle_{service}_oauth_status,
}
```

```python
# server/nodes/{service}/__init__.py
from services.ws_handler_registry import register_ws_handlers
from ._handlers import WS_HANDLERS

register_ws_handlers(WS_HANDLERS)
```

---

## Step 5: Service Handlers

**Post-Wave-11 the operation logic is inlined into the plugin class** as `@Operation`-decorated methods on the `ActionNode` / `ToolNode` subclass in `server/nodes/{service}/{plugin}/__init__.py`; shared helpers live alongside as `_base.py` / `_<service>.py`. The standalone `server/services/handlers/{service}.py` file no longer exists (Google's six operations, for example, are inlined into the six service plugins under [server/nodes/google/](../server/nodes/google/) with shared helpers `_base.py` / `_gmail.py` / `_auth_helper.py`). The template below shows the pre-Wave-11 handler shape for historical reference:

```python
"""
{Service} node handlers.

Provides handlers for all {service} operations:
- Operation 1
- Operation 2
- etc.
"""

import asyncio
from typing import Any, Dict

from core.logging import get_logger

logger = get_logger(__name__)


async def _get_service(parameters: Dict[str, Any], context: Dict[str, Any]):
    """Get authenticated service client."""
    from core.container import container

    account_mode = parameters.get('account_mode', 'owner')

    if account_mode == 'customer':
        customer_id = parameters.get('customer_id')
        db = container.database()
        connection = await db.get_service_connection(customer_id)
        if not connection:
            raise ValueError(f"No connection for customer: {customer_id}")
        access_token = connection.access_token
        await db.update_service_last_used(customer_id)
    else:
        auth_service = container.auth_service()
        access_token = await auth_service.get_api_key("{service}_access_token")
        if not access_token:
            raise ValueError("Service not connected. Please authenticate via Credentials.")

    # Build and return service client
    return build_client(access_token)


async def handle_{operation1}(
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle {operation1}."""
    try:
        service = await _get_service(parameters, context)

        # Execute operation
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: service.operation1().execute()
        )

        return {
            "success": True,
            "result": {...},
            "node_id": node_id,
        }
    except Exception as e:
        logger.error(f"{operation1} failed: {e}")
        return {"success": False, "error": str(e), "node_id": node_id}


# Add more handlers for each operation...
```

### Register Handlers

**No `node_executor.py` edit needed (post-Wave-11).** The plugin class auto-registers its handler via `BaseNode.__init_subclass__` on import — dispatch flows through `BaseNode.execute()`. The pre-Wave-11 manual registry entry in `server/services/node_executor.py::_build_handler_registry()` is gone.

---

## Step 6: Frontend Node Definitions

**No frontend changes required.** Post-Wave-11 nodes are declared as Python plugins (Step 5); the frontend fetches NodeSpec via GET /api/schemas/nodes/{type}/spec.json and renders without per-service TypeScript. The Pydantic Params class doubles as the LLM-visible tool schema; icon goes in <plugin>/icon.svg; color in <plugin>/meta.json. See [node_creation.md](./node_creation.md) and [server/nodes/README.md](../server/nodes/README.md) for the canonical recipe.

---


## Step 7: AI Tool Schemas

> **Historical — superseded.** Dual-purpose nodes no longer add schemas to `_get_tool_schema()`: the plugin's Pydantic `Params` class IS the LLM-visible tool schema (`usable_as_tool = True`), and `services/handlers/{service}.py` dispatcher modules no longer exist (logic lives in the plugin's `@Operation` methods). `_get_tool_schema` survives only for delegation schemas. Do not follow this step.

Add Pydantic schemas in `server/services/ai.py`:

```python
def _get_tool_schema(self, node_type: str, parameters: Dict[str, Any] = None):
    """Get Pydantic schema for tool node."""

    # ... existing schemas

    # {Service} schemas
    elif node_type == '{operation1}Node':
        class Operation1Schema(BaseModel):
            param1: str = Field(description="Description of param1")
            param2: Optional[str] = Field(default=None, description="Optional param2")
        return Operation1Schema
```

### Add Tool Dispatchers

In `server/services/handlers/tools.py`:

```python
async def execute_tool(...):
    # ... existing dispatchers

    elif node_type == '{operation1}Node':
        return await _execute_{operation1}(args, node_params)


async def _execute_{operation1}(args: Dict[str, Any], node_params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute {operation1} via service handler."""
    from services.handlers.{service} import handle_{operation1}

    parameters = {**node_params, **args}
    return await handle_{operation1}(
        node_id="tool_{operation1}",
        node_type="{operation1}Node",
        parameters=parameters,
        context={}
    )
```

---

## Step 8: Output Schemas (backend)

Output shapes live on the backend — the editor fetches them lazy (see [schema_source_of_truth_rfc.md](./schema_source_of_truth_rfc.md)). Add one Pydantic model per node type to [server/services/node_output_schemas.py](../server/services/node_output_schemas.py) and register it:

```python
# server/services/node_output_schemas.py

class YourServiceOutput(_OutputBase):
    result_id: Optional[str] = None
    field1: Optional[str] = None
    field2: Optional[list] = None
    timestamp: Optional[str] = None

# In NODE_OUTPUT_SCHEMAS:
NODE_OUTPUT_SCHEMAS["yourServiceOperationNode"] = YourServiceOutput
```

InputSection consumes these automatically via `GET /api/schemas/nodes/{node_type}.json`. No frontend change needed. Shape shared across operations? Assign the same model to multiple keys in `NODE_OUTPUT_SCHEMAS` (see how `_SEARCH_TYPES` aliases `SearchOutput`).

---

## Step 9: Credentials Modal

**No React changes required.** Post-Wave-7 the Credentials Modal renders entirely from `server/config/credential_providers.json` via the catalogue → `useCatalogueQuery` → `ApiKeyPanel` / `OAuthConnect` chain. To add a new provider:

1. Add an entry to `server/config/credential_providers.json` with `id`, `name`, `category`, `fields[]` (validation/connect targets + optional secondary fields like Telegram's owner-chat-id), `icon_ref` (URL form `/api/schemas/credentials/<id>/icon`), and `panel_type` (`api_key` / `oauth` / `qr_pairing` / `email_smtp`).
2. Drop the brand SVG at `server/credentials/icons/<id>.svg` (served by `GET /api/schemas/credentials/<id>/icon`).
3. Implement the `Credential` subclass in your plugin folder's `_credentials.py` (validation, `_probe(api_key)` for API keys, or OAuth callback router for OAuth flows).

See [credentials_encryption.md](./credentials_encryption.md) for the catalogue contract and `Credential` base class details.

---

## Step 10: Pricing Configuration

Add to `server/config/pricing.json`:

```json
{
  "api": {
    "{service}": {
      "_description": "{Service} API pricing",
      "_source": "https://provider.com/pricing",
      "operation1": 0.001,
      "operation2": 0.002
    }
  },
  "operation_map": {
    "{service}": {
      "create": "operation1",
      "list": "operation2"
    }
  }
}
```

### Add Usage Tracking

```python
# In handler
async def _track_{service}_usage(node_id: str, operation: str, count: int, ...):
    """Track API usage for cost calculation."""
    from services.pricing import get_pricing_service

    pricing = get_pricing_service()
    cost = pricing.calculate_api_cost("{service}", operation, count)

    # Store to database
    ...
```

---

## Step 11: WebSocket Context Updates

Update `client/src/contexts/WebSocketContext.tsx`:

```typescript
// Add state
const [{service}Status, set{Service}Status] = useState<ServiceStatus>({
  connected: false,
  email: null,
});

// Add message handler
case '{service}_oauth_complete':
  if (data?.success) {
    set{Service}Status({
      connected: true,
      email: data.email,
    });
  }
  break;

// Export in context value
{service}Status,
```

---

## Example: Google Workspace Integration

The Google Workspace integration demonstrates this pattern with:

**Note:** post-extraction, all Google Workspace code lives inside the self-contained plugin folder [server/nodes/google/](../server/nodes/google/). The former `server/services/google_oauth.py`, `server/services/handlers/{calendar,drive,gmail,sheets,tasks,contacts}.py`, and `server/services/handlers/google_auth.py` files are gone — their bodies moved into the plugin folder (OAuth client → `_oauth.py`, shared credential builder → `_auth_helper.py`, per-service operations inlined into the service plugins). The pre-Wave-11 `client/src/nodeDefinitions/*.ts` files no longer exist at all.

### Files (current layout)

| File | Purpose |
|------|---------|
| `server/nodes/google/__init__.py` | Self-registration: WS handlers, router, OAuth callback path, service-refresh, filter builder, 4 option loaders, canary trigger |
| `server/nodes/google/_oauth.py` | OAuth 2.0 client with google-auth-oauthlib (was `services/google_oauth.py`) |
| `server/nodes/google/_auth_helper.py` | Shared credential builder + proactive refresh (was `services/handlers/google_auth.py`) |
| `server/nodes/google/_credentials.py` | `GoogleCredential` (OAuth2) — referenced by all 6 service plugins |
| `server/nodes/google/_handlers.py` | WS handlers: `google_oauth_login` / `google_oauth_status` / `google_logout` |
| `server/nodes/google/_router.py` | HTTP OAuth callback (`/api/google/callback`) |
| `server/nodes/google/_option_loaders.py` | `gmailLabels` / `googleCalendarList` / `googleDriveFolders` / `googleTasklists` loaders |
| `server/nodes/google/_base.py`, `_gmail.py` | Shared helpers (`build_google_service`, `run_sync`, `track_google_usage`, Gmail-specific) |
| `server/nodes/google/{gmail,calendar,drive,sheets,tasks,contacts}/__init__.py` | 6 service plugins (operation logic inlined) |
| `server/nodes/google/gmail_receive/__init__.py` | Polling trigger plugin |
| `server/config/google_apis.json` | API endpoints and scopes |

### Files Modified (cross-cutting)

| File | Changes |
|------|---------|
| `server/models/database.py` | Added `GoogleConnection` model |
| `server/core/database.py` | Added CRUD methods, migration logic |
| `server/services/ai.py` | Added Pydantic schemas for AI tools (dual-purpose nodes) |
| `server/services/handlers/tools.py` | Added tool dispatchers |
| `server/services/node_output_schemas.py` | Added per-operation output schemas |
| `server/config/pricing.json` | Added Google API pricing |

No frontend edits: the node definitions, credentials panel, output schemas, and Dashboard component routing are all now driven by the backend `NodeSpec` + `credential_providers.json` catalogue.

### Combined OAuth Scopes

```python
GOOGLE_WORKSPACE_SCOPES = [
    # User Info
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    # Gmail
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    # Calendar
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    # Drive
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    # Sheets
    "https://www.googleapis.com/auth/spreadsheets",
    # Tasks
    "https://www.googleapis.com/auth/tasks",
    # Contacts
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/contacts.readonly",
]
```

---

## Checklist

Use this checklist when adding a new service:

- [ ] **OAuth Service** (`server/nodes/{service}/_oauth.py`)
  - [ ] Define scopes
  - [ ] Implement `generate_authorization_url()`
  - [ ] Implement `exchange_code()`
  - [ ] Implement `refresh_credentials()`
  - [ ] Add `get_pending_state()` helper

- [ ] **Database** (`server/models/database.py`, `server/core/database.py`)
  - [ ] Add `ServiceConnection` model
  - [ ] Add import to `core/database.py`
  - [ ] Add CRUD methods (save, get, delete, update_last_used)
  - [ ] Add migration for table rename if needed

- [ ] **API Router** (`server/nodes/{service}/_router.py`)
  - [ ] Add callback endpoint
  - [ ] Add status endpoint
  - [ ] Add logout endpoint
  - [ ] Add customer endpoints if needed
  - [ ] Register from `__init__.py` via `register_router(...)` + `register_oauth_callback_path(...)`

- [ ] **WebSocket Handlers** (`server/nodes/{service}/_handlers.py`)
  - [ ] Add `handle_{service}_oauth_login`
  - [ ] Add `handle_{service}_oauth_status`
  - [ ] Collect into a `WS_HANDLERS` dict, register from `__init__.py` via `register_ws_handlers(...)`

- [ ] **Operation Logic** (plugin classes in `server/nodes/{service}/{plugin}/__init__.py`)
  - [ ] Add `@Operation`-decorated methods for each operation
  - [ ] Shared helpers in `_base.py` / `_<service>.py`, credential builder in `_auth_helper.py`
  - [ ] Auto-registers via `BaseNode.__init_subclass__` — no `node_executor.py` edit

- [ ] **Frontend Nodes** — none. The `NodeSpec` emitted by the plugin drives the palette, canvas component, and parameter panel automatically (see [node_creation.md](./node_creation.md)).

- [ ] **AI Tool Integration** — *(historical; superseded)* set `usable_as_tool = True` on the plugin class; its Pydantic `Params` IS the LLM tool schema. No `_get_tool_schema()` entry, no `execute_tool()` dispatcher, no handler functions.

- [ ] **Output Schemas** (`server/services/node_output_schemas.py`)
  - [ ] Add a Pydantic model per node type, register in `NODE_OUTPUT_SCHEMAS`

- [ ] **Credentials Modal** — no React edits. Add an entry to `server/config/credential_providers.json`, drop the brand SVG at `server/credentials/icons/<id>.svg`, and implement the `Credential` subclass in the plugin's `_credentials.py` (see Step 9).

- [ ] **WebSocket Context** (`client/src/contexts/WebSocketContext.tsx`)
  - [ ] Add OAuth complete handler
  - [ ] Add state if needed

- [ ] **Pricing** (`server/config/pricing.json`)
  - [ ] Add service pricing
  - [ ] Add operation map
  - [ ] Add URL patterns if using tracked HTTP

- [ ] **Skills** (`server/skills/{agent_type}/`)
  - [ ] Create skill folder (e.g., `productivity_agent/`)
  - [ ] Add SKILL.md for each service capability
  - [ ] Define allowed-tools in frontmatter
  - [ ] Update GUIDE.md folder structure

---

## Best Practices

1. **Use Existing Patterns** - Follow the Google Workspace implementation as a template
2. **Unified OAuth** - If a provider has multiple services, use combined scopes
3. **Dual-Purpose Nodes** - Add `group: ['{service}', 'tool']` for AI Agent integration
4. **Error Handling** - Always catch and log errors, return proper error responses
5. **Token Refresh** - Implement automatic token refresh for long-running workflows
6. **Cost Tracking** - Add pricing config and track usage for billing
7. **Documentation** - Update CLAUDE.md with the new service section
8. **Skills** - Create skills for AI agents to use service operations with proper instructions

---

## Step 12: AI Agent Skills (Optional but Recommended)

Create skills for AI agents to use the service operations.

### Skill Folder Structure

```
server/skills/{agent_type}/
├── {service}-skill/
│   └── SKILL.md
```

For Google Workspace, the skills are in `server/skills/productivity_agent/`:
- `gmail-skill/SKILL.md`
- `calendar-skill/SKILL.md`
- `drive-skill/SKILL.md`
- `sheets-skill/SKILL.md`
- `tasks-skill/SKILL.md`
- `contacts-skill/SKILL.md`

### Skill File Format

```markdown
---
name: {service}-skill
description: Brief description of capabilities
allowed-tools: tool1 tool2 tool3
metadata:
  author: opencompany
  version: "1.0"
  category: productivity
  icon: "icon"
  color: "#HEXCOLOR"
---

# {Service} Skill

Description of what the skill provides.

## Available Tools

### tool_name

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| param1 | string | Yes | Description |

**Example:**
```json
{
  "param1": "value"
}
```

## Setup Requirements

1. Connect nodes to AI Agent's `input-tools` handle
2. Authenticate in Credentials Modal
```

See `server/skills/GUIDE.md` for full documentation on skill creation.
