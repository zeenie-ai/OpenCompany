# Contributing to OpenCompany

Welcome! This guide is a contributor's map to the codebase. It tells you *where things live* and *where to start reading* when you want to add a feature. For the full architecture tour, use the [DeepWiki badge](https://deepwiki.com/zeenie-ai/OpenCompany) on the README or browse [docs-internal/](docs-internal/).

## Contribution Workflow

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

See [SETUP.md](docs-internal/SETUP.md) for environment setup and [SCRIPTS.md](docs-internal/SCRIPTS.md) for the full scripts reference.

## System Overview

[![System Overview](docs/diagrams/system-overview.svg)](https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/docs/diagrams/system-overview.svg)

At a glance:

- **115+ workflow nodes** across ~30 palette groups (live count: glob `server/nodes/**/__init__.py`; group list: `server/nodes/groups.py`; one self-contained plugin folder per node under `server/nodes/<group>/`)
- **12 LLM providers** via a hybrid native SDK + LangChain architecture (11 chat-model nodes; xAI native-chat-only)
- **Specialized AI agents** with the Agent Teams delegation pattern — SSOT is the `AI_AGENT_TYPES` frozenset in `server/constants.py`, which spans the base/specialized/team-lead agents plus the CLI-backed (`claude_code_agent`, `rlm_agent`) and Vertex-hosted (`vertex_managed_agent`) variants; `codex_agent` is a sibling CLI-agent plugin
- **WebSocket-first API** replacing most REST endpoints (live handler count = `MESSAGE_HANDLERS` + plugin registries)
- **65+ built-in skills**, editable in-UI with SKILL.md defaults on disk (live count: glob `server/skills/**/SKILL.md`)
- **Two execution modes** with automatic fallback: Temporal distributed, sequential

## How Workflows Execute

[![Execution Flow](docs/diagrams/execution-flow.svg)](https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/docs/diagrams/execution-flow.svg)

[WorkflowService](server/services/workflow.py) is a thin facade that routes each run through Temporal when available, falling back to a plain sequential walk otherwise. Every run has an isolated `ExecutionContext` with no shared global state. Nodes are scheduled continuously — when any node completes, its newly-ready dependents start immediately (`FIRST_COMPLETED` pattern) instead of waiting for a whole layer to finish.

Deep dives: [DESIGN.md](docs-internal/DESIGN.md) - [TEMPORAL_ARCHITECTURE.md](docs-internal/TEMPORAL_ARCHITECTURE.md) - [event_framework.md](docs-internal/event_framework.md)

## AI Agent System

[![AI Agent Routing](docs/diagrams/ai-agent-routing.svg)](https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/docs/diagrams/ai-agent-routing.svg)

AI execution splits into two paths. `execute_chat()` for direct chat completions delegates every provider to the native SDK layer in [services/llm/](server/services/llm/) via `ChatUnifier` (12 providers, lazy imports, normalized `LLMResponse`; Groq, Cerebras, and the local servers ride the OpenAI-compatible client with a custom `base_url` — the old chat-path LangChain fallback is retired). `execute_agent()` and `execute_chat_agent()` build a LangChain chat model (`ChatOpenAI` / `ChatAnthropic` / etc.) and drive it through `_run_agent_loop`. Team leads (`orchestrator_agent`, `ai_employee`) receive an intrinsic Task Manager and may assign only agents connected to their `input-teammates` handle. Delegate descriptors stay internal. `task_manager(assign_task)` persists and returns `queued`; Temporal hands work to a detached `DelegatedTaskWorkflow`, while legacy execution reuses the same durable task record. Completion emits `taskTrigger` with owning execution context for a separate lead review. The RLM Agent uses a REPL-based recursive language model pattern. Long-running activities remain alive through activity heartbeats.

Deep dives: [agent_architecture.md](docs-internal/agent_architecture.md) - [native_llm_sdk.md](docs-internal/native_llm_sdk.md) - [agent_teams.md](docs-internal/agent_teams.md) - [memory_compaction.md](docs-internal/memory_compaction.md) - [cli_agent_framework.md](docs-internal/cli_agent_framework.md)

## Repository Map

