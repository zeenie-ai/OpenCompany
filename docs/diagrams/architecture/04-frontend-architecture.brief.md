# Frontend architecture

Status: current implementation. Source baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Components and boundaries

Use three vertical zones: React shell/screens on the left, distinct client-state and transport adapters in the middle, backend on the right. Keep UI state and server-state Query cache separate. The WebSocketContext fans out to Query cache bridge, status stores, chat subscribers and console/terminal consumers. It does not make AppShell subscribe to every live frame. Draw shell effects as a small app-wide sync lane.

- React root mounts persisted Query state, theme, auth and WebSocket providers. AppShell owns common effects/dialogs and lazily selects Home or the Dev canvas.
- Zustand UI/graph state and TanStack Query server-state cache are distinct. The diagram does not imply all state is persisted or that decrypted credentials are persisted in browser storage.
- Transport is mixed REST, WebSocket commands/subscriptions and feature-specific polling. It is not a universal WebSocket-only architecture.
- WebSocket cache/status bridges update selected consumers. AppShell intentionally avoids subscribing to the high-frequency context value.
- useCurrentWorkflowSync owns workflow identity propagation and reconnect/switch status refresh across both screens.
- Chat delivery uses session/run subscriptions and resynchronization. Console/terminal event retention is not equivalent to the durable chat ledger.
- NodeSpec/backend catalogue data drive the current editor. The current parameter renderer remains switch-based; the widget registry is a separate proposal.

## Exact labels and relationships

- `boot`: **React root providers** (client).
- `shell`: **AppShell** (client).
- `home`: **Home employee interface** (client).
- `editor`: **Dev workflow canvas** (client).
- `uistate`: **Zustand UI / graph state** (state).
- `query`: **TanStack Query cache** (state).
- `api`: **REST request adapters** (client).
- `ws`: **WebSocketContext** (client).
- `backend`: **Backend API / WS** (service).
- `status`: **Workflow / node status stores** (state).
- `chat`: **Chat run / session subscribers** (client).
- `dock`: **Console / terminal consumers** (client).
- `sync`: **Shell synchronization effects** (client).

- React root providers → AppShell: mount.
- AppShell → Home employee interface: lazy screen.
- AppShell → Dev workflow canvas: lazy screen.
- Zustand UI / graph state → Home employee interface: UI state.
- Zustand UI / graph state → Dev workflow canvas: graph / UI state.
- TanStack Query cache → Home employee interface: server state.
- TanStack Query cache → Dev workflow canvas: catalogue / specs.
- Home employee interface → REST request adapters: REST operations.
- Dev workflow canvas → REST request adapters: REST operations.
- REST request adapters → Backend API / WS: HTTP.
- Home employee interface → WebSocketContext: commands / subscriptions.
- Dev workflow canvas → WebSocketContext: commands / subscriptions.
- WebSocketContext → Backend API / WS: WebSocket.
- WebSocketContext → TanStack Query cache: cache bridge.
- WebSocketContext → Workflow / node status stores: status updates.
- WebSocketContext → Chat run / session subscribers: chat frames / resync.
- WebSocketContext → Console / terminal consumers: live output.
- AppShell → Shell synchronization effects: own app-wide effects.
- Shell synchronization effects → Workflow / node status stores: workflow identity / reconnect sync.

## Evidence

| Source | Implementation boundary |
|---|---|
| [client/src/main.tsx](../../../client/src/main.tsx) | PersistQueryClientProvider, ThemeProvider, AuthProvider, WebSocketProvider. |
| [client/src/app/AppShell.tsx](../../../client/src/app/AppShell.tsx) | ShellEffects, ShellDialogs and AppShell ownership; avoids high-frequency WS context subscription. |
| [client/src/app/ShellModeSwitch.tsx](../../../client/src/app/ShellModeSwitch.tsx) | LazyHome/LazyEditor and shell-mode selection. |
| [client/src/app/useCurrentWorkflowSync.ts](../../../client/src/app/useCurrentWorkflowSync.ts) | Workflow identity to node status and socket-ready status resynchronization. |
| [client/src/lib/queryClient.ts](../../../client/src/lib/queryClient.ts) | Shared QueryClient and distinct catalogue/credential cache policy. |
| [client/src/lib/queryPersist.ts](../../../client/src/lib/queryPersist.ts) | Browser persistence allowlist excludes decrypted credential values. |
| [client/src/contexts/WebSocketContext.tsx](../../../client/src/contexts/WebSocketContext.tsx) | Command dispatch and cache/status/live-event consumption bridge. |
