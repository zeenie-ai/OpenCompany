# Distributed deployment

Status: current implementation. Source baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Components and boundaries

The main shared-execution topology has three detail panels. Repeated PostgreSQL, Temporal, backend API and standalone-worker boxes are **aliases of the same deployed components**, not additional services.

- Main: Browser/client → API/services inside one backend process. API/services start/manage embedded SDK workers and submit workflows to shared external Temporal. SDK workers poll/report; optional standalone workers use the same namespace/queue.
- API/services access shared PostgreSQL, approved 1Password bindings and configured workspace storage. Generic-worker credential/file prerequisites still apply even though their individual storage arrows are omitted for clarity.
- Browser ownership: the Browser owner worker runs inside its backend replica. It exclusively operates owner-local child browser processes/profile files, writes ownership/fencing metadata to shared PostgreSQL, and polls its owner-specific Temporal queue. Browser/profile resources are outside the backend process, not interchangeable generic-worker state.
- Remote chat: standalone generic workers → selected chat event relay → backend API. Both arrows point toward the backend. Only chat and selected lifecycle frames use this relay.
- Mobile: backend API → installation-owner check → owner-only mobile capability. This separate one-way authorization flow is not joined to the relay.

## Exact directed relationships

| From | To | Label |
|---|---|---|
| Browser / client | API / services | HTTP / WebSocket |
| API / services | Embedded SDK workers | start / manage |
| API / services | Shared external Temporal | submit workflows |
| Embedded SDK workers | Shared external Temporal | poll / report |
| Standalone generic workers | Shared external Temporal | same namespace / queue |
| API / services | Shared PostgreSQL | app state |
| API / services | 1Password service account | secret bindings |
| API / services | Configured workspace storage | file references |
| Browser owner worker | Owner-local browser + profile | exclusive live owner |
| Browser owner worker | Shared PostgreSQL alias | ownership / fencing |
| Browser owner worker | Shared external Temporal alias | owner queue |
| Standalone generic worker alias | Selected chat event relay | selected frames |
| Selected chat event relay | Backend replica API alias | authenticated relay |
| Backend replica API alias | Installation-owner check | authorization admission |
| Installation-owner check | Owner-only mobile capability | authorized owner |

## Prerequisites and caveats

- Distributed mode requires shared PostgreSQL, external Temporal, native-agent and per-type dispatch, unique browser replica identity/trusted origin, one backend process per replica, and 1Password service-account authentication.
- Standalone workers refuse local mode and inherit the backend's configured Temporal address, namespace and task queue. Generic workers exclude process-local Browser activities.
- Shared rows hold browser ownership, routing and fencing metadata, not live sockets/daemon state. Owner loss does not automatically transfer a profile to another machine.
- Cross-worker file access requires suitable configured storage. PostgreSQL does not contain workspace file bytes.
- Distributed credential preflight rejects inline, OAuth, CLI-managed and unsupported machine-local connections. Local integrations are not universally portable to generic workers.
- The chat relay is not a universal durable event bus. Mobile capability requires installation-owner authorization plus Android runtime/queue prerequisites.
- Single-container Docker is a separate local hosting recipe; Compose scaling alone does not establish this configuration.

## Evidence

| Source | Implementation boundary |
|---|---|
| [server/core/config.py](../../../server/core/config.py) | `Settings._validate_distributed_browser`: PostgreSQL, external Temporal, native dispatch, replica identity/origin, workers=1, 1Password. |
| [server/services/temporal/worker.py](../../../server/services/temporal/worker.py) | `run_standalone_worker` namespace/queue defaults and local-mode refusal; `_generic_plugin_activities` excludes Browser. |
| [server/services/temporal/lifecycle.py](../../../server/services/temporal/lifecycle.py) | `_start_execution_engine` starts embedded workers and the current replica's BrowserOwnerWorkerPool. |
| [server/services/browser_owners.py](../../../server/services/browser_owners.py) | `register_browser_owner`, `owner_queue`, fencing metadata and explicit recovery boundaries. |
| [server/services/credentials/preflight.py](../../../server/services/credentials/preflight.py) | `assert_cluster_credentials` rejects unsupported distributed sources and requires approved bindings. |
| [server/services/chat/relay.py](../../../server/services/chat/relay.py) | Selected chat/lifecycle relay to authenticated internal API. |
| [server/nodes/mobile/_node.py](../../../server/nodes/mobile/_node.py) | `require_mobile_owner` and Android task queue. |

## Visual review

The initial dense layout was rejected after cross-review found ambiguous ownership/relay/mobile endpoints. The selected ImageGen regeneration uses alias detail panels. Submission/polling origins, owner-local process boundary and every detail arrow were checked at native and normal documentation width. Full prompts and rejected-attempt provenance are retained.