| Directory | What lives here | Start reading |
|---|---|---|
| `server/nodes/<category>/<node>/__init__.py` | Workflow node plugins (self-contained folders, NodeSpec + execute) — backend SSOT since Wave 11 | [plugin_system.md](docs-internal/plugin_system.md), [server/nodes/README.md](server/nodes/README.md) |
| `client/src/components/` | React Flow canvas, parameter panel, modals | [CLAUDE.md](CLAUDE.md) |
| `server/services/` | WorkflowService, NodeExecutor, AI service | [DESIGN.md](docs-internal/DESIGN.md) |
| `server/services/handlers/` | Cross-cutting orchestration only (`tools.py` AI-tool dispatch + delegation, `triggers.py`, `todo.py`) — per-node handlers live inside the plugins since Wave 11 | [node_creation.md](docs-internal/node_creation.md) |
| `server/services/llm/` | Native LLM SDK layer (12 providers) | [native_llm_sdk.md](docs-internal/native_llm_sdk.md) |
| `server/services/execution/` | Decide pattern, DLQ, recovery, conditions | [DESIGN.md](docs-internal/DESIGN.md) |
| `server/services/temporal/` | Distributed execution via Temporal | [TEMPORAL_ARCHITECTURE.md](docs-internal/TEMPORAL_ARCHITECTURE.md) |
| `server/routers/websocket.py` | WebSocket endpoint + core `MESSAGE_HANDLERS` (plugins register more via `ws_handler_registry`) | [status_broadcaster.md](docs-internal/status_broadcaster.md) |
| `server/core/` | Cache, encryption, DI container, config | [credentials_encryption.md](docs-internal/credentials_encryption.md) |
| `server/skills/` | Skill SKILL.md files, one folder per agent domain (live count: glob `server/skills/**/SKILL.md`) | [GUIDE.md](server/skills/GUIDE.md) |
| `server/config/` | llm_defaults.json, pricing.json, model_registry.json, email_providers.json, google_apis.json, credential_providers.json, ai_cli_providers.json, node_allowlist.json | [pricing_service.md](docs-internal/pricing_service.md), [node_allowlist.md](docs-internal/node_allowlist.md) |
| `server/tests/` | Contract-test invariants + per-category node tests + `NodeTestHarness` | [tests/nodes/_harness.py](server/tests/nodes/_harness.py), [tests/credentials/README.md](server/tests/credentials/README.md) |
| `client/src/` (styling + themes) | Tailwind tokens, shadcn primitives, the 12-theme contract | [frontend_architecture.md](docs-internal/frontend_architecture.md), [theme_system.md](docs-internal/theme_system.md) |
| `docs-internal/` | In-repo architecture deep dives (50+ files) | Index below |

## How to Contribute Features

[![Node Anatomy](docs/diagrams/node-anatomy.svg)](https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/docs/diagrams/node-anatomy.svg)

The diagram above shows the full lifecycle of a workflow node: one self-contained Python plugin folder that auto-registers on import and renders itself on the frontend with zero TypeScript edits. Use these recipes as a starting point:

**Add a workflow node**
- Start at [node_creation.md](docs-internal/node_creation.md) (the decision tree routes action / trigger / tool / dual-purpose / agent work), copy the recipe from [server/nodes/README.md](server/nodes/README.md), and reach for [plugin_system.md](docs-internal/plugin_system.md) as the deep reference
- One plugin folder: `server/nodes/<category>/<node>/__init__.py` — subclasses `ActionNode` / `TriggerNode` / `ToolNode`, declares Pydantic `Params`/`Output` + `@Operation` methods; auto-registers on import, zero frontend edits
- Icon = `icon.svg` in the folder, color = `meta.json` (never class attributes); raise `NodeUserError` for user-correctable failures
- Add a behavioral test in `server/tests/nodes/test_<category>.py` (the invariant suites cover the contract automatically — see Testing below)

