# System context

Status: current implementation. Source baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Components and boundaries

Place people/clients on the left; one central OpenCompany boundary; external model/tool/API capabilities on the right. Gateway is a visibly OPTIONAL branch to model providers, alongside direct native provider calls. Browser/mobile runtime is an owned capability, not an interchangeable external cloud service. Keep incoming event sources distinct from outgoing operations.

- The browser and Electron shell share the API/SPA backend. The CLI launches or operates OpenCompany; it is not a second Node.js API backend.
- Direct model-provider calls and supported optional Gateway routes coexist. Gateway is a separate stock LiteLLM deployment with its own infrastructure; its Redis is not the application execution engine.
- Integration APIs use per-install credentials. Gateway support does not imply it proxies every tool API or provider.
- MCP/SDK/CLI are adapter contracts, not proof that every arbitrary external operation works. Browser and mobile capabilities retain local/runtime/owner prerequisites.
- 1Password is optional in local installations and required in supported distributed credential mode. Configured event ingress includes provider-specific polling, webhooks and schedules; registered-but-unavailable nodes are not depicted as working ingress.

## Exact labels and relationships

- `user`: **Person** (client).
- `browser`: **Browser / Electron** (client).
- `cli`: **OpenCompany CLI** (client).
- `app`: **OpenCompany** (service).
- `models`: **Model providers** (external).
- `gateway`: **Optional LLM Gateway** (external).
- `apis`: **Integration APIs** (external).
- `mcp`: **MCP / SDK / CLI tools** (external).
- `devices`: **Browser / mobile runtime** (service).
- `secrets`: **Optional 1Password** (external).
- `events`: **Configured event sources** (external).

- Person → Browser / Electron: interact.
- Person → OpenCompany CLI: launch / operate.
- Browser / Electron → OpenCompany: HTTP / WebSocket.
- OpenCompany CLI → OpenCompany: launch / commands.
- OpenCompany → Model providers: native provider calls.
- OpenCompany → Optional LLM Gateway: supported gateway routes.
- Optional LLM Gateway → Model providers: vendor API calls.
- OpenCompany → Integration APIs: configured operations.
- OpenCompany → MCP / SDK / CLI tools: tool adapters.
- OpenCompany → Browser / mobile runtime: owned local capabilities.
- OpenCompany → Optional 1Password: resolve approved references.
- Configured event sources → OpenCompany: configured event ingress.

## Evidence

| Source | Implementation boundary |
|---|---|
| [server/main.py](../../../server/main.py) | API/WebSocket routing and production SPA serving share one backend origin. |
| [server/services/llm/unifier.py](../../../server/services/llm/unifier.py) | ChatUnifier provider-native request boundary. |
| [server/config/llm_defaults.json](../../../server/config/llm_defaults.json) | Supported native providers and configured Gateway model routes. |
| [gateway/README.md](../../../gateway/README.md) | Separate LiteLLM deployment, supported routes, independent PostgreSQL/Redis and vendor-key boundary. |
| [server/services/handlers/tools.py](../../../server/services/handlers/tools.py) | Agent tool execution and adapter dispatch. |
| [server/services/events/dispatch.py](../../../server/services/events/dispatch.py) | Configured WorkflowEvent admission/dispatch; current fail-soft event path. |
| [server/services/credentials/onepassword.py](../../../server/services/credentials/onepassword.py) | External reference resolution. |
| [server/nodes/mobile/_node.py](../../../server/nodes/mobile/_node.py) | Installation-owner authorization and Android queue prerequisite. |
