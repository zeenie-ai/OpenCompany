# Plugin and node contracts

Status: current implementation. Source baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Components and boundaries

Show authoring/discovery on the left, registry/schema contract in the middle, two clearly separated outgoing lanes: UI catalogue→adapter→editor above, handler/typed-activity execution below. The capability index is a sidecar documentation output from metadata and class declarations/flags; it is not a NodeSpec consumer or runtime service. No node equals a separately deployed service.

- Recursive imports discover plugin modules. BaseNode subclasses register class, metadata, schemas and handlers; abstract kinds do not register as executable concrete nodes.
- NodeSpec assembles the backend/editor contract from actual metadata and schemas. Current frontend adapters consume the live contract.
- Handler-free configuration/context/visual nodes can still have catalogue metadata. Registration alone does not establish executable behavior.
- Typed Temporal activities and queues derive from declared plugin classes. Workflow-shaped agent/Pi dispatch and specialized task admissions have additional contracts.
- Workspace-task eligibility, trigger behavior, credentials and ownership prerequisites must be read per class. All registered types are documented in the companion index without claiming every type supports every admission.
- Unavailable twitterReceive stays marked unavailable; retired browserHarness belongs to migration/history notes, not the working registry graph.
- This is the current NodeSpec contract. The proposed frontend widget registry is a separate status-labeled view.

## Exact labels and relationships

- `modules`: **Node plugin modules** (service).
- `base`: **BaseNode registration** (service).
- `registry`: **Metadata + class registry** (service).
- `schemas`: **Input / output schemas** (service).
- `handlers`: **Executable handler registry** (service).
- `spec`: **NodeSpec assembly** (service).
- `endpoint`: **Schema / catalogue API** (client).
- `adapter`: **Frontend NodeSpec adapter** (client).
- `editor`: **Palette / canvas / parameters** (client).
- `executor`: **NodeExecutor** (service).
- `activities`: **Typed Temporal activities** (temporal).
- `workers`: **Worker queue registration** (temporal).
- `index`: **Capability index** (state).

- Node plugin modules → BaseNode registration: recursive import discovery.
- BaseNode registration → Metadata + class registry: register class / metadata.
- BaseNode registration → Input / output schemas: register schema models.
- BaseNode registration → Executable handler registry: register handler.
- Metadata + class registry → NodeSpec assembly: metadata.
- Input / output schemas → NodeSpec assembly: schemas.
- NodeSpec assembly → Schema / catalogue API: wire contract.
- Schema / catalogue API → Frontend NodeSpec adapter: fetch specs.
- Frontend NodeSpec adapter → Palette / canvas / parameters: render / configure.
- Executable handler registry → NodeExecutor: runtime dispatch.
- Metadata + class registry → Typed Temporal activities: declared plugin classes.
- Typed Temporal activities → Worker queue registration: activity / queue contracts.
- Metadata + class registry → Capability index: all types + class flags.

## Evidence

| Source | Implementation boundary |
|---|---|
| [server/nodes/__init__.py](../../../server/nodes/__init__.py) | Recursive module discovery and registration-import behavior. |
| [server/services/plugin/base.py](../../../server/services/plugin/base.py) | BaseNode.__init_subclass__ validation and eager registry writes. |
| [server/services/node_registry.py](../../../server/services/node_registry.py) | register_node, register_node_class and metadata/schema/handler registries. |
| [server/services/node_spec.py](../../../server/services/node_spec.py) | get_node_spec assembles metadata, input/output schemas and UI hints. |
| [server/routers/schemas.py](../../../server/routers/schemas.py) | NodeSpec HTTP and catalogue API surface. |
| [client/src/lib/nodeSpec.ts](../../../client/src/lib/nodeSpec.ts) | Backend spec fetching/cache and precedence. |
| [client/src/adapters/nodeSpecToDescription.ts](../../../client/src/adapters/nodeSpecToDescription.ts) | Current NodeSpec-to-editor adaptation. |
| [server/services/temporal/plugin_activities.py](../../../server/services/temporal/plugin_activities.py) | Per-type plugin activity generation/collection. |
