# Local runtime

Status: current implementation. Source baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Boundaries and relationships

- `company start` launches the backend. The production browser and optional Electron shell use the same backend origin.
- One Python backend process contains the API, WebSocket endpoints, built SPA serving, application services and embedded Temporal SDK workers. Embedded workers are not separate OS processes.
- Application services submit workflows to the backend-owned Temporal server child over gRPC and start/manage the embedded SDK workers. The workers poll Temporal and report task results. Temporal persists its own SQLite history separately from the application database.
- Application services use the application database, encrypted credential store and files beneath the data root.
- Lazy sidecars represent Bun JavaScript execution, the WhatsApp bridge and optional browser processes. They are created as required by their owning runtime, not obligatory external services.
- Development adds Vite as a frontend/proxy process. Electron launches or attaches to the same backend and loads its SPA.
- The supported Docker recipe hosts this topology in one container, persists `/data`, and publishes only the application port. It does not make every internal runtime port public. Browser and git support are optional image extras.

## Evidence

| Source | Implementation evidence |
|---|---|
| [cli/commands/start.py](../../../cli/commands/start.py) | `start` service specification launches the backend; production serving is backend-owned. |
| [cli/commands/dev.py](../../../cli/commands/dev.py) | Development additionally runs the Vite frontend. |
| [server/main.py](../../../server/main.py) | `lifespan`, background `run_temporal_lifecycle`, static SPA serving, concurrent worker shutdown. |
| [server/services/temporal/lifecycle.py](../../../server/services/temporal/lifecycle.py) | `_start_execution_engine` wires the executor, manager and specialized worker pool inside the backend. |
| [server/services/temporal/_runtime.py](../../../server/services/temporal/_runtime.py) | Backend-owned Temporal child, CLI arguments and separate SQLite history. |
| [server/core/paths.py](../../../server/core/paths.py) | Separate `workflow.db`, `credentials.db`, `temporal.db`, workspaces and packages. |
| [desktop/src/main/backend.ts](../../../desktop/src/main/backend.ts) | `backendArgv`, `spawnBackend` and readiness contract. |
| [client/vite.config.js](../../../client/vite.config.js) | Development API/WebSocket proxy. |
| [docker-compose.yml](../../../docker-compose.yml) | One service, `data:/data`, localhost application port and stop grace. |
| [docker/entrypoint.sh](../../../docker/entrypoint.sh) | Direct uvicorn entrypoint. |
| [docker/Dockerfile](../../../docker/Dockerfile) | Optional browser/git extras and headless browser configuration. |

## Accuracy constraints

This is the default local runtime, not the distributed deployment. Local application SQLite can be replaced by explicitly configured PostgreSQL. Managed teams and new Pi work require Temporal; the eligible sequential path is explained in the execution view. Redis is not a required application execution dependency. Docker is an optional host variant. Draw no separate Node.js API server, no per-node microservices, no client-to-database/Temporal connections, and no merged application/Temporal database.