**Add an LLM provider**
- Guide: [native_llm_sdk.md](docs-internal/native_llm_sdk.md) → "Adding a New Provider"
- OpenAI-compatible (DeepSeek, Kimi, Mistral pattern): config-only in `server/config/llm_defaults.json` + the compat list in `services/llm/providers/_compat.py`
- Custom-SDK provider: new file in `server/services/llm/providers/` that calls `register_provider(ProviderSpec(...))` at module bottom (lazy factory + `sdk_exception_refs`; the legacy `factory.py` was removed — `register_provider` is the only entry point)
- Chat-model node plugin: `server/nodes/model/<provider>_chat_model/__init__.py`; for agent-dropdown exposure also extend the `provider` Literal in `nodes/agent/{ai_agent,chat_agent,_specialized}` and `detect_ai_provider` in `server/constants.py`

**Add a dual-purpose tool (workflow node + AI tool)**
- Guide: [node_creation.md](docs-internal/node_creation.md); live references: the whatsapp / twitter / stripe folders
- Plugin folder with `group: ['category', 'tool']` and `usable_as_tool = True`; the Pydantic `Params` doubles as the LLM-visible tool schema (keep it flat — no nested models / `$defs`)
- If you give the tool a short `tool_name` that isn't `<snake_case_of_node_type>` AND ship a paired skill, add a `visuals.json` alias entry keyed by the tool name carrying **icon and color** — otherwise the Master Skill row renders blank (locked by `server/tests/test_skill_icon_resolution.py`)

**Add a specialized AI agent**
- Guide: [node_creation.md](docs-internal/node_creation.md) + [agent_architecture.md](docs-internal/agent_architecture.md)
- Add the plugin under `server/nodes/agent/<name>/` (extends `SpecializedAgentBase` from `server/nodes/agent/_specialized.py`)
- Single cross-cutting edit: add the agent's `type` string to the `AI_AGENT_TYPES` frozenset in `server/constants.py` — delegation dispatch imports that frozenset, nothing else to update
- CLI-backed agents (Claude Code / Codex shape) follow [cli_agent_framework.md](docs-internal/cli_agent_framework.md); Vertex-hosted agents follow the `vertex_managed_agent` plugin

**Add a skill**
- Guide: [GUIDE.md](server/skills/GUIDE.md) (folder structure, SKILL.md frontmatter, and the tool-naming contract that drives the skill's icon)
- New folder under `server/skills/<domain>/<skill-name>/SKILL.md` with YAML frontmatter + markdown body

**Add an event source / trigger (Wave 12)**
- Guides: [node_creation.md](docs-internal/node_creation.md) → Wave 12 recipe (authoring) → [event_framework.md](docs-internal/event_framework.md) (Temporal routing + ops) → [stripe_service.md](docs-internal/stripe_service.md) (the reference implementation)
- Pick the source shape: `DaemonEventSource` (CLI daemon), `WebhookSource` + `WebhookTriggerNode` (signed webhooks, with a verifier from `services.events.verifiers`), or `PollingEventSource` (API polling)
- Register via `register_canary_trigger_type(node_type, cloudevent_type)` + emit `WorkflowEvent`s via `dispatch.emit` from the plugin's `_events.py`; plugin-owned filters go through `register_filter_builder` — never hand-edit `event_waiter.py` ([event_waiter_system.md](docs-internal/event_waiter_system.md) is historical, pre-Wave-11)

**Integrate a CLI-managed-auth service (Stripe / Vercel / GitHub shape)**
- Guide: [node_creation.md](docs-internal/node_creation.md) → "CLI-managed auth" recipe; references: [stripe_service.md](docs-internal/stripe_service.md) (two-step browser OAuth), [vercel_service.md](docs-internal/vercel_service.md) (device-flow variant), [github_service.md](docs-internal/github_service.md) (gh owns auth entirely)
- The external CLI owns the real tokens; the plugin writes marker tokens via `auth_service.store_oauth_tokens(provider, "cli-managed", "cli-managed")` and broadcasts the generic `credential_catalogue_updated` event — zero per-provider frontend code
- `_install.py` auto-downloads the pinned CLI binary into the shared packages tree

**Integrate an OAuth service**
- Guides: [plugin_system.md](docs-internal/plugin_system.md) (Connection facade, credentials, `register_router` / `register_option_loader`); live reference: [server/nodes/google/](server/nodes/google/) — 7 nodes sharing one OAuth connection via `_oauth.py` / `_router.py` / `_auth_helper.py`
- ([new_service_integration.md](docs-internal/new_service_integration.md) is historical — do not follow its steps)

**Add a credential provider**
- Declare a `Credential` subclass in the plugin folder's `_credentials.py` and add the provider entry to `server/config/credential_providers.json` — the Credentials Modal renders it with no React edits
- Guides: [credentials_encryption.md](docs-internal/credentials_encryption.md) (storage pipeline) + [credentials_panel.md](docs-internal/credentials_panel.md) (modal state machine); test conventions in [server/tests/credentials/README.md](server/tests/credentials/README.md)

**Contribute frontend / a new theme**
- Read [frontend_architecture.md](docs-internal/frontend_architecture.md) first (stack, token tiers, state-ownership boundary, the strict styling rules), then [theme_system.md](docs-internal/theme_system.md) for the theme contract
- New themes follow theme_system.md → "Adding a new theme" (per-theme CSS file + tokens; no component code changes — that is the contract)
- New backend uiHint flags need two frontend-side edits: `INodeProperties.ts` + the `known` set in `server/tests/test_node_spec.py`

## Testing Your Contribution

- **Contract invariants run automatically.** `server/tests/test_plugin_contract.py` and `test_node_spec.py` iterate every registered plugin and assert the declared shape — a new node is covered the moment it imports. `test_plugin_self_containment.py` enforces the no-cross-plugin-imports rule; `test_skill_icon_resolution.py` asserts every skill resolves an icon.
- **Behavioral tests** live per category in `server/tests/nodes/test_<category>.py`, driven through `NodeTestHarness` ([server/tests/nodes/_harness.py](server/tests/nodes/_harness.py)) — it executes any node via `NodeExecutor` with mocked services and asserts the result envelope.
- **Import sanity:** `uv run pytest --collect-only` (from `server/`) is the live plugin-count invariant — it fails if any plugin errors at import.
- **Credential tests** follow the numbered-invariant style documented in [server/tests/credentials/README.md](server/tests/credentials/README.md).
- Run everything: `uv run pytest` from `server/`, `npm test` from `client/`, `uv run pytest cli/tests` from the repo root.

## Local Dev Quick Reference

Development from source uses **pnpm** (not npm). The `scripts/preinstall.js` hook enforces this when `pnpm-workspace.yaml` is present. Install pnpm once with `npm install -g pnpm`.

```bash
pnpm install           # install workspace dependencies
pnpm run dev           # start frontend + backend + Temporal + WhatsApp
pnpm run stop          # stop everything
pnpm run build         # production build
pnpm exec tsc --noEmit # typecheck client (from client/)
uv run pytest          # run backend tests (from server/, uv-managed venv)
```

Full setup and scripts reference: [SETUP.md](docs-internal/SETUP.md) - [SCRIPTS.md](docs-internal/SCRIPTS.md)

## Full Documentation Index

| Document | Description |
|---|---|
| [DESIGN.md](docs-internal/DESIGN.md) | Execution engine architecture, design patterns, execution modes |
| [TEMPORAL_ARCHITECTURE.md](docs-internal/TEMPORAL_ARCHITECTURE.md) | Distributed execution via Temporal activities |
| [workflow-schema.md](docs-internal/workflow-schema.md) | Workflow JSON schema and node catalog (live count = glob `server/nodes/**/__init__.py`) |
| [ROADMAP.md](docs-internal/ROADMAP.md) | Implementation status and completed phases |
| [SETUP.md](docs-internal/SETUP.md) | Development environment setup |
| [SCRIPTS.md](docs-internal/SCRIPTS.md) | npm/shell scripts reference |
| [server-readme.md](docs-internal/server-readme.md) | Python backend architecture and API |
| [agent_architecture.md](docs-internal/agent_architecture.md) | AI Agent / Chat Agent skill and tool discovery |
| [agent_delegation.md](docs-internal/agent_delegation.md) | How delegated agents share context and memory |
| [agent_teams.md](docs-internal/agent_teams.md) | Agent Teams pattern with `input-teammates` handle |
| [native_llm_sdk.md](docs-internal/native_llm_sdk.md) | Native LLM SDK layer and provider protocol |
| [rlm_service.md](docs-internal/rlm_service.md) | Recursive Language Model agent via REPL |
| [claude_code_agent.md](docs-internal/claude_code_agent.md) | Claude Code agent hub (routes to architecture, interactive mode, and the vendored `claude_code_*_reference.md` snapshots) |
| [claude_code_agent_architecture.md](docs-internal/claude_code_agent_architecture.md) | Claude Code SDK integration as a specialized agent |
| [cli_agent_framework.md](docs-internal/cli_agent_framework.md) | Multi-provider CLI agent runtime (Claude Code / Codex / Gemini) — worktree isolation, MCP bridge, memory bridge |
| [autonomous_agent_creation.md](docs-internal/autonomous_agent_creation.md) | Autonomous agents with Code Mode patterns |
| [event_framework.md](docs-internal/event_framework.md) | Wave 12 event framework — Temporal Signals + Visibility routing, Search Attributes, plugin `_events.py` contract |
| [stripe_service.md](docs-internal/stripe_service.md) | Reference Wave 12 plugin — signed webhooks + CLI-managed auth, file-by-file |
| [vercel_service.md](docs-internal/vercel_service.md) | CLI-managed auth, device-flow variant |
| [github_service.md](docs-internal/github_service.md) | gh CLI integration — CLI owns auth entirely |
| [event_waiter_system.md](docs-internal/event_waiter_system.md) | Push-based trigger waiters *(historical, pre-Wave-11 — canvas-Run path only)* |
| [status_broadcaster.md](docs-internal/status_broadcaster.md) | WebSocket broadcaster (live handler count via `len(MESSAGE_HANDLERS) + len(get_ws_handlers())`) |
| [credentials_encryption.md](docs-internal/credentials_encryption.md) | Fernet + PBKDF2 credentials system |
| [memory_compaction.md](docs-internal/memory_compaction.md) | Token tracking and model-aware compaction |
| [pricing_service.md](docs-internal/pricing_service.md) | LLM and API cost tracking |
| [proxy_service.md](docs-internal/proxy_service.md) | Residential proxy provider management |
| [ci_cd.md](docs-internal/ci_cd.md) | GitHub Actions workflows |
| [node_creation.md](docs-internal/node_creation.md) | How to create new nodes |
| [memory_lifecycle.md](docs-internal/memory_lifecycle.md) | Canonical home for markdown memory format, vector store, claude_code_agent session resume |
| [tool_building_pipeline.md](docs-internal/tool_building_pipeline.md) | Canonical home for `_build_tool_from_node`, tool discovery, per-type Temporal dispatch |
| [new_service_integration.md](docs-internal/new_service_integration.md) | External service integration *(historical, pre-Wave-11 — do not follow; see the OAuth recipe above)* |
| [cli_services_integration.md](docs-internal/cli_services_integration.md) | CLI service lifecycle management |
| [onboarding.md](docs-internal/onboarding.md) | Welcome wizard and replay |
| [frontend_architecture.md](docs-internal/frontend_architecture.md) | Current frontend stack, token tiers, state-ownership boundary, strict styling rules |
| [theme_system.md](docs-internal/theme_system.md) | The 12-theme token contract + "Adding a new theme" checklist |
| [workflow_ops_protocol.md](docs-internal/workflow_ops_protocol.md) | Backend → canvas mutation wire format (`{operations: [...]}`) |
| [schema_source_of_truth_rfc.md](docs-internal/schema_source_of_truth_rfc.md) | Backend-as-SSOT for node schemas, icons, output schemas |
| [node_allowlist.md](docs-internal/node_allowlist.md) | Single-config UI visibility gating (`node_allowlist.json`) |
| [authentication.md](docs-internal/authentication.md) | JWT/cookie auth — modes, middleware, frontend bootstrap |
| [errors.md](docs-internal/errors.md) | Known errors and troubleshooting |
| [performance.md](docs-internal/performance.md) | Cold-start measurements, optimisation history, anti-patterns |
| [release_build_pipeline.md](docs-internal/release_build_pipeline.md) | npm-distribution build pipeline (tsgo, esbuild sidecar, bytecode) |
| [Skill Creation Guide](server/skills/GUIDE.md) | How to create new skills |

## Community

Join our [Discord](https://discord.gg/NHUEQVSC) for help, feedback, and updates.
