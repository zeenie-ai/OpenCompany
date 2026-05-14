# MachinaOs - Claude Documentation

## Project Overview
This is a React Flow-based workflow automation platform implementing n8n-inspired architectural patterns. The project has undergone a comprehensive refactoring to implement modern INodeProperties interface system with full TypeScript compliance and code cleanup.

## Documentation Reference

**Always refer to these documentation files for detailed guides:**

| Document | Description |
|----------|-------------|
| **[Frontend Architecture](./docs-internal/frontend_architecture.md)** | Current frontend stack (React 19 + Vite + Tailwind v4 + shadcn/ui + Radix + RHF/zod + TanStack Query + Zustand). Tokens, primitives, forms, credentials exemplar, ownership boundary, `uiHints` catalogue. |
| **[UI Migration Plan](./docs-internal/ui_migration_plan.md)** | antd → shadcn/ui migration plan + completion log. Waves 1–10 done. Full frontend is schema-driven (backend SSOT); remaining DIY widget registry (ex-Phase 6) is the one deferred item. |
| **[Node Allowlist](./docs-internal/node_allowlist.md)** | Single-config UI visibility — `server/config/node_allowlist.json` controls which nodes / credential categories / skill folders show in the UI. Five lists with two enforcement tiers (mode-gated allowlist + absolute blocklist). `useNodeAllowlist` hook exposes `isVisible` / `isBlocked` / `isAllowed` / `isCredentialCategoryDisabled` / `isSkillFolderDisabled`. Adding a new disabled domain = single JSON edit, no code change. |
| **[Theme System](./docs-internal/theme_system.md)** | 10-way visual theme system — 5 utopian (light, dark, renaissance, greek, edo, steampunk, atomic) + 5 dystopian (cyber, wasteland, rot, plague, surveillance) — driven by `<html data-theme>` + per-theme CSS files in `client/src/themes/`. Token taxonomy (surface / fg / border / accent / typography / motion), shadcn HSL-triplet bridge, action role tokens, decorative-layer wrappers (`.app-frame` / `.canvas-host` / `.modal-frame`), per-component decorative ornaments (panel textures + canvas decorations + node pseudo-element overlays + theme-specific keyframes), **canvas-node visual contract via `--node-color` CSS custom property** (no inline `background` / `border` on node components — base.css + per-theme CSS owns visuals; `NodeStyle` helper type at [types/NodeTypes.ts](./client/src/types/NodeTypes.ts) makes the inline custom-prop typecheck-clean), **`--node-pulse-color` separate from `--node-color`** so executing-node glow uses each theme's highest-contrast accent regardless of plugin accent (Cyber neon cyan, Surveillance REC red, Renaissance ultramarine, etc.), **`data-page-hidden` animation pause** (toggled by Dashboard's `visibilitychange` listener; base.css declares `html[data-page-hidden] *, *::before, *::after { animation-play-state: paused !important }` to prevent compositor stall on tab return), **per-theme icon glyph system** (290 SVGs across 29 keys × 10 themes via [themedGlyphs.ts](./client/src/assets/icons/themedGlyphs.ts) + theme-aware [NodeIcon.tsx](./client/src/assets/icons/NodeIcon.tsx)), **per-theme canvas-grid + custom cursors** via `--canvas-grid` / `--cursor-default` slots, **decorative HTML primitives** (`<SvgFilterDefs>` mounting `#ink-blot` / `#noise` / `#crt` filter IDs at app root, `<DropCap>` wrapper for Renaissance ornament rule), **parameter panel migrated to Tailwind tokens** (no `useAppTheme()` reads; section headers carry the display-typography triplet; raw `<Button>` swapped to `<ActionButton intent>`), **per-theme scrollbar webkit rules** in all 10 themes, 9-event WebAudio sound system (10 packs via `--sound-pack` token + `useSound()` hook + global hover delegate + sonner toast monkey-patch + `withSound()` HOC + `Sounds.unlock()` gesture-unlock for AudioContext autoplay-policy compliance), `@media (prefers-reduced-motion: reduce)` accessibility, 30 ms throttle on `type` / `hover`, migration recipe, anti-patterns. Read this before adding a new theme, migrating a component to the new contract, or adding a canvas-node component. |
| **[Schema Source of Truth RFC](./docs-internal/schema_source_of_truth_rfc.md)** | Backend is SSOT for node schemas, visual metadata, handlers, palette metadata, icons. Plugin pattern: one `BaseNode` subclass in `server/nodes/<group>/<plugin>/__init__.py`. Wire format: `asset:<key>` / `<lib>:<brand>` / URL / emoji. Endpoint: `/api/schemas/nodes/{type}/spec.json`. Live invariant total via `pytest --collect-only`. |
| **[Plugin System (Wave 11)](./docs-internal/plugin_system.md)** | Class-based plugin-first architecture. `BaseNode` / `ActionNode` / `TriggerNode` / `ToolNode` + `@Operation` decorator. Pydantic `Params`/`Output`. Declarative `Routing` DSL + `Connection` facade (Nango pattern). 18 `Credential` subclasses live in each node folder's `_credentials.py` (or inline for single-use). `TaskQueue` constants route to Temporal worker pools. Plugins live across 9 queues (live count via `glob server/nodes/**/__init__.py`); handler bodies fully inlined (`services/handlers/` shrank 12.8K → 1.1K LOC across 16 → 4 files; only cross-cutting orchestration remains: `tools.py` AI-tool dispatch + agent delegation, `google_auth.py`, `triggers.py`). **Wave 11.H added "self-contained plugin folders"** — up to six generic registries (`ws_handler_registry`, `register_router`, `event_waiter.{register_filter_builder,register_trigger_precheck}`, `status_broadcaster.register_service_refresh`, `node_output_schemas.register_output_schema`) so rich plugins like telegram own their entire surface area without core-services edits. |
| **[Nodes Cookbook](./server/nodes/README.md)** | 5-minute recipe + folder map + shared helpers (`_base.py` / `_inline.py` per domain) + shared credentials + **canonical folder-per-plugin shape (telegram is the reference implementation)** + contract invariants + common pitfalls. Lives next to the plugin files. |
| **[Node Creation Guide](./docs-internal/node_creation.md)** | Canonical plugin recipe — one self-contained folder per plugin under `server/nodes/<group>/<plugin>/`, rooted at `__init__.py`. Multi-file split (`_service.py` / `_handlers.py` / etc.) when the plugin owns long-lived state. Zero frontend edits, zero core-services edits. Auto-registers via `BaseNode.__init_subclass__` + the six `register_*` hooks. Covers tool nodes, dual-purpose nodes (workflow + AI tool), and specialized agents as variations of the same recipe. |
| **[Agent Architecture](./docs-internal/agent_architecture.md)** | How AI Agent and Chat Agent discover skills/tools, inject them into LLM prompts, and execute via the plain-async `_run_agent_loop` |
| **[Agent Delegation](./docs-internal/agent_delegation.md)** | How memory, parameters, and execution context flow when one AI agent delegates work to another agent connected as a tool |
| **[Agent Teams](./docs-internal/agent_teams.md)** | Claude SDK Agent Teams pattern - AI Employee and Orchestrator nodes with input-teammates handle for multi-agent coordination |
| **[Memory Compaction](./docs-internal/memory_compaction.md)** | Token tracking and model-aware memory compaction using native provider APIs (Anthropic, OpenAI) with threshold = 50% of context window |
| **[Pricing Service](./docs-internal/pricing_service.md)** | Centralized cost tracking for LLM tokens and API services (Twitter, Google Maps) with HTTPX event hooks |
| **[Proxy Service](./docs-internal/proxy_service.md)** | Residential proxy provider management with template-based URL formatting, health scoring, and transparent HTTP node injection |
| **[Email Service](./docs-internal/email_service.md)** | IMAP/SMTP integration via Himalaya CLI with EmailService orchestrator, provider presets, custom credential fallback, and polling triggers |
| **[Stripe Service](./docs-internal/stripe_service.md)** | Stripe CLI integration — `stripeAction` (CLI pass-through) + `stripeReceive` (signed-webhook trigger). Reference plugin for the Wave 12 event framework AND the **CLI-managed-auth pattern**: browser OAuth via `stripe login --non-interactive` + `--complete <url>` (URL extracted from `next_step` shell command via `shlex.split`) + auto-installed binary (`ensure_stripe_cli` from GitHub releases) + marker-token reuse of `auth_service.store_oauth_tokens` + CloudEvents-shaped `broadcast_credential_event("credential.oauth.connected" \| ".disconnected", provider=...)` — zero per-provider hardcoding in the frontend. Daemon stdout/stderr ingested via `ProcessService`'s `line_handler` callback (no log-file tailing); credential gate routed through `await self.has_credential()` so non-api-key auth (`is_logged_in()`) plugs in via subclass override. |
| **[CI/CD Pipeline](./docs-internal/ci_cd.md)** | GitHub Actions workflows, predeploy validation, release publishing, and composite setup action |
| **[Performance](./docs-internal/performance.md)** | Cold-start measurements (Application startup complete 2.90 s warm, first WS connect 8.29 s) + per-phase timeline + optimisation history (lazy LangChain imports, esbuild sidecar bundle, scoped `compileall`, PartySocket reconnect, TanStack Query auth bootstrap, `manualChunks`) + bottleneck inventory + reproduction commands + anti-patterns to never reintroduce. Pairs with the build-time pipeline doc below. |
| **[Release Build Pipeline](./docs-internal/release_build_pipeline.md)** | npm-distribution build pipeline: `tsgo` for type-check (5× faster than tsc), Vite `manualChunks` + `target: 'es2022'`, esbuild Node.js sidecar bundle (drops `tsx` interpreter cost), `python -O -m compileall` for project-source bytecode (excludes `.venv/`, `tests/`). Single source of truth for the bytecode-compile path list: `machina.commands.build.COMPILEALL_SOURCE_DIRS`. |
| **[Workflow Schema](./docs-internal/workflow-schema.md)** | JSON schema for workflows, edge handle conventions, config node architecture |
| **[Execution Engine Design](./docs-internal/DESIGN.md)** | Architecture patterns, design standards, and implementation details for the workflow execution engine |
| **[Execution Roadmap](./docs-internal/ROADMAP.md)** | Implementation status, completed phases, and pending features |
| **[Setup Guide](./docs-internal/SETUP.md)** | Development environment setup and installation instructions |
| **[Scripts Reference](./docs-internal/SCRIPTS.md)** | Available npm/shell scripts and their usage |
| **[Server Documentation](./docs-internal/server-readme.md)** | Python backend architecture and API documentation |
| **[Skill Creation Guide](./server/skills/GUIDE.md)** | How to create new skills (folder structure, SKILL.md format, metadata, supporting files) |
| **[Known Errors & Troubleshooting](./docs-internal/errors.md)** | Documented root causes and fixes for common errors (SQLAlchemy Windows hang, Temporal issues, WhatsApp timeouts) |
| **[New Service Integration](./docs-internal/new_service_integration.md)** | Complete guide for integrating external services (OAuth, database, handlers, nodes, AI tools) - use Google Workspace as reference |
| **[Onboarding Service](./docs-internal/onboarding.md)** | First-launch welcome wizard with 5 steps, database persistence, and replay from Settings |
| **[CLI Services Integration](./docs-internal/cli_services_integration.md)** | Guide for integrating CLI-based services (Temporal, etc.) with proper lifecycle management |
| **[Temporal Architecture](./docs-internal/TEMPORAL_ARCHITECTURE.md)** | Distributed workflow execution: activities, FIRST_COMPLETED scheduling, horizontal scaling |
| **[Native LLM SDK](./docs-internal/native_llm_sdk.md)** | Native SDK layer in services/llm/: Protocol-based providers, config-driven base URLs, 10 providers, native vs LangChain path routing |
| **[Event Waiter System](./docs-internal/event_waiter_system.md)** | Generic asyncio.Future/Redis-Streams waiter for push-based trigger nodes (WhatsApp, Telegram, Webhook, Chat, Task completion) |
| **[Credentials Encryption](./docs-internal/credentials_encryption.md)** | Fernet + PBKDF2 encryption pipeline, separate credentials.db, two credential systems (OAuth vs API keys), multi-backend abstraction |
| **[Status Broadcaster](./docs-internal/status_broadcaster.md)** | WebSocket-first communication: StatusBroadcaster singleton, live count via `len(MESSAGE_HANDLERS) + len(get_ws_handlers())`, broadcast message types, Android two-state model |
| **[RLM Service](./docs-internal/rlm_service.md)** | Recursive Language Model agent with REPL-based execution (llm_query, rlm_query, FINAL) |
| **[Claude Code Agent](./docs-internal/claude_code_agent_architecture.md)** | Claude Code integration internals (LangGraph reference; companion to `cli_agent_framework.md`) |
| **[CLI Agent Framework](./docs-internal/cli_agent_framework.md)** | Multi-provider CLI runtime (Claude Code / Codex / Gemini): `AICliService.run_batch`, per-task worktree isolation, FastMCP bridge, **memory bridge** (`--continue` for first run + intra-process stream-json multi-turn + `--resume <UUID>` for crash recovery, all on a stable `cwd=repo_root`; `node_parameters_updated` broadcast on every successful turn). |
| **[Claude Code Interactive Mode](./docs-internal/claude_code_interactive_mode.md)** | The interactive-mode cutover: MachinaOs no longer uses `claude -p` headless. `ClaudeSessionPool` (keyed by `simpleMemory.node_id`) spawns `claude` as a plain subprocess with stdio pipes — **no PTY** — and drives it over the VSCode-extension protocol: `--output-format stream-json --input-format stream-json --verbose --ide`. User prompts written as JSON to `proc.stdin` (`{"type":"user","message":{"role":"user","content":"..."}}\n`); `system/init` / `assistant` / `result` / `system/compact_boundary` events stream back on `proc.stdout` (parsed by a background `stdout_reader_task` — the on-disk JSONL is persistence-only, not the runtime contract, because `result` events are stdout-only in stream-json mode). Cross-platform via plain pipes (the earlier PTY pattern was broken on Windows because pywinpty/ConPTY's emulated stdin never reached claude's Ink TUI keystroke handler). Stays in interactive billing — entrypoint `claude-vscode`, NOT `sdk-cli`. Multi-turn within one warm subprocess preserves the session UUID (verified end-to-end); cross-batch continuity via `--continue` (first run) or `--resume <UUID>` (crash recovery). Four typed CloudEvents (`claude.session.{spawned,cleared,terminated,usage}`) fire from the pool. `/compact` events forward to `CompactionService`. The non-pooled `AICliSession` path (one-shot prompt-in-argv runs) still uses PTY (out of scope for this refactor; works on POSIX). |
| **[CLI Agent Canonical Patterns RFC](./docs-internal/cli_agent_canonical_patterns_rfc.md)** | Audit of MachinaOs's `services/cli_agent` against the official Claude Code spec — six invariants (skills-as-files, MCP-only transport, `list_changed`, deferral, visible-tool filtering, native-session-continuity-via-stable-cwd) with current implementation status. |
| **[Claude Code CLI Reference (snapshot)](./docs-internal/claude_code_cli_reference.md)** | Verbatim snapshot of [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference) — every CLI subcommand + flag + system-prompt-flag matrix + the subset we emit from `services/cli_agent/providers/anthropic_claude.py`. Fetched 2026-05-11. |
| **[Claude Code Env Vars (snapshot)](./docs-internal/claude_code_env_vars_reference.md)** | Categorised [code.claude.com/docs/en/env-vars](https://code.claude.com/docs/en/env-vars) snapshot — auth, Bedrock/Vertex, model, bash, MCP, telemetry, session/debug, paths. Documents `CLAUDE_CONFIG_DIR`, `MAX_MCP_OUTPUT_TOKENS`, `ENABLE_TOOL_SEARCH` which we touch directly. |
| **[Claude Code Permission Modes (snapshot)](./docs-internal/claude_code_permission_modes_reference.md)** | Verbatim [code.claude.com/docs/en/permission-modes](https://code.claude.com/docs/en/permission-modes) — `default` / `acceptEdits` / `plan` / `auto` / `dontAsk` / `bypassPermissions` semantics, Shift+Tab cycle, protected paths. MachinaOs default is `acceptEdits`. |
| **[Claude Code Headless / Print Mode (snapshot)](./docs-internal/claude_code_headless_reference.md)** | Verbatim [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless) — `claude -p`, `--output-format` (`text` / `json` / `stream-json`), `--input-format`, `--bare`, stream-json event schema (`system/init`, `system/api_retry`, `system/plugin_install`). MachinaOs no longer uses `-p`; the pool path emits `--output-format stream-json --input-format stream-json --verbose --ide` over stdio pipes (interactive billing). The event schema is the contract `services/cli_agent/session_pool.py` parses off `proc.stdout`. |
| **[Claude Code Skills (snapshot)](./docs-internal/claude_code_skills_reference.md)** | Verbatim [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills) — `SKILL.md` frontmatter spec, discovery paths, content lifecycle, `context: fork`, dynamic context injection via `` !`<command>` ``. The spec MachinaOs materialises connected skills against in `_pre_spawn`. |
| **[Autonomous Agent Creation](./docs-internal/autonomous_agent_creation.md)** | Creating autonomous agents with Code Mode patterns and agentic loops |
| **[Polyglot Server](../polyglot-server/ARCHITECTURE.md)** | Plugin registry microservice with MCP gateway (optional integration) |

## Design Principles & Standards

**CRITICAL: Always follow these principles when modifying backend execution code:**

### 0. Adding a new node — the canonical recipe (Wave 11.H)

Every plugin is a self-contained folder under `server/nodes/<group>/<plugin>/` rooted at `__init__.py`. `BaseNode.__init_subclass__` auto-registers metadata, schemas, handlers, and Temporal activity on import — zero edits anywhere else.

**Where to look:**
- [server/nodes/README.md](./server/nodes/README.md) — 5-minute walkthrough with the canonical folder template
- [docs-internal/plugin_system.md → Self-contained plugin folders](./docs-internal/plugin_system.md#self-contained-plugin-folders) — full reference, plus the **up-to-six generic registries** plugins self-wire into (`register_ws_handlers`, `register_router`, `register_filter_builder`, `register_trigger_precheck`, `register_service_refresh`, `register_output_schema`)
- [docs-internal/node_creation.md](./docs-internal/node_creation.md) — decision tree for action / trigger / tool / dual-purpose / specialized-agent nodes
- [server/nodes/telegram/](./server/nodes/telegram/) — reference implementation of the multi-file split (`_service.py` / `_handlers.py` / `_filters.py` / `_refresh.py` / `_credentials.py` / two node files)

**Wire format is the contract — not module paths.** The frontend identifies plugin commands by WebSocket message-type strings (`telegram_connect`, `telegram_status`, …). Moving handler bodies between Python files is invisible to the frontend so long as the registered keys stay the same.

**Don't** import the plugin folder from `routers/` / `services/` / another `nodes/` subfolder. **Don't** edit `event_waiter.py` / `status_broadcaster.py` / `routers/websocket.py` to add a plugin's handler / filter / refresh — register from the plugin's `__init__.py` instead.

### 1. Use Existing Patterns - No Tribal Code
- **Never add ad-hoc workarounds** - Use the established patterns documented in DESIGN.md
- **Conductor Decide Pattern** - All orchestration goes through `_workflow_decide()` loop
- **Fork/Join Parallelism** - Use `asyncio.gather()` for concurrent node execution
- **Prefect Task Caching** - Cache results via `hash_inputs()` and `generate_cache_key()`
- **Distributed Locking** - Use Redis SETNX pattern for concurrent access control

### 2. State Management
- **Isolated Execution Contexts** - Each workflow run has its own `ExecutionContext`
- **No Global State** - Never use module-level variables for execution state
- **Cache Persistence** - Execution state persists to Redis (production) or SQLite (local development)
- **Explicit State Machines** - Tasks follow `TaskStatus` enum, workflows follow `WorkflowStatus`

### 3. Separation of Concerns
- **Models** (`models.py`) - Pure data structures, JSON-serializable, no business logic
- **Cache** (`cache.py`) - Redis persistence abstraction only
- **Executor** (`executor.py`) - Orchestration logic, decide pattern implementation
- **Recovery** (`recovery.py`) - Heartbeat and crash recovery only
- **Conditions** (`conditions.py`) - Edge condition evaluation for runtime branching

### Backend Service Architecture (n8n-inspired)
The workflow backend follows modular architecture patterns from n8n, Temporal, and Conductor:

```
server/services/
├── workflow.py              # Facade (~460 lines) - thin coordinator
├── node_executor.py         # Single node execution with registry pattern
├── parameter_resolver.py    # Template variable resolution
├── agent_team.py            # AgentTeamService for multi-agent coordination
├── model_registry.py        # ModelRegistryService - model constraints from OpenRouter + llm_defaults
├── nodejs_client.py         # HTTP client for Node.js code executor
├── pricing.py               # LLM and API cost calculation (loads config/pricing.json)
├── markdown_formatter.py    # GFM markdown to platform-specific formatting (Telegram HTML, WhatsApp, plain)
├── ws_handler_registry.py   # Plugin-owned WS commands self-register here (Wave 11.H)
├── browser_service.py       # BrowserService singleton wrapping agent-browser CLI
├── himalaya_service.py      # HimalayaService CLI wrapper for IMAP/SMTP (any email provider)
├── email_service.py         # EmailService orchestrator (credential resolution, provider presets)
├── todo_service.py          # TodoService singleton for writeTodos tool (JSON per-session state)
├── scheduler.py             # APScheduler singleton for cron job management
├── memory.py                # Markdown-based conversation memory helpers
├── memory_store.py          # In-memory conversation store (LangChain 0.3+ compatible)
├── skill_prompt.py          # Skill system prompt builder (injects SKILL.md for personality skills)
├── text.py                  # TextService (text generation nodes)
├── chat_client.py           # JSON-RPC 2.0 WebSocket client for chat backend
├── claude_code_service.py   # Claude Code CLI wrapper (--max-budget-usd, session persistence)
├── claude_oauth.py          # Isolated Claude CLI auth (~/.claude-machina, no main-session impact)
├── tracked_http.py          # HTTPX event hooks for automatic API cost tracking
├── whatsapp_service.py      # WhatsApp RPC proxy helpers (used by nodes/whatsapp/*, not an APIRouter)
├── handlers/                # Cross-cutting orchestration only (Wave 11: 16 → 4 files, 12.8K → 1.1K LOC)
│   ├── tools.py             # AI-tool dispatch + agent delegation (~821 LOC)
│   ├── triggers.py          # Generic event-trigger handler
│   ├── google_auth.py       # Shared OAuth credential helper
│   └── __init__.py          # Docstring only
├── llm/                     # Native LLM provider SDKs (replaces LangChain for chat)
│   ├── __init__.py          # Public API exports
│   ├── protocol.py          # ThinkingConfig, Message, LLMResponse, LLMProvider Protocol
│   ├── config.py            # ProviderConfig, resolve_max_tokens, resolve_temperature
│   ├── factory.py           # create_provider() lazy-import factory
│   ├── messages.py          # filter_empty_messages, is_valid_message_content
│   └── providers/           # Per-provider implementations
│       ├── anthropic.py     # AnthropicProvider (anthropic SDK)
│       ├── openai.py        # OpenAIProvider (openai SDK)
│       ├── gemini.py        # GeminiProvider (google-genai SDK)
│       └── openrouter.py    # OpenRouterProvider (extends OpenAIProvider)
├── proxy/                   # Residential proxy provider management
│   ├── __init__.py          # Exports get_proxy_service, ProxyService
│   ├── service.py           # ProxyService singleton - provider selection, URL generation
│   ├── providers.py         # TemplateProxyProvider - JSON url_template formatting
│   └── models.py            # ProxyProvider, RoutingRule, SessionType enums
├── deployment/              # Event-driven deployment lifecycle
│   ├── __init__.py
│   ├── state.py             # DeploymentState, TriggerInfo dataclasses
│   ├── triggers.py          # TriggerManager (cron, event triggers)
│   └── manager.py           # DeploymentManager (deploy, cancel, status)
├── execution/               # Parallel workflow orchestration
│   ├── models.py            # ExecutionContext, TaskStatus
│   ├── executor.py          # WorkflowExecutor with decide pattern
│   ├── cache.py             # Cache persistence (Redis/SQLite)
│   └── recovery.py          # Crash recovery
└── temporal/                # Distributed workflow execution (optional)
    ├── __init__.py          # Exports TemporalExecutor, TemporalClientWrapper
    ├── workflow.py          # MachinaWorkflow orchestrator
    ├── activities.py        # Class-based activities with connection pooling
    ├── worker.py            # TemporalWorkerManager + run_standalone_worker()
    ├── executor.py          # TemporalExecutor interface
    ├── client.py            # Temporal client wrapper
    └── ws_client.py         # WebSocket connection pool

server/core/
├── container.py             # Dependency injection container
├── database.py              # SQLite database with cache CRUD methods
├── cache.py                 # CacheService with Redis/SQLite/Memory fallback
├── config.py                # Application configuration
├── logging.py               # Logging configuration
├── encryption.py            # Fernet encryption with PBKDF2 key derivation
├── credentials_database.py  # Async SQLite for encrypted API keys and OAuth tokens
└── credential_backends.py   # Multi-backend abstraction (Fernet, Keyring, AWS)

server/models/
├── cache.py                 # CacheEntry SQLModel for SQLite cache
├── auth.py                  # User model with bcrypt
└── database.py              # ConversationMessage, NodeParameter, ToolSchema, ChatMessage, TokenUsageMetric, APIUsageMetric, CompactionEvent, SessionTokenState, UserSettings, ProviderDefaults, AgentTeam, TeamMember, TeamTask, AgentMessage tables

server/config/
├── llm_defaults.json        # Per-provider defaults (model, base_url, max_output_tokens, context_length, temperature_range, reasoning_models, thinking_type, ...) AND a top-level `agent` block (recursion_limit, default_temperature, compaction.ratio) that drives the agent loop and CompactionService — no env-var defaults; this is the source of truth.
├── model_registry.json      # Cached model data from OpenRouter (auto-refreshed)
├── pricing.json             # LLM and API pricing config
├── google_apis.json         # Google Workspace API endpoints, scopes, OAuth callback paths
└── email_providers.json     # IMAP/SMTP provider presets (Gmail, Outlook, Yahoo, iCloud, ProtonMail, Fastmail, custom)

server/nodejs/                   # Persistent Node.js server for JS/TS execution
├── package.json                 # Dependencies: express, tsx
├── tsconfig.json                # TypeScript config (ES2024)
├── src/
│   └── index.ts                 # Express server (/execute, /health, /packages/*)
└── user-packages/               # User-installed npm packages
```

### Polyglot Server Integration (Optional)
MachinaOs can optionally integrate with **polyglot-server** - a centralized plugin registry microservice that exposes integrations through REST API, MCP (Model Context Protocol), and WebSocket.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────────┐
│                      MachinaOs                                   │
│  ┌────────────────┐    ┌────────────────┐                       │
│  │ React Flow     │───▶│ FastAPI Backend│                       │
│  │ Frontend       │    │ (port 3010)    │                       │
│  └────────────────┘    └───────┬────────┘                       │
│                                │                                 │
│              ┌─────────────────┴─────────────────┐              │
│              │         NodeExecutor              │              │
│              │  (registry-based dispatch)        │              │
│              └─────────────────┬─────────────────┘              │
└────────────────────────────────│────────────────────────────────┘
                                 │ HTTP (aiohttp)
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Polyglot Server (port 8080)                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   REST API  │  │     MCP     │  │  WebSocket  │              │
│  │   Gateway   │  │   Server    │  │   Handler   │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
│         └────────────────┼────────────────┘                      │
│                          ▼                                       │
│              ┌───────────────────────┐                          │
│              │    Plugin Registry    │                          │
│              │  Discord, Telegram,   │                          │
│              │  Notion, GitHub, etc. │                          │
│              └───────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

**Files:**
```
server/services/
├── polyglot_client.py          # HTTP client for polyglot-server
└── handlers/
    └── polyglot.py             # Standalone handler (not wired to NodeExecutor)
```

**PolyglotClient** (`server/services/polyglot_client.py`):
```python
class PolyglotClient:
    """HTTP client for polyglot-server plugin registry."""

    async def list_plugins(self) -> List[Dict[str, Any]]:
        """List all available plugins."""

    async def get_schema(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get plugin input/output schema for workflow node integration."""

    async def execute(self, plugin_name: str, action: str, params: Dict) -> Dict:
        """Execute a plugin action."""
```

**Polyglot Handler** (`server/services/handlers/polyglot.py`):
```python
async def handle_polyglot_node(
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any],
    polyglot_client,  # Injected via functools.partial
) -> Dict[str, Any]:
    """Execute a workflow node via polyglot-server plugin registry."""
    plugin_name = node_type.replace("Node", "").lower()
    result = await polyglot_client.execute(plugin_name, action, params)
    return {"success": True, "result": result.get("result", {}), ...}

# Node types that can be routed to polyglot-server
POLYGLOT_NODE_TYPES = frozenset([
    "discordNode", "telegramNode", "slackNode", "notionNode",
    "todoistNode", "gmailNode", "twitterNode", "githubNode", ...
])
```

**Configuration** (when enabled):
```bash
# In server/.env
POLYGLOT_SERVER_URL=http://localhost:8080  # polyglot-server address
```

**Current Status**: Standalone integration files created. Not wired into NodeExecutor to avoid disturbing existing workflow execution flow. Future integration will add polyglot node types to handler registry via `functools.partial` pattern.

### Node.js Code Executor
Persistent Node.js server for JavaScript/TypeScript code execution, replacing subprocess spawning per execution.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────┐
│                  Python Backend (port 3010)                  │
│  ┌────────────────┐     HTTP/JSON      ┌──────────────────┐ │
│  │ NodeJSClient   │◄──────────────────►│  Node.js Server  │ │
│  │ (aiohttp)      │   localhost:3020   │  (Express + tsx) │ │
│  └────────────────┘                    └──────────────────┘ │
│         ▲                                                    │
│         │                                                    │
│  ┌──────┴─────────┐                                         │
│  │ code.py        │                                         │
│  │ handlers       │                                         │
│  └────────────────┘                                         │
└─────────────────────────────────────────────────────────────┘
```

**Files:**
```
server/nodejs/
├── package.json              # Dependencies: express, tsx
├── tsconfig.json             # TypeScript config (ES2024)
├── src/
│   └── index.ts              # Express server with /execute, /health, /packages/*
└── user-packages/            # User npm packages directory
    └── package.json

server/services/
├── nodejs_client.py          # Async HTTP client for Node.js server
└── handlers/
    └── code.py               # handle_javascript_executor, handle_typescript_executor
```

**Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check with Node.js version |
| `/execute` | POST | Execute JS/TS code with input_data and timeout |
| `/packages/install` | POST | Install npm packages to user-packages |
| `/packages` | GET | List installed packages |

**Environment Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `NODEJS_EXECUTOR_URL` | `http://localhost:3020` | Server URL for Python client |
| `NODEJS_EXECUTOR_TIMEOUT` | `30` | Request timeout in seconds |
| `NODEJS_EXECUTOR_PORT` | `3020` | Server port |
| `NODEJS_EXECUTOR_HOST` | `localhost` | Server host |
| `NODEJS_EXECUTOR_BODY_LIMIT` | `10mb` | Max request body size |

**Key Modules:**

| Module | Responsibility | Pattern |
|--------|---------------|---------|
| `workflow.py` | Facade delegating to specialized modules | Facade Pattern |
| `node_executor.py` | Execute single node via handler registry | Registry + functools.partial |
| `parameter_resolver.py` | Resolve `{{node.field}}` templates | Compiled regex |
| `deployment/manager.py` | Deploy/cancel workflows, spawn runs | n8n Deployment |
| `deployment/triggers.py` | Setup cron/event triggers | Event-driven |
| `deployment/state.py` | Immutable state dataclasses | Dataclass |
| `temporal/executor.py` | Temporal-based distributed execution | Per-node Activities |
| `temporal/workflow.py` | Pure orchestrator (no business logic) | FIRST_COMPLETED |
| `temporal/worker.py` | Worker lifecycle + horizontal scaling | Connection Pooling |

**NodeExecutor Registry Pattern:**
```python
class NodeExecutor:
    def _build_handler_registry(self) -> Dict[str, Callable]:
        return {
            'start': handle_start,
            'aiAgent': _dispatch_plugin_node,  # Wave 11: routes via BaseNode.execute()
            # ... registry-based dispatch instead of if-else chains
        }
```

### 4. Dependency Injection
```python
# Correct: Receive dependencies via constructor
class WorkflowExecutor:
    def __init__(self, cache: ExecutionCache, node_executor: Callable):
        self.cache = cache
        self.node_executor = node_executor

# Wrong: Import and use global singletons
from services.some_service import global_instance
```

### 5. Error Handling & Logging
- **Log at appropriate levels**: DEBUG for routine operations, INFO for significant events, ERROR for failures
- **Never suppress errors silently** - Always log or propagate
- **Use structured logging** - Include context (node_id, execution_id, etc.)
- **Configurable via `.env`**: Set `LOG_LEVEL=DEBUG` for verbose output, `LOG_LEVEL=INFO` for production

#### Logging Configuration
```bash
# In server/.env
LOG_LEVEL=INFO                  # Default: INFO, DEBUG for verbose
LOG_FORMAT=json                 # 'json' (default) or 'text' for console
LOG_FILE=                       # Optional rotating-file destination
LOG_FILE_MAX_BYTES=10485760     # 10 MiB ceiling per file
LOG_FILE_BACKUP_COUNT=5         # Keep 5 backups (50 MiB total cap)
```

**What logs at each level:**
- `DEBUG`: Template resolution, parameter resolution, node execution details, event waiter registration, downstream traversal
- `INFO`: Workflow completion, deployment start/stop, significant state changes
- `ERROR`: Failures, exceptions, validation errors

#### Logging Infrastructure (canonical patterns)

**Console mode is timestamp-less by design.** The supervisor
(`machina/colors.py`) prepends `[HH:MM:SS.fff]` to every aggregated
line, so `configure_logging` does NOT add an inner `TimeStamper` in
console mode. JSON mode keeps ISO timestamps for machine consumers.
Helpers that print pre-logger init (`_startup_log` in `main.py`,
`_clog` in `core/container.py`) emit raw `print()` so the CLI prefix
is the single timing source.

**Context propagation via `structlog.contextvars`.** Bind once at the
entry point; every log record inside that async context picks the
fields up automatically. Stdlib `contextvars` rides `asyncio.gather`
child tasks.

```python
from core.logging import log_context

async with log_context(workflow_id=wf_id, node_id=node_id):
    await do_work()  # all logs inside carry workflow_id + node_id
```

`BaseNode.execute()` already wraps its body in
`log_context(node_id, node_type, workflow_id?)` so plugin operation
logs are auto-tagged — don't pass these as kwargs at each call site.

**Per-plugin OpenTelemetry span.** `BaseNode.execute()` opens a
`node.<type>.execute` span with attributes `node.id` / `node.type` /
`workflow.id` around the operation body. Single edit instruments every
plugin — no per-plugin span code needed.

**Source-tag resolver for the Terminal UI panel.** `record.name`
collapses to a ≤12-char tag via `_resolve_source_tag` in
`core/logging.py`:

1. `nodes.<plugin>.*` → `<plugin>` (auto-rule; no per-plugin entry)
2. `routers.<name>.*` → `<name>` (auto-rule)
3. Explicit registry `_LOG_SOURCE_TAGS` — only for cross-cutting
   services with long module names (`workflow_validator` → `validator`,
   `status_broadcaster` → `broadcaster`, `user_auth` → `auth`, etc.)
4. Second-segment fallback (`services.ai` → `ai`)

Plugins that genuinely want a different label from their folder name
call `register_log_source_tag(prefix, tag)` from their package
`__init__.py` — same self-registration pattern as the five plugin
registries (`ws_handler`, `filter_builder`, `trigger_precheck`,
`service_refresh`, `output_schema`).

**RotatingFileHandler** swaps in when `LOG_FILE` is set — no
unbounded log growth.

**NodeUserError vs Exception contract** (`services/plugin/base.py`):
- `NodeUserError` → single WARN line, no traceback, structured response
- `PermissionError` annotated with `.provider` / `.reason` / `.auth` →
  `error_type="PermissionDeniedError"` + `credential` envelope block +
  CloudEvents `credential.{auth}.runtime_failed` broadcast
- Bare `Exception` → `logger.exception` with full traceback

Reach for `NodeUserError` for any user-correctable failure
(missing required field, unknown enum value, bad regex). Reserve
`RuntimeError` / `Exception` for genuinely unexpected server bugs.

### 6. Cleanup & Lifecycle
- **Use existing teardown methods** - e.g., `_teardown_all_cron_triggers()` for cron cleanup
- **Cleanup in finally blocks** - Ensure resources are released even on error
- **No orphan prevention hacks** - Trust the existing lifecycle management

### 7. Frontend Design + Theme System (strict)

**Always use the existing design and theme systems.** Tribal styling reintroduced anywhere defeats the migration. The following rules are non-negotiable for any new or edited frontend file:

1. **Compose shadcn primitives** from [client/src/components/ui/](./client/src/components/ui/) — `Button`, `Badge`, `Alert`, `AlertDialog`, `Dialog`, `DropdownMenu`, `Select`, `Popover`, `Tooltip`, `Tabs`, `Card`, `Input`, `Textarea`, `Switch`, `Checkbox`, `Slider`, `Label`, `Form`, `Collapsible`, `Accordion`, `Skeleton`, `Sonner`. **Do not hand-roll** modals, dropdowns, menus, toasts, dialogs, or buttons when a primitive exists. Add `npx shadcn@latest add <name>` if the primitive is missing.
2. **Action buttons → `<ActionButton intent="...">`** ([client/src/components/ui/action-button.tsx](./client/src/components/ui/action-button.tsx)). The `intent` prop is a semantic role (`run | stop | save | config | secret | tools`), never a palette color. Never re-introduce the `actionButtonStyle()` / hand-built colored buttons.
3. **Style with Tailwind classes**, not `style={{...}}`. Inline `style` is allowed only for genuinely dynamic values (React Flow `<Handle>` positioning, runtime-computed coordinates, dynamic per-definition `nodeColor` on canvas nodes).
4. **Use the token tier table** in [docs-internal/frontend_architecture.md](./docs-internal/frontend_architecture.md#tokens--theming):
   - Generic chrome / status → shadcn semantic tokens (`bg-card`, `text-muted-foreground`, `border-border`, `text-success`, `bg-destructive`, `text-warning`, `text-info`, `bg-accent`, etc.)
   - Node-type-themed surfaces → `--node-X` role tokens (`bg-node-agent`, `bg-node-model-soft`, `border-node-skill-border`, `text-node-trigger`, `text-node-workflow`, `bg-node-tool-soft`)
   - Toolbar / panel actions → `--action-X` semantic role tokens (`bg-action-run-soft`, `text-action-stop`, `border-action-config-border`, etc.) for icon-only buttons + dropdown items; `<ActionButton intent="...">` for the standard "soft tinted button" pill
   - **No palette names in components.** `bg-dracula-green` etc. are forbidden in non-decorative code; always go through `--action-X` or `--node-X`
5. **No opacity arithmetic at call sites.** `bg-primary/10`, `border-node-agent/30`, `${color}25` template literals are forbidden. If a unique tint is needed, add a new variant to the theme (e.g., `--node-X-soft`, `--node-X-border`) and use it by name.
6. **No theme-locked names in non-decorative code.** Avoid `bg-dracula-purple`, `text-dracula-cyan`, etc. unless the constant accent is intentional (action-button palette). Prefer the role token (`bg-node-agent`) so future themes redefine without code edits.
7. **No `useAppTheme()` in new files.** It is grandfathered for the canvas node components and `EdgeConditionEditor` only because they interpolate per-definition `nodeColor`. Every other surface uses Tailwind + the tokens above.
8. **Icons → `lucide-react`.** Inline SVGs are reserved for non-iconographic graphics (charts, decorative shapes). Replace any `<svg>...</svg>` icon you encounter while editing.

When in doubt, read [docs-internal/frontend_architecture.md](./docs-internal/frontend_architecture.md) before introducing new patterns.

### 8. Naming Conventions (strict)

| Layer | Convention | Examples |
|---|---|---|
| Python identifier (function, variable, module, file) | `snake_case` | `get_user_settings`, `auth_service`, `node_allowlist.py` |
| JSON config key (read by Python) | `snake_case` | `enabled_nodes`, `default_llm_provider`, `compaction_ratio` |
| WebSocket message type (Python ↔ TS wire) | `snake_case` | `get_node_allowlist`, `save_user_settings`, `validate_api_key` |
| Database column / SQLModel field | `snake_case` | `created_at`, `auto_save_interval`, `examples_loaded` |
| Python class | `PascalCase` | `NodeAllowlistService`, `WorkflowExecutor` |
| TypeScript identifier | `camelCase` | `useNodeAllowlist`, `enabledNodes`, `isVisible` |
| TypeScript file (React hook) | `camelCase` starting with `use` | `useNodeAllowlist.ts`, `useWebSocket.ts` |
| Node type identifier | stored verbatim — **do not transform** | `aiAgent`, `httpRequest`, `openaiChatModel` (currently camelCase in this repo) |

**Crossing the wire**: payload keys between Python and TS are always `snake_case` (Python writes the payload). The TS hook receives `snake_case` keys and binds them to local `camelCase` variables; do not auto-transform across languages with a serializer.

Do not invent kebab-case or PascalCase variants for any of the rows above. The existing codebase is internally consistent — match it.

### 9. Cache System Architecture (n8n Pattern)
The cache system follows n8n's pattern with automatic fallback:

```
Production (Docker):  Redis → SQLite → Memory
Local Development:    SQLite → Memory (Redis disabled)
```

**Configuration** (`server/.env`):
```bash
REDIS_ENABLED=false           # Local dev: use SQLite
REDIS_URL=redis://redis:6379  # Production: Docker Redis
```

**CacheService** (`server/core/cache.py`):
```python
class CacheService:
    def __init__(self, database: Database, settings: Settings):
        self._database = database
        self._settings = settings
        self._redis: Optional[Redis] = None
        self._memory_cache: Dict[str, Any] = {}

    async def get(self, key: str) -> Optional[str]:
        # Try Redis first (if enabled)
        if self._redis:
            value = await self._redis.get(key)
            if value: return value
        # Fall back to SQLite
        entry = await self._database.get_cache_entry(key)
        if entry: return entry.value
        # Fall back to memory
        return self._memory_cache.get(key)
```

**SQLite Cache Model** (`server/models/cache.py`):
```python
class CacheEntry(SQLModel, table=True):
    __tablename__ = "cache_entries"
    key: str = Field(primary_key=True)
    value: str
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

**Key Methods** (`server/core/database.py`):
- `get_cache_entry(key)` - Get cache entry by key
- `set_cache_entry(key, value, ttl)` - Set with optional TTL
- `delete_cache_entry(key)` - Delete by key
- `cleanup_expired_cache()` - Remove expired entries

## Codebase Summary
- **Hybrid architecture**: Python (FastAPI + Pydantic plugins) + React/TypeScript frontend + Node.js subprocess for JS/TS code execution.
- **Backend NodeSpec is the single source of truth.** Plugins live in [`server/nodes/<group>/<name>.py`](./server/nodes/) and auto-register via `BaseNode.__init_subclass__`. Authoritative node count is whatever globs out of `server/nodes/**/*.py` (excluding `_*.py` helpers and `__init__.py`); folders cover agent / model / android / google / whatsapp / twitter / telegram / social / email / search / scraper / document / code / filesystem / proxy / location / chat / text / scheduler / trigger / tool / utility / workflow / skill / browser / stripe.
- **WebSocket-first frontend-backend communication.** Authoritative handler count is the size of the `MESSAGE_HANDLERS` dict in [`server/routers/websocket.py`](./server/routers/websocket.py) plus plugin-registered handlers via `services.ws_handler_registry`. Don't hand-maintain the count in this doc — it drifts on every plugin add.
- **Plugin-first architecture (Wave 11).** One file = one node. `services/handlers/` shrank from 12.8K → 1.1K LOC across 16 → 4 files. Live invariant total via `pytest --collect-only`.

## Frontend Performance Architecture

The frontend uses a layered cache + slice-subscription model so cold refreshes are instant and high-frequency status broadcasts do not cascade through the React tree. The patterns below are canonical -- follow them when adding new server-state queries, status broadcasts, or canvas node components.

### TanStack Query persistence ([client/src/lib/queryPersist.ts](./client/src/lib/queryPersist.ts))
- App is wrapped in `<PersistQueryClientProvider>` ([main.tsx](./client/src/main.tsx)) with a localStorage persister + `__APP_VERSION__` buster + 24h SWR window (RFC 5861).
- Only queries with key prefixes `nodeSpec` / `nodeGroups` / `pluginCatalogue` are dehydrated (see `shouldPersistQuery`). High-frequency / per-session queries stay in-memory.
- Hard refresh paints from cached specs **before** the WS connects, so canvas nodes never flash placeholder icons.

### `useNodeSpec` is a slice subscription, not a `useQuery` ([client/src/lib/nodeSpec.ts](./client/src/lib/nodeSpec.ts))
- Reads via `useSyncExternalStore` against `queryClient.getQueryCache().subscribe(...)` filtered by `hashKey(['nodeSpec', type])`. Per-spec observer count is **0**; only the matching slot triggers a re-render.
- Lazy fetch is one-shot via `useEffect`, gated on `isReady` (see below).
- **Do not re-introduce `useQuery(['nodeSpec', type])`** anywhere -- N consumers would create N observers, all woken on every cache write.
- **Critical: any cache entry consumed via `useSyncExternalStore` MUST set `gcTime: GC_TIME.FOREVER`** ([lib/queryConfig.ts](./client/src/lib/queryConfig.ts)). Slice subscribers don't register as observers, so without this override TanStack garbage-collects the entry after `GC_TIME.DEFAULT` (5 min) and every consumer reads `undefined`. Symptom: canvas nodes lose their icons + handles after idling on the page. Applies to `fetchNodeSpec`, `fetchNodeGroups`, `useNodeGroups`. The persistor in `lib/queryPersist.ts` only handles cross-reload survival, not in-session GC.

### `nodeStatusStore` for high-frequency state ([client/src/stores/nodeStatusStore.ts](./client/src/stores/nodeStatusStore.ts))
- Per-workflow node statuses live in a Zustand store (built on `useSyncExternalStore`). `useNodeStatus(id)` is a slice selector -- only the affected node's consumers re-render on a status tick.
- Mirror this pattern when adding any new high-frequency push state. Do **not** put it on `WebSocketContext.value` -- that's a context fan-out trap.

### `useAppStore` reads must be slice selectors, never whole-store destructure
- Always `const x = useAppStore((s) => s.x)`, never `const { x } = useAppStore()`. The whole-store form re-renders the consumer on ANY mutation (sidebar toggle, unrelated workflow rename, parameter save on another node), which defeats `React.memo` + `nodePropsEqual` on the canvas. Setters are stable refs from Zustand — single-field selectors are the cheapest read.
- Audited and converted across the canvas + parameter-panel hot paths: every node component, `Dashboard.tsx`, `useDragVariable`, `useParameterPanel`, `useReactFlowNodes`, `useWorkflowManagement`, `InputSection`, `MiddleSection`, `OutputPanel`, `ParameterRenderer`, `ToolSchemaEditor`, `ParameterPanel`, `InputNodesPanel`. New code should follow.

### `isOpen` vs `isReady` -- gate every catalogue/spec query on `isReady` ([WebSocketContext.tsx](./client/src/contexts/WebSocketContext.tsx))
- `isOpen` flips when the socket opens. `isReady` flips only after the init burst (api-key probes, terminal / chat / console history) settles.
- The init burst runs **in parallel** via `Promise.allSettled`: 5 `probeApiKey(provider)` calls + `loadTerminalLogs()` + `loadChatHistory()` + `loadConsoleLogs()`, each owning its own request id, message handler, 5 s timeout, and state write via a small `sendBurstRequest` factory. Time-to-`isReady` is one wide round-trip, not 8 sequential ones. `drainPendingSends(ws)` still runs synchronously after the await and before `setIsReady(true)` so the queue replay ordering is preserved.
- Queries that depend on backend-served catalogue data (`useCatalogueQuery`, `useNodeParamsQuery`, `useUserSettingsQuery`, `useNodeGroups`, `useNodeSpec` lazy fetch, prefetch effect) gate on `isReady` so they fire once, post-burst, instead of racing the parallel init helpers.
- `WebSocketContext.value` is `useMemo`'d -- consumers only re-render when an actual field they read changes. Pending requests are rejected on `ws.onclose` so retries fire immediately on the new socket instead of waiting the 30 s `REQUEST_TIMEOUT`.

### Catalogue invalidation is debounced
- `invalidateCatalogue(queryClient)` in [`hooks/useCatalogueQuery.ts`](./client/src/hooks/useCatalogueQuery.ts) wraps `queryClient.invalidateQueries({ queryKey: CATALOGUE_QUERY_KEY })` with a 300 ms trailing-edge debounce via a single shared module-scope timer. **Always go through it** from broadcast handlers — direct `invalidateQueries` calls were the old pattern.
- All 8 broadcast handlers in `WebSocketContext.tsx` (`api_key_status`, `whatsapp_status`, `twitter_oauth_complete`, `google_oauth_complete`, `google_status`, `telegram_status`, `credential_catalogue_updated`, `initial_status`) now route through it. An OAuth burst or multi-service reconnect collapses to one refetch instead of N back-to-back round-trips.

### React.memo every canvas node component ([client/src/components/nodeMemoEquality.ts](./client/src/components/nodeMemoEquality.ts))
- React Flow's documented requirement. Use the shared `nodePropsEqual` comparator -- it skips drag-state props (`xPos` / `yPos` / `dragging`) so the memo isn't defeated during drag.
- Applies to `SquareNode`, `AIAgentNode`, `TriggerNode`, `GenericNode`, `ToolkitNode`, `StartNode`, `TeamMonitorNode`. Add new node components the same way.
- Reference: https://reactflow.dev/learn/advanced-use/performance

### Icon + color — per-plugin folder + visuals.json fallback

Plugin icons and colors live co-located in the plugin folder; `visuals.json` is the fallback registry for emoji / library icons and the skill reverse-map. Full resolution chain (per-node-type → shared → fallback) is documented in [server/nodes/README.md → Icon + color](./server/nodes/README.md) and [docs-internal/plugin_system.md](./docs-internal/plugin_system.md).

Backend endpoints serve SVGs at `GET /api/schemas/nodes/<type>/icon` (plugin icons) and `GET /api/schemas/credentials/<provider>/icon` (credential brand icons). Frontend resolver at [client/src/assets/icons/index.ts](./client/src/assets/icons/index.ts) dispatches `lib:brand` / URL passthrough / emoji / dead-code `asset:<key>`.

**Do not** declare `icon` / `color` as class attributes on a node (the override path was removed in F1). Drop `icon.svg` (or `icon_<nodeType>.svg` for multi-node folders like whatsapp) and `meta.json` into the plugin folder. SKILL.md icon/color resolves from the first node in `allowed-tools` — only orphan skills keep inline `metadata.icon` / `metadata.color`.

## Key Files & Components

### Core Types
- `src/types/INodeProperties.ts` - Core interfaces for n8n-inspired node properties system
- `src/types/NodeTypes.ts` - Legacy compatibility types (NodeParameter, NodeOutput)

### Node System
Node metadata is SSOT on the backend after Wave 11. Each node is a Python
plugin at `server/nodes/<category>/<plugin>/__init__.py` that emits a `NodeSpec` via
the registry. The frontend fetches specs through
[`client/src/lib/nodeSpec.ts`](./client/src/lib/nodeSpec.ts) and adapts
them via [`client/src/adapters/nodeSpecToDescription.ts`](./client/src/adapters/nodeSpecToDescription.ts).
See [`docs-internal/plugin_system.md`](./docs-internal/plugin_system.md)
and [`server/nodes/README.md`](./server/nodes/README.md) for the plugin
authoring model.

- `src/lib/nodeSpec.ts` - TanStack-Query-backed spec fetch, `resolveNodeDescription`, `listCachedNodeSpecs`, group lookup
- `src/lib/aiModelProviders.ts` - Frontend-only AI provider icon/credential map
- `src/adapters/nodeSpecToDescription.ts` - Backend `NodeSpec` → legacy `INodeTypeDescription` shape
- `src/services/executionService.ts` - Node execution routed through the backend WebSocket layer

### Assets
- `src/assets/icons/google/` - Official Google service SVG icons (Gmail, Calendar, Drive, Sheets, Tasks, Contacts) using n8n pattern with data URI exports

### UI Components
- `src/components/ParameterRenderer.tsx` - Universal parameter renderer (also handles AI-specific control rendering; the former `AIParameterRenderer.tsx` was absorbed here)
- `src/components/parameterPanel/MiddleSection.tsx` - Parameter panel middle section with conditional display logic
- `src/components/OutputPanel.tsx` - Connected node output display with drag mapping
- `src/components/LocationParameterPanel.tsx` - Location-specific parameter handling
- `src/components/AIAgentNode.tsx` - Spec-driven agent canvas component. Reads `useNodeSpec(type)` for handles / icon / colour / displayName / uiHints; renders any plugin whose backend `component_kind` is `"agent"` or `"chat"`. No `AGENT_CONFIGS` map.
- `src/ParameterPanel.tsx` - Main parameter configuration modal

### AI Chat Model Components
AI model nodes route through `SquareNode` via `Dashboard.tsx`'s `COMPONENT_BY_KIND['model']` lookup. Per-provider visual data (icon, color, displayName) comes from the backend `NodeSpec` declared in `server/nodes/model/<provider>_chat_model.py`. The pre-Wave-11 per-provider wrappers (`BaseChatModelNode`, `OpenAIChatModelNode`, `ClaudeChatModelNode`, `GeminiChatModelNode`, `ModelNode`) were deleted -- nothing imported them after the migration.
- `src/services/apiKeyManager.ts` - Secure API key storage and validation with LangChain

### Specialized UI
- `src/components/ui/MapSelector.tsx` - Interactive location picker
- `src/components/ui/OutputDisplayPanel.tsx` - Execution result display
- `src/components/ui/ComponentPalette.tsx` - Searchable component library with emoji icons and dracula-themed category colors. Categories: Workflow, Triggers, AI Agents, AI Models, AI Skills, AI Abilities, AI Tools, Google Maps, Social Media Platforms (merged WhatsApp + Social), Android, Chat, Code Executors
- `src/components/ui/ComponentItem.tsx` - Draggable node items with hover effects and icon rendering
- `src/components/ui/CodeEditor.tsx` - Syntax-highlighted code editor using react-simple-code-editor + prismjs with centralized theming

### Hooks & State
- `src/hooks/useParameterPanel.ts` - Parameter management via WebSocket
- `src/hooks/useExecution.ts` - Node execution via WebSocket
- `src/hooks/useApiKeys.ts` - API key management via WebSocket
- `src/hooks/useAndroidOperations.ts` - Android device operations via WebSocket
- `src/hooks/useWhatsApp.ts` - WhatsApp operations via WebSocket
- `src/hooks/useDragAndDrop.ts` - Drag-and-drop functionality
- `src/hooks/useComponentPalette.ts` - Component palette state with localStorage persistence
- `src/store/useAppStore.ts` - Zustand application state with localStorage persistence for UI settings

### Theme System

Single source of truth: [client/src/index.css](./client/src/index.css). All tokens are HSL channel triplets (no `hsl()` wrapper) so Tailwind composes alpha via `bg-primary/50`, `text-foreground/80`, etc.

**Token tiers** (pick the most specific that fits):

1. **shadcn semantic tokens** — `background`, `foreground`, `card`, `popover`, `primary`, `secondary`, `muted`, `accent`, `destructive`, `success`, `warning`, `info`, `border`, `input`, `ring`. Each rotates per theme.
2. **Node-type role tokens** — `--node-agent`, `--node-model`, `--node-skill`, `--node-tool`, `--node-trigger`, `--node-workflow`. Each exposes three variants:
   - `bg-node-X` / `text-node-X` — solid (icons, badges, accents)
   - `bg-node-X-soft` — tinted surface (cards, tinted backgrounds)
   - `border-node-X-border` — tinted outline
   Themes redefine these in their own scope without touching call sites. **Never use opacity arithmetic at the call site** (`bg-node-agent/10`); add a new `-soft` / `-border` variant if you need a different opacity.
3. **Action role tokens** — `--action-run`, `--action-stop`, `--action-save`, `--action-config`, `--action-secret`, `--action-tools`. Same `base / -soft / -hover / -border` quartet as `--node-X` (e.g. `bg-action-run-soft text-action-run border-action-run-border`, plus `hover:bg-action-run-hover` for the hover state). Used for toolbar icon buttons, File menu items, and as the underlying tokens behind `<ActionButton>`'s `intent` variants. Themes redefine these without touching call sites. The `-hover` triplet means ActionButton no longer composes hover via opacity arithmetic.
4. **Dracula raw accents** — `--dracula-green/purple/pink/cyan/red/orange/yellow`. Same value across light + dark themes. Used as the underlying palette that `--action-X` and `--node-X` reference; do not consume directly in components — go through the semantic role token instead.

**Action buttons** — use [ActionButton](./client/src/components/ui/action-button.tsx) (CVA primitive with semantic `intent` variants: `run | stop | save | config | secret | tools`). Each intent reads the matching `--action-X` quartet (`-soft` for resting bg, `-hover` for hover bg, `-border` for outline, base for text). Disabled state is the shadcn-idiomatic `disabled:opacity-50` on the base class (one rule, all intents) — never per-token opacity arithmetic. Replaces the old `actionButtonStyle()` helper. Credential-modal panels (`OAuthConnect`, `EmailPanel`, `QrPairingPanel`, `ActionBar`) consume `<ActionButton>` directly; their `ActionDef` carries an `intent` key, not a free-form colour.

**Canvas-wide animations** — [client/src/styles/canvasAnimations.ts](./client/src/styles/canvasAnimations.ts) owns the `@keyframes` + `.react-flow__edge.{status}` + `.react-flow__node.{status}` rules injected once into Dashboard's `<style>` tag. Three named groups (`KEYFRAMES`, `edgeStatusStyles`, `nodeStatusStyles`) -- adding a new keyframe or status visual is a single-file change. Per-node inline animations (border pulse on `isExecuting`, etc.) live in their components and read theme tokens directly. Light/dark distinction lives entirely in `theme.ts` — `buildCanvasStyles(colors)` is single-arg and the file ships zero hardcoded hex colours; `CanvasStatusColors` carries `edgeDefault | edgeSelected | edgeExecuting | edgeCompleted | edgeError | edgePending | edgeMemoryActive | edgeToolActive`. The `nodeGlow` keyframe consumes scoped `--node-glow` / `--node-glow-soft` vars so one keyframe serves both themes.

**shadcn primitives** — full set under `client/src/components/ui/` (Button, Badge, Alert, Dialog, DropdownMenu, Select, Popover, Tooltip, Tabs, Card, Input, Textarea, Switch, Checkbox, Slider, Label, Form, Sonner, Skeleton, Collapsible, Accordion, AlertDialog). New code composes these with Tailwind classes.

**`useAppTheme()`** ([src/styles/theme.ts](./client/src/styles/theme.ts), [src/hooks/useAppTheme.ts](./client/src/hooks/useAppTheme.ts)) is grandfathered for the canvas node components (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`, `GenericNode`) and `EdgeConditionEditor` — they interpolate per-definition `nodeColor` into gradients, borders, and React Flow `<Handle>` styles. Credential-modal panels (`OAuthConnect`, `EmailPanel`, `QrPairingPanel`) and the skill/tool editors (`SkillEditorModal`, `ToolSchemaEditor`) no longer call it — they compose `<ActionButton intent="...">` plus shadcn semantic tokens (`bg-warning/10`, `bg-accent/10`, `<Alert variant="destructive">`). Every other surface uses Tailwind + the tokens above.

**Theme switching** — `[data-theme="dark"]` + Tailwind's `.dark` class, both set by `App.tsx`'s `useEffect` on `isDarkMode`. See [docs-internal/frontend_architecture.md](./docs-internal/frontend_architecture.md#tokens--theming) for the full token table and migration patterns.

### WebSocket-First Architecture
The project uses WebSocket as the primary communication method between frontend and backend, replacing most REST API calls:
- `src/contexts/WebSocketContext.tsx` - Central WebSocket context with request/response pattern
- `server/routers/websocket.py` - WebSocket endpoint; the live handler set is the `MESSAGE_HANDLERS` dict plus plugin-registered handlers via `services.ws_handler_registry`. Don't hand-maintain a count here.
- `server/services/status_broadcaster.py` - Connection management and broadcasting

**Canvas mutations from the backend** -- any handler that needs to add / move / delete nodes or edges (auto-add-skill on tool connect, Agent Builder runtime tools called by the LLM mid-execution, future workflow-template features) returns a workflow-ops batch (`{operations: [...]}`) and the frontend applies it through `applyOperations` in [client/src/lib/workflowOps.ts](./client/src/lib/workflowOps.ts). Backend builders live in [server/services/workflow_ops.py](./server/services/workflow_ops.py). Two delivery modes: request/response (frontend-driven, e.g. auto-skill) and push broadcast (`send_custom_event('workflow_ops_apply', ...)`, picked up by `useWorkflowOpsListener`). Full spec: [docs-internal/workflow_ops_protocol.md](./docs-internal/workflow_ops_protocol.md).

## Implemented Node Types

> **Authoritative source: backend plugin registry.** Glob [`server/nodes/**/*.py`](./server/nodes/) (excluding `_*.py` helpers and `__init__.py`) for the live count. The per-node descriptions below are reference material — they drift on every plugin add and should be cross-checked against [`server/nodes/README.md`](./server/nodes/README.md), the per-domain docs in `docs-internal/`, and the actual plugin classes before relying on any specific detail.

### AI Chat Models (9 nodes)
- **openaiChatModel**: OpenAI GPT models with response format options. O-series models (o1, o3, o4) support reasoning effort parameter.
- **anthropicChatModel**: Claude models with extended thinking support (budget_tokens for claude-3-5-sonnet, claude-3-opus)
- **geminiChatModel**: Google Gemini models with multimodal capabilities, safety settings, and thinking support for 2.5/Flash Thinking models
- **openrouterChatModel**: OpenRouter unified API - access 200+ models from OpenAI, Anthropic, Google, Meta, Mistral, and more through a single API. Features free/paid model grouping in dropdown.
- **groqChatModel**: Groq ultra-fast inference with Llama, Qwen3, and GPT-OSS models. Qwen3-32b supports reasoning_format.
- **cerebrasChatModel**: Cerebras ultra-fast inference on custom AI hardware with Llama and Qwen models
- **deepseekChatModel**: DeepSeek V3 models (deepseek-chat, deepseek-reasoner). Reasoner has always-on Chain-of-Thought with reasoning_content in response. 128K context, up to 64K output.
- **kimiChatModel**: Kimi K2 models by Moonshot AI (kimi-k2.5, kimi-k2-thinking). 256K context, 96K output. Thinking on by default for k2.5 (explicitly disabled for tool-calling agent compatibility). Fixed temperature: 0.6 (instant) / 1.0 (thinking).
- **mistralChatModel**: Mistral AI models (mistral-large-latest, mistral-small-latest, codestral-latest). Up to 256K context. No thinking support. Temperature 0-1.5.

### AI Agents & Memory (3 nodes)
- **aiAgent**: Advanced AI agent with tool calling, memory input handle, and iterative reasoning. Uses the plain-async `_run_agent_loop` for structured execution. Parameters: Provider, Model, Prompt, System Message, Options.
- **chatAgent**: Conversational AI agent with memory and skill support for multi-turn chat interactions. Parameters: Provider, Model, Prompt (supports `{{chatTrigger.message}}` template or auto-fallback from connected input), System Message. Behavior extended by connected skills.
### AI Agent Tool Nodes (6 dedicated + 10 dual-purpose)
Tool nodes connect to AI Agent's `input-tools` handle to provide capabilities the agent can invoke during reasoning. Both `masterSkill` and `simpleMemory` are in the AI Tools category.

#### Dedicated Tool Nodes (passive, tool-only)
- **masterSkill**: Master Skill (icon: target) - Aggregates multiple skills with enable/disable toggles. Split-panel UI: left panel shows skill list with checkboxes, right panel shows selected skill's markdown editor. Supports both built-in skills (from `server/skills/` folders) and user-created skills (stored in database). User skills can be created/edited/deleted inline via the "+" button.
- **simpleMemory**: Markdown-based conversation memory with editable UI, window-based trimming, and optional vector DB for long-term semantic retrieval

#### Skill Node Architecture
Skills are organized in subfolders under `server/skills/`. Each top-level folder appears as an option in the Master Skill node's folder dropdown. See **[Skill Creation Guide](./server/skills/GUIDE.md)** for full documentation.

```
server/skills/
├── GUIDE.md                              # Skill creation guide
├── assistant/                            # General-purpose assistant skills
│   ├── agent-builder-skill/SKILL.md      # 5 canvas-mutation ops for live tool growth
│   ├── assistant-personality/SKILL.md
│   ├── compaction-skill/SKILL.md
│   ├── humanify-skill/SKILL.md
│   ├── memory-skill/SKILL.md
│   ├── subagent-skill/SKILL.md
│   └── write-todos-skill/SKILL.md        # Task planning with plan-work-update loop
├── android_agent/                        # Android device control skills
│   ├── personality/SKILL.md
│   ├── battery-skill/SKILL.md
│   ├── wifi-skill/SKILL.md
│   └── ... (12 skills total)
├── autonomous/                           # Autonomous agent patterns
│   ├── code-mode-skill/SKILL.md
│   ├── agentic-loop-skill/SKILL.md
│   ├── progressive-discovery-skill/SKILL.md
│   ├── error-recovery-skill/SKILL.md
│   └── multi-tool-orchestration-skill/SKILL.md
├── coding_agent/                         # Code execution & filesystem skills
│   ├── python-skill/SKILL.md
│   ├── javascript-skill/SKILL.md
│   ├── file-read-skill/SKILL.md
│   ├── file-modify-skill/SKILL.md
│   └── fs-search-skill/SKILL.md
├── productivity_agent/                   # Google Workspace skills
│   ├── gmail-skill/SKILL.md              # Send, search, read emails
│   ├── calendar-skill/SKILL.md           # Create, list, update, delete events
│   ├── drive-skill/SKILL.md              # Upload, download, list, share files
│   ├── sheets-skill/SKILL.md             # Read, write, append spreadsheet data
│   ├── tasks-skill/SKILL.md              # Create, list, complete tasks
│   └── contacts-skill/SKILL.md           # Create, list, search contacts
├── social_agent/                         # Social media platform skills
│   ├── twitter-send-skill/SKILL.md
│   ├── twitter-search-skill/SKILL.md
│   ├── twitter-user-skill/SKILL.md
│   ├── whatsapp-send-skill/SKILL.md
│   └── whatsapp-db-skill/SKILL.md
├── task_agent/                           # Task management skills
│   ├── timer-skill/SKILL.md
│   ├── cron-scheduler-skill/SKILL.md
│   └── task-manager-skill/SKILL.md
├── travel_agent/                         # Location and maps skills
│   ├── geocoding-skill/SKILL.md
│   └── nearby-places-skill/SKILL.md
├── rlm_agent/                            # Recursive Language Model agent skills
│   └── rlm-reasoning-skill/SKILL.md
├── terminal/                             # Shell, process management, OS-specific terminals
│   ├── shell-skill/SKILL.md              # Sandboxed shell (no system PATH)
│   ├── process-manager-skill/SKILL.md    # Long-running processes (full PATH, streams output)
│   ├── bash-skill/SKILL.md               # Linux/macOS terminal patterns
│   ├── powershell-skill/SKILL.md         # Windows terminal patterns
│   └── wsl-skill/SKILL.md               # WSL bridge for Linux tools on Windows
└── web_agent/                            # Web automation skills
    ├── http-request-skill/SKILL.md
    ├── proxy-config-skill/SKILL.md
    ├── apify-skill/SKILL.md
    ├── crawlee-scraper-skill/SKILL.md
    ├── browser-skill/SKILL.md
    ├── duckduckgo-search-skill/SKILL.md
    ├── brave-search-skill/SKILL.md
    ├── serper-search-skill/SKILL.md
    └── perplexity-search-skill/SKILL.md
```

**SKILL.md Format:**
```yaml
---
name: skill-name
description: Brief description for LLM visibility
allowed-tools: tool1 tool2
metadata:
  author: machina
  version: "1.0"
  category: general
  icon: "🔧"
  color: "#6366F1"
---

# Skill Instructions (Markdown)
Detailed instructions loaded when skill is activated.
```

**Skill Content Lifecycle:**
1. First load reads from SKILL.md on disk, seeds to database
2. Database is source of truth after first activation
3. Users edit instructions in UI, edits saved to DB only
4. "Reset to Default" reloads from original SKILL.md file

#### Skill Content Editor
Skill nodes display an Ant Design `Input.TextArea` (markdown content) to view and edit instructions:
- Instructions loaded automatically when skill node is selected
- Save button writes changes back to database
- Uses `get_skill_content` / `save_skill_content` WebSocket handlers
- **No Input/Output panels**: Skill nodes only show the middle section (parameters + editor) in the parameter panel

**Key Files:**
| File | Description |
|------|-------------|
| `client/src/hooks/useParameterPanel.ts` | Loads/saves skill content for skill nodes |
| `server/services/skill_loader.py` | SkillLoader for filesystem and database skills |
| `server/routers/websocket.py` | `get_skill_content`, `save_skill_content`, `list_skill_folders`, `scan_skill_folder` handlers |

#### Master Skill Editor
The Master Skill node uses a custom split-panel editor (`MasterSkillEditor.tsx`) instead of standard parameters:

**UI Layout:**
```
+----------------------------------------------------------+
| [Folder Dropdown: assistant v]                            |
+----------------------------------------------------------+
| +--------------------+ +--------------------------------+ |
| | SKILLS LIST        | | SKILL INSTRUCTIONS             | |
| | [Badge: 3 enabled] | |                                | |
| | [Search skills...] | | # WhatsApp Skill               | |
| |                    | |                                | |
| | [x] WhatsApp       | | This skill provides WhatsApp   | |
| | [x] Memory         | | messaging capabilities...      | |
| | [ ] Android        | |                                | |
| | [x] Maps           | | [Reset to Default]             | |
| | [ ] HTTP           | |                                | |
| +--------------------+ +--------------------------------+ |
+----------------------------------------------------------+
```

**Folder Dropdown:**
- Ant Design `Select` listing available skill folders from backend via `list_skill_folders` WebSocket handler
- Shows loading/disabled state while folders are being fetched
- Uses `getPopupContainer` to ensure dropdown renders correctly regardless of parent overflow
- Default folder: `assistant`

**Icon Resolution Priority:**
Skill icons resolve via the central `visuals.json` registry — see "Icon + color — central `visuals.json` registry" below. The pre-Wave-10 `skillNodes.ts` + `getNodeDefaults()` helper that did per-skill icon overrides on the frontend was retired; icons + colours now live exclusively in [`server/nodes/visuals.json`](./server/nodes/visuals.json) and SKILL.md frontmatter for orphan skills (no node target).

**Keyboard Handling:**
The editor uses a native DOM `addEventListener('keydown')` on a wrapper div (via `useRef`) to `stopPropagation()` for Ctrl/Meta key events. This prevents React Flow's document-level `useKeyPress` hook from intercepting Ctrl+A (select all) and other Ctrl shortcuts inside the textarea. React synthetic `stopPropagation()` is insufficient because React Flow uses native `document.addEventListener`.

**Data Structure:**
```typescript
// skillsConfig parameter stored in node parameters
interface MasterSkillConfig {
  [skillName: string]: {
    enabled: boolean;        // Whether skill is active
    instructions: string;    // Custom or default SKILL.md content
    isCustomized: boolean;   // True if user modified instructions
  };
}
```

**skillsConfig Persistence:**
- `skillsConfig` persists skills from **all folders**, not just the currently selected one. Switching folders does not remove previously enabled skills from config.
- The backend (`handlers/ai.py`) handles stale config entries gracefully at execution time -- it checks `enabled`, tries to load instructions, and logs a warning if a skill file is missing. Stale disabled entries are simply skipped.
- No frontend cleanup of stale skills is performed to avoid race conditions with async skill loading.

**Custom Skill Creation:**
- The `create_user_skill` WebSocket handler requires `name`, `display_name`, and `instructions`. The `description` field is optional (defaults to empty string).

**Backend Expansion:**
When AI Agent executes with a connected Master Skill node, `_collect_agent_connections()` in `handlers/ai.py` expands the skillsConfig into individual skill entries:
```python
if skill_type == 'masterSkill':
    skills_config = skill_params.get('skillsConfig', {})
    for skill_key, skill_cfg in skills_config.items():
        if skill_cfg.get('enabled', False):
            # Load from customized or skill folder
            instructions = skill_cfg.get('instructions') if skill_cfg.get('isCustomized') else skill_loader.load_skill(skill_key).instructions
            skill_data.append({
                'node_id': f"{source_node_id}_{skill_key}",
                'node_type': 'masterSkill',
                'skill_name': skill_key,
                'parameters': {'instructions': instructions},
                'label': skill_key
            })
```

**Key Files:**
| File | Description |
|------|-------------|
| `client/src/components/parameterPanel/MasterSkillEditor.tsx` | Split-panel skill aggregator UI with inline user skill CRUD |
| `server/routers/websocket.py` | User skill CRUD: `get_user_skills`, `create_user_skill`, `update_user_skill`, `delete_user_skill` |
| `server/core/database.py` | UserSkill model and database CRUD methods |
| `server/services/handlers/ai.py` | Expands masterSkill into individual skills at execution |
| `server/skills/GUIDE.md` | Skill creation guide for built-in skills |

#### Other Dedicated Tool Nodes
- **calculatorTool**: Mathematical operations (add, subtract, multiply, divide, power, sqrt, mod, abs)
- **currentTimeTool**: Get current date/time with timezone support
- **duckduckgoSearch**: DuckDuckGo web search (free, uses `ddgs` library, no API key required)
- **taskManager**: Task management tool for AI agents to create, track, and manage tasks
- **writeTodos**: Structured task list planning for complex multi-step operations. Connects to any agent's `input-tools` handle. Dual output (`tool` + `main`), dracula purple `#bd93f9`. Backed by `TodoService` singleton (`server/services/todo_service.py`) with JSON-based per-session state keyed by workflow_id. Schema: `WriteTodosSchema` with `TodoItem`/`TodoStatus` Pydantic enum (`pending` | `in_progress` | `completed`). Handler broadcasts `phase: "todo_update"` via WebSocket for real-time UI; `formatTodoOutput()` in `OutputDisplayPanel.tsx` renders as a checklist. Skill: `server/skills/assistant/write-todos-skill/SKILL.md` teaches the plan-work-update loop.

#### Dual-Purpose Search Nodes (workflow node + AI tool)
Search API nodes that work BOTH as standalone workflow nodes AND as AI Agent tools. When connected to `input-tools`, the LLM fills the node's parameter schema.
- **braveSearch**: **Dual-purpose node** - Search the web using Brave Search API. Returns web results with titles, snippets, and URLs. Group: `['search', 'tool']`. Parameters: query, maxResults, country, searchLang, safeSearch.
- **serperSearch**: **Dual-purpose node** - Search the web using Google via Serper API. Supports web, news, images, and places search types with knowledge graph. Group: `['search', 'tool']`. Parameters: query, searchType, maxResults, country, language.
- **perplexitySearch**: **Dual-purpose node** - AI-powered search using Perplexity Sonar. Returns a markdown-formatted AI answer with inline citation references and source URLs. Group: `['search', 'tool']`. Parameters: query, model (sonar/sonar-pro/sonar-reasoning/sonar-reasoning-pro), searchRecencyFilter, returnImages, returnRelatedQuestions.

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/handlers/search.py` | 3 handler functions with API key fetch + usage tracking |
| `server/services/handlers/tools.py` | Tool dispatch wrappers for AI Agent tool calling |
| `server/services/ai.py` | Tool names, descriptions, and Pydantic schemas |
| `server/constants.py` | `SEARCH_NODE_TYPES` and `SEARCH_TOOL_TYPES` constants |
| `client/src/assets/icons/search/` | SVG icons for Brave, Serper, Perplexity |

**Search API Authentication:**
| Provider | Credential Key | Header | API Endpoint |
|----------|---------------|--------|-------------|
| Brave Search | `brave_search` | `X-Subscription-Token` | `GET https://api.search.brave.com/res/v1/web/search` |
| Serper | `serper` | `X-API-KEY` | `POST https://google.serper.dev/search` |
| Perplexity | `perplexity` | `Authorization: Bearer` | `POST https://api.perplexity.ai/chat/completions` |

**Credentials Modal Layout:**
- Brave Search and Perplexity in **Search** category
- Serper in **Scrapers** category (Google SERP scraping)

### Specialized AI Agents (15 nodes)
Specialized agents are AI Agents pre-configured for specific domains. They inherit full AI Agent functionality (provider, model, prompt, system message, thinking/reasoning) while being tailored for specific capabilities. All specialized agents dispatch through `BaseNode.execute()` via the node registry (Wave 11) and support the same input handles. Node colors use centralized dracula theme constants imported from `client/src/styles/theme.ts`.

**Input Handles:**
- `input-main` - Main data input (auto-prompting fallback)
- `input-skill` - Skill nodes (including Master Skill for aggregated skills)
- `input-memory` - Memory node for conversation history
- `input-tools` - Tool nodes for LLM tool calling
- `input-task` - Task completion events from taskTrigger nodes

**Specialized Agent Types:**
- **android_agent**: Android Control Agent - AI agent for Android device control. Connect Android service nodes (battery, wifi, bluetooth, apps, location, camera, sensors) as tools.
- **coding_agent**: Coding Agent - AI agent for code execution. Connect code executor nodes (Python, JavaScript) as tools.
- **web_agent**: Web Control Agent - AI agent for web automation. Connect web/browser nodes (scraper, HTTP, browser) as tools.
- **task_agent**: Task Management Agent - AI agent for task automation. Connect scheduling nodes (scheduler, reminders) as tools.
- **social_agent**: Social Media Agent - AI agent for social messaging. Connect messaging nodes (WhatsApp, Telegram) as tools.
- **travel_agent**: Travel Agent - AI agent for travel planning. Connect location, maps, and scheduling nodes as tools.
- **tool_agent**: Tool Agent - AI agent for tool orchestration. Connect any combination of tool nodes for flexible automation.
- **productivity_agent**: Productivity Agent - AI agent for productivity workflows. Connect scheduling, task, and utility nodes as tools.
- **payments_agent**: Payments Agent - AI agent for payment processing. Connect payment, invoice, and financial tool nodes.
- **consumer_agent**: Consumer Agent - AI agent for consumer interactions. Connect customer support, product, and order management tools.
- **autonomous_agent**: Autonomous Agent - AI agent for autonomous operations using Code Mode patterns. Uses agentic loops, progressive discovery, error recovery, and multi-tool orchestration for 81-98% token savings. Connect autonomous skills via Master Skill.
- **orchestrator_agent**: Orchestrator Agent - Team lead agent for coordinating multiple agents. Connect specialized agents via `input-teammates` handle; they become `delegate_to_*` tools the AI can invoke.
- **ai_employee**: AI Employee - Team lead agent similar to orchestrator_agent. Connect specialized agents via `input-teammates` handle for intelligent task delegation.
- **rlm_agent**: RLM Agent - Recursive Language Model agent using REPL-based code execution with recursive LM calls. Replaces the standard tool-calling loop with RLM's `exec()` REPL loop (`llm_query()`, `rlm_query()`, `FINAL()`). Routes to dedicated `handle_rlm_agent` handler and `RLMService` (not `handle_chat_agent`). Connect AI chat model nodes as small LMs for depth>=1 calls. See `docs-internal/rlm_service.md`.

**Backend Routing:**
Specialized agents are detected by `SPECIALIZED_AGENT_TYPES` and dispatched through `BaseNode.execute()` via the node registry (Wave 11). Dedicated engine: `rlm_agent` -> `RLMService`.
```python
SPECIALIZED_AGENT_TYPES = {
    'android_agent', 'coding_agent', 'web_agent', 'task_agent', 'social_agent',
    'travel_agent', 'tool_agent', 'productivity_agent', 'payments_agent', 'consumer_agent',
    'autonomous_agent', 'orchestrator_agent', 'ai_employee',
}
```

**Team Lead Types (Agent Teams Pattern):**
Team leads (`orchestrator_agent`, `ai_employee`) have a special `input-teammates` handle. Agents connected to this handle become delegation tools:
```python
# In handlers/ai.py
TEAM_LEAD_TYPES = {'orchestrator_agent', 'ai_employee'}

# Teammates become delegate_to_* tools automatically
if node_type in TEAM_LEAD_TYPES:
    teammates = await _collect_teammate_connections(node_id, context, database)
    if teammates:
        for tm in teammates:
            tool_data.append({
                'node_id': tm['node_id'],
                'node_type': tm['node_type'],  # e.g., 'coding_agent'
                'label': tm['label'],
            })
        # AI now has delegate_to_coding_agent, delegate_to_web_agent, etc.
```

**Direct Android Service Tools:**
Android service nodes (batteryMonitor, wifiAutomation, etc.) can be connected directly to any agent's `input-tools` handle. The backend maps camelCase node types to snake_case service IDs:
```python
# In handlers/tools.py
service_id_map = {
    'batteryMonitor': 'battery',
    'wifiAutomation': 'wifi_automation',
    'bluetoothAutomation': 'bluetooth_automation',
    # ... etc
}
```

#### Dual-Purpose Tool Nodes (workflow node + AI tool)
Nodes that work BOTH as standalone workflow nodes AND as AI Agent tools. When connected to `input-tools`, the LLM fills the node's parameter schema.
- **whatsappSend**: Send WhatsApp messages (text, media, location, contact) to contacts, groups, or newsletter channels. Full schema for all message types.
- **whatsappDb**: Query WhatsApp database - chat history, contacts, groups, and newsletter channels with filtering and pagination.
- **pythonExecutor**: Execute Python code for calculations, data processing, and automation. Tool name: `python_code`. Available: math, json, datetime, Counter, defaultdict, random.
- **javascriptExecutor**: Execute JavaScript code via persistent Node.js server for calculations, data processing, and JSON manipulation. Tool name: `javascript_code`.
- **typescriptExecutor**: Execute TypeScript code via persistent Node.js server with type safety. Tool name: `typescript_code`.
- **gmaps_locations**: Google Maps Geocoding service for address-to-coordinates conversion. Tool name: `geocode`.
- **gmaps_nearby_places**: Google Places API nearbySearch. Tool name: `nearby_places`.

See [plugin_system.md](./docs-internal/plugin_system.md) for the dual-purpose pattern (mark `group: ['category', 'tool']` and set `usable_as_tool = True` on the plugin class); whatsapp / twitter / google plugin folders are the live examples.

### Location Services (3 nodes)
- **gmaps_create**: Google Maps creation with customizable center, zoom, and map type (display only, not a tool)
- **gmaps_locations**: **Dual-purpose** - Google Maps Geocoding service for address-to-coordinates conversion
- **gmaps_nearby_places**: **Dual-purpose** - Google Places API nearbySearch with detailed place information

#### Google Maps API Key Resolution
The MapsSection component fetches the Google Maps API key from backend credentials:
```typescript
// In MapsSection.tsx
const { getStoredApiKey, isConnected } = useApiKeys();
const [apiKey, setApiKey] = useState<string | undefined>(() => getGoogleMapsApiKey()); // Env fallback
const hasFetchedRef = useRef(false);

useEffect(() => {
  if (!isConnected || hasFetchedRef.current) return;
  const fetchApiKey = async () => {
    hasFetchedRef.current = true;
    const storedKey = await getStoredApiKey('google_maps');
    if (storedKey) setApiKey(storedKey);
  };
  fetchApiKey();
}, [isConnected, getStoredApiKey]);
```
- Falls back to environment variable if no stored key
- Uses `hasFetchedRef` to prevent multiple fetches
- Only fetches when WebSocket is connected

### Android Services (16 nodes)
Android device connection is configured via the Credentials Modal (Android panel), not via workflow nodes.

#### System Monitoring (4 nodes)
- **batteryMonitor**: Monitor battery status, level, charging state, temperature, and health
- **networkMonitor**: Monitor network connectivity, type, and internet availability
- **systemInfo**: Get device and OS information including Android version, API level, memory, and hardware details
- **location**: GPS location tracking with latitude, longitude, accuracy, and provider information

#### App Management (2 nodes)
- **appLauncher**: Launch applications by package name
- **appList**: Get list of installed applications with package names, versions, and metadata

#### Device Automation (6 nodes)
- **wifiAutomation**: WiFi control and scanning - enable, disable, get status, scan for networks
- **bluetoothAutomation**: Bluetooth control - enable, disable, get status, and paired devices
- **audioAutomation**: Volume and audio control - get/set volume, mute, unmute
- **deviceStateAutomation**: Device state control - airplane mode, screen on/off, power save mode, brightness
- **screenControlAutomation**: Screen control - brightness adjustment, wake screen, auto-brightness, screen timeout
- **airplaneModeControl**: Airplane mode status monitoring and control

#### Sensors (2 nodes)
- **motionDetection**: Accelerometer and gyroscope data - detect motion, shake gestures, device orientation
- **environmentalSensors**: Environmental sensors - temperature, humidity, pressure, light level

#### Media (2 nodes)
- **cameraControl**: Camera control - get camera info, take photos, camera capabilities
- **mediaControl**: Media playback control - volume control, playback control, play media files

### WhatsApp Nodes (3 nodes)
- **whatsappSend**: **Dual-purpose node** - Send WhatsApp messages (text, image, video, audio, document, sticker, location, contact) to contacts, groups, or newsletter channels. Works as workflow node OR AI Agent tool. Group: `['whatsapp', 'tool']`. Recipient types: Self (connected phone), Phone Number, Group, Channel (newsletter). Channel messages only support text, image, video, audio, document (NOT sticker, location, contact). **Format Markdown** toggle (default: true): converts GFM markdown to WhatsApp-native formatting via `markdown_formatter.to_whatsapp()`. Full parameter schema for message type, media URL, location coordinates, contact vCard, channel JID.
- **whatsappDb**: **Dual-purpose node** - Comprehensive WhatsApp database query node with 18 operations. Works as workflow node OR AI Agent tool. Group: `['whatsapp', 'tool']`. Operations:
  - `chat_history`: Retrieve messages from individual or group chats with filtering, pagination, and optional media download
  - `search_groups`: Search groups by name
  - `get_group_info`: Get group details with participant names and phone numbers
  - `get_contact_info`: Get full contact info (name, phone, profile picture) for sending/replying
  - `list_contacts`: List all contacts with saved names
  - `check_contacts`: Check WhatsApp registration status for phone numbers
  - `list_channels`: List subscribed newsletter channels with optional server refresh
  - `get_channel_info`: Get channel details (name, description, subscribers)
  - `channel_messages`: Get channel message history with pagination, date range, media type filter, text search, and optional media download
  - `channel_stats`: Get channel subscriber/view statistics
  - `channel_follow`: Follow/subscribe to a newsletter channel
  - `channel_unfollow`: Unfollow/unsubscribe from a newsletter channel
  - `channel_create`: Create a new newsletter channel (with optional picture)
  - `channel_mute`: Mute/unmute a newsletter channel
  - `channel_mark_viewed`: Mark channel messages as viewed
  - `newsletter_react`: React to a channel message with an emoji
  - `newsletter_live_updates`: Subscribe to live view/reaction counts for channel messages
  - `contact_profile_pic`: Get profile picture for a contact or group
- **whatsappReceive**: Event-driven trigger that waits for incoming WhatsApp messages with filters (message type, sender, group, channel, keywords, forwarded status). Marked with `['whatsapp', 'trigger']` group for n8n-style trigger identification. Stores group/sender names alongside JID/phone for display persistence. The Go RPC resolves LIDs to phone numbers before sending events - `sender_phone` field is already resolved. Filter options: All Messages, From Self (notes to self chat only), From Any Contact (Non-Group), From Specific Contact, From Specific Group, From Channel (Newsletter), Contains Keywords

### Social Nodes (2 nodes)
Unified social messaging nodes for multi-platform communication. Supports WhatsApp, Telegram, Discord, Slack, Signal, SMS, Webchat, Email, Matrix, Teams.

- **socialReceive**: Normalizes messages from platform triggers into unified format. Multiple outputs: Message, Media, Contact, Metadata. Filters by channel, message type, sender.
- **socialSend**: **Dual-purpose node** - Send messages to any supported platform. Works as workflow node OR AI Agent tool. Supports text, image, video, audio, document, sticker, location, contact, poll, buttons, list message types.

### Twitter/X Nodes (4 nodes)
Twitter/X integration using the official XDK Python SDK with OAuth 2.0 PKCE authentication. All sync XDK calls wrapped in `asyncio.to_thread()` to avoid blocking the event loop. Lazy token refresh on 401/403 errors instead of validating on every call.

- **twitterSend**: **Dual-purpose node** - Post tweets, reply, retweet, like/unlike, and delete tweets. Works as workflow node OR AI Agent tool. Group: `['social', 'tool']`. Actions: `tweet`, `reply`, `retweet`, `like`, `unlike`, `delete`. Parameters: action, text (280 char max), tweet_id, reply_to_id.
- **twitterSearch**: **Dual-purpose node** - Search recent tweets with rich data via X API v2 expansions. Returns enriched tweets with `display_text` (expanded URLs), `author` profile, `public_metrics` (likes/retweets/replies/quotes/bookmarks/impressions), `media` attachments, `referenced_tweets` (quoted/replied), `note_tweet` for long-form content. Works as workflow node OR AI Agent tool. Group: `['social', 'tool']`. `max_results` clamped to 10-100 (X API v2 minimum is 10). Supports query operators: keywords, hashtags (#), mentions (@), from:user, to:user, -exclude, OR, lang:, has:links, has:media, has:images, has:videos, is:retweet, -is:retweet, is:reply, is:quote, url:.
- **twitterUser**: **Dual-purpose node** - Look up user profiles and social connections with description and created_at. Works as workflow node OR AI Agent tool. Group: `['social', 'tool']`. Operations: `me` (get authenticated user), `by_username`, `by_id`, `followers` (max_results 1-1000), `following` (max_results 1-1000).
- **twitterReceive**: Event-driven trigger that waits for incoming Twitter events (mentions, DMs, timeline updates). Group: `['social', 'trigger']`. Polling-based since X API free tier lacks webhooks.

#### Twitter OAuth 2.0 Authentication
Authentication is handled via OAuth 2.0 PKCE flow in the Credentials Modal:
1. User clicks "Login with Twitter" button
2. Backend generates PKCE code challenge and authorization URL
3. Browser opens Twitter authorization page
4. User grants permission, Twitter redirects with auth code
5. Backend exchanges code for access_token + refresh_token
6. Tokens stored in database via auth_service

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/twitter_oauth.py` | OAuth 2.0 PKCE flow implementation |
| `server/services/oauth_utils.py` | Runtime OAuth redirect URI derivation from request context |
| `server/routers/twitter.py` | OAuth callback endpoint, token exchange |
| `server/services/handlers/twitter.py` | Node handlers using XDK SDK |
| `client/src/components/CredentialsModal.tsx` | Twitter panel with OAuth button |
| `server/skills/social_agent/twitter-*-skill/` | 3 Twitter skills for AI agents |

**Handler Architecture:**
- All sync XDK calls wrapped in `asyncio.to_thread()` (XDK uses sync `requests` internally)
- No `get_me()` validation on every call -- lazy token refresh on 401/403 via `_refresh_and_get_client()`
- Search uses full X API v2 expansions and returns enriched data with expanded URLs, author profiles, media, metrics, referenced tweets
- `max_results` clamped: search 10-100, followers/following 1-1000

**XDK SDK API Patterns:**
```python
from xdk import Client
client = Client(access_token=access_token)

client.posts.create(body={"text": "Hello world!"})
client.posts.create(body={"text": "Reply", "reply": {"in_reply_to_tweet_id": "123"}})
client.users.repost_post(user_id, body={"tweet_id": "123"})
client.users.like_post(user_id, body={"tweet_id": "123"})
client.users.unlike_post(user_id, tweet_id="123")
client.posts.delete(tweet_id)
client.posts.search_recent(query="...", max_results=100,
    tweet_fields=[...], expansions=[...], media_fields=[...], user_fields=[...])
client.users.get_me(user_fields=["created_at", "description"])
client.users.get_by_usernames(usernames=["user1"], user_fields=["description"])
client.users.get_followers(user_id, max_results=100, user_fields=["created_at"])
```

**Environment Variables:**
```bash
TWITTER_CLIENT_ID=your_client_id
TWITTER_CLIENT_SECRET=your_client_secret
# TWITTER_REDIRECT_URI is derived at runtime from request context (no env var needed)
```

### Telegram Nodes (2 nodes) — reference for self-contained plugin folders
Telegram bot integration using `python-telegram-bot` SDK with long-polling for incoming messages. **All telegram code lives in `server/nodes/telegram/`** — the plugin folder owns its service, WebSocket handlers, event filter, lifecycle hooks, and credentials. Read this folder first before adding any plugin that needs more than a single `BaseNode` subclass; see [docs-internal/plugin_system.md → "Self-contained plugin folders"](./docs-internal/plugin_system.md#self-contained-plugin-folders).

- **telegramSend**: Workflow-only ActionNode (dropped dual-tool support in `190dbb9` — `usable_as_tool=True` removed, group reduced from `('social', 'tool')` to `('social',)`). Send text, photo, document, location, or contact messages via Telegram bot. Recipient types: Self (bot owner), User/Chat ID, Group. Parameters: recipient_type, chat_id, message_type, text, media_url, caption, parse_mode (Auto/HTML/Markdown/MarkdownV2/None), silent, reply_to_message_id. **Auto parse_mode** (default, recommended): converts GFM markdown to Telegram HTML via `markdown_formatter.to_telegram_html()`. MarkdownV2/Markdown text auto-escaped via `_escape_text()`; falls back to plain text on `BadRequest`. "Self" restores `owner_chat_id` from credentials DB if not in memory.
- **telegramReceive**: Event-driven trigger that waits for incoming Telegram messages. Group: `['social', 'trigger']`. Uses `senderFilter` dropdown: All Messages, From Self (Bot Owner) (zero-config), Private/Group/Supergroup/Channel Only, Specific Chat, Specific User, Keywords. Content type filter and ignore bots option. "From Self" uses lazy `_get_owner_chat_id()` lookup at match time. Auto-reconnects on server restart via the registered service-refresh callback.

**Key Files** (all under `server/nodes/telegram/`):
| File | Description |
|------|-------------|
| `__init__.py` | Imports + 6 self-registration calls — the only wiring needed (zero logic). |
| `_credentials.py` | `TelegramCredential(ApiKeyCredential)` — bot token + `telegram_owner_chat_id` extra field. |
| `_service.py` | `TelegramService` singleton: connect/disconnect/send/poll lifecycle, idempotent connect, parse-mode fallback helper. |
| `_handlers.py` | 7 WebSocket handlers (`telegram_connect` / `_disconnect` / `_status` / `_send` / `_reconnect` / `_get_me` / `_get_chat`) + `WS_HANDLERS` dict registered into `services.ws_handler_registry`. |
| `_filters.py` | `build_telegram_filter` — registered into `event_waiter.FILTER_BUILDERS`. |
| `_refresh.py` | `refresh_telegram_status` (registered into `status_broadcaster._SERVICE_REFRESH_CALLBACKS`) + `precheck_telegram_trigger` (registered into `event_waiter._TRIGGER_PRECHECKS`). |
| `telegram_send.py` | `TelegramSendNode(ActionNode)` + `TelegramSendOutput` — the workflow + AI-tool plugin. |
| `telegram_receive.py` | `TelegramReceiveNode(TriggerNode)` + `TelegramReceiveOutput` — the trigger plugin (declares `event_type = "telegram_message_received"`). |
| `client/src/components/CredentialsModal.tsx` | Telegram bot token panel (frontend; identifies commands by WebSocket message-type strings, not Python paths). |

**Authentication:** Bot token from @BotFather on Telegram. Stored via `auth_service.store_api_key('telegram', token, models=[])` (renamed from `telegram_bot_token` — class id, catalogue key, and co-located SVG basename now all align with the brand name). **Owner detection has three independent layers** (commit f746ddf): (1) explicit `Your Chat ID (optional)` text input in the Telegram credentials modal — declared as a secondary `FieldDef` in `credential_providers.json` and rendered by `ApiKeyPanel`'s `SecondaryFieldRow`; saves directly to `telegram_owner_chat_id` via `auth_service.store_api_key`; (2) pre-poll peek in `connect()` via `_capture_owner_from_pending_updates()` — `bot.get_updates(timeout=0)` BEFORE `start_polling(drop_pending_updates=True)` discards the queue; (3) atomic write-through in `_on_message_received` — persists FIRST, sets in-memory only on success, ERROR-level logging on failure. Lazy fallback in `telegram_send.py` reads DB and calls `service.set_owner` if in-memory is empty. Credentials panel: Save (store only), Connect (store + connect), Reconnect. All credential access uses `from core.container import container; container.auth_service()` (NOT `from services.auth import get_auth_service`).

**Environment Variables:**
```bash
# Optional: Set owner chat ID for "Self" recipient type
TELEGRAM_OWNER_CHAT_ID=your_chat_id
```

### Markdown Formatter Service
Platform-specific markdown formatting using `markdown-it-py` (Python port of `markdown-it`, 18K+ GitHub stars). Converts GFM markdown (as produced by LLMs) to platform-native formats.

**Key File:** `server/services/markdown_formatter.py`

**Public Functions:**
| Function | Target | Description |
|----------|--------|-------------|
| `to_telegram_html(text)` | Telegram | Renders GFM to HTML, converts unsupported tags (`<h1>`-`<h6>` -> `<b>`, `<ul>/<li>` -> bullets, strips `<p>`) |
| `to_whatsapp(text)` | WhatsApp | Walks markdown-it token stream, maps to WhatsApp syntax (`*bold*`, `_italic_`, `~strike~`, `` ```code``` ``, `> quote`) |
| `to_plain(text)` | Any | Renders to HTML, strips all tags |

**Usage:**
- Telegram Send node: Auto parse_mode (default) calls `to_telegram_html()` via `TelegramService._format_auto()`
- WhatsApp Send node: `format_markdown` toggle (default: true) calls `to_whatsapp()` in handler
- No new dependencies -- `markdown-it-py` v4.0.0 already installed as transitive dependency

### Google Workspace Nodes (7 nodes)
Consolidated Google Workspace integration with 6 unified operation-based nodes + 1 polling trigger. Each service node uses an `operation` parameter to select the action (e.g., gmail with operation: send/search/read). All services share a single OAuth connection with combined scopes.

#### Consolidated Service Nodes (6 nodes)
- **gmail**: **Dual-purpose node** - Operations: `send`, `search`, `read`. Handler: `handle_google_gmail()` dispatcher.
- **calendar**: **Dual-purpose node** - Operations: `create`, `list`, `update`, `delete`.
- **drive**: **Dual-purpose node** - Operations: `upload`, `download`, `list`, `share`.
- **sheets**: **Dual-purpose node** - Operations: `read`, `write`, `append`.
- **tasks**: **Dual-purpose node** - Operations: `create`, `list`, `complete`, `update`, `delete`.
- **contacts**: **Dual-purpose node** - Operations: `create`, `list`, `search`, `get`, `update`, `delete`.

#### Trigger Nodes (1 node)
- **gmailReceive**: Polling-based trigger for incoming emails. Polls Gmail API at configurable interval (10-3600s). Parameters: `filter_query` (default: `is:unread`), `label_filter` (default: `INBOX`), `mark_as_read`, `poll_interval` (default: 60s). In deployment mode, uses `setup_polling_trigger` with baseline detection to avoid triggering on existing emails.

#### Google Workspace OAuth 2.0 Authentication
Authentication is handled via OAuth 2.0 flow in the Credentials Modal:
1. User enters Google Cloud Client ID and Secret (OAuth 2.0 credentials)
2. User clicks "Login with Google"
3. Consent screen shows all requested scopes (Gmail, Calendar, Drive, etc.)
4. User grants permission, Google redirects with auth code
5. Backend exchanges code for access_token + refresh_token
6. Tokens stored via auth_service (owner mode) or google_connections table (customer mode)

**Combined OAuth Scopes:**
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

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/google_oauth.py` | OAuth 2.0 flow (authorization URL, token exchange, `get_callback_paths()`) |
| `server/services/oauth_utils.py` | Runtime OAuth redirect URI derivation from request context |
| `server/services/handlers/google_auth.py` | Shared credential helper (`get_google_credentials()`) used by all 6 handlers |
| `server/config/google_apis.json` | API endpoints, scopes, and OAuth callback paths |
| `server/routers/google.py` | OAuth callback and status endpoints |
| `server/services/handlers/gmail.py` | Gmail handlers + `handle_gmail_receive` polling trigger |
| `server/services/handlers/calendar.py` | Calendar handlers |
| `server/services/handlers/drive.py` | Drive handlers |
| `server/services/handlers/sheets.py` | Sheets handlers |
| `server/services/handlers/tasks.py` | Tasks handlers |
| `server/services/handlers/contacts.py` | Contacts handlers |
| `server/models/database.py` | `GoogleConnection` model |
| `server/skills/productivity_agent/` | Google Workspace skills for AI agents |
| `client/src/assets/icons/google/` | Official Google service SVG icons (n8n pattern) |
| `client/src/components/CredentialsModal.tsx` | Google Workspace panel |

**Token Storage (Two Separate Systems):**

Client ID and Secret are stored as API keys (user enters manually):
| Key | Storage Method | Description |
|-----|---------------|-------------|
| `google_client_id` | `auth_service.get_api_key()` / `EncryptedAPIKey` table | OAuth 2.0 Client ID |
| `google_client_secret` | `auth_service.get_api_key()` / `EncryptedAPIKey` table | OAuth 2.0 Client Secret |

Access and refresh tokens are stored via OAuth system (after Google login):
| Field | Storage Method | Description |
|-------|---------------|-------------|
| `access_token` | `auth_service.get_oauth_tokens("google")` / `EncryptedOAuthToken` table | Access token for API calls |
| `refresh_token` | `auth_service.get_oauth_tokens("google")` / `EncryptedOAuthToken` table | Refresh token for renewal |
| `email`, `name` | `auth_service.get_oauth_tokens("google")` / `EncryptedOAuthToken` table | Connected user info |

**IMPORTANT**: All handlers use `get_google_credentials()` from `google_auth.py`, which reads tokens via `get_oauth_tokens()`. Never use `get_api_key("google_access_token")` — that reads from the wrong table.

**AI Agent Skills (productivity_agent folder):**
| Skill | Tools | Description |
|-------|-------|-------------|
| `gmail-skill` | `gmail_send`, `gmail_search`, `gmail_read` | Send, search, read emails |
| `calendar-skill` | `calendar_create`, `calendar_list`, `calendar_update`, `calendar_delete` | Manage calendar events |
| `drive-skill` | `drive_upload`, `drive_download`, `drive_list`, `drive_share` | Manage Drive files |
| `sheets-skill` | `sheets_read`, `sheets_write`, `sheets_append` | Read/write spreadsheet data |
| `tasks-skill` | `tasks_create`, `tasks_list`, `tasks_complete` | Manage Google Tasks |
| `contacts-skill` | `contacts_create`, `contacts_list`, `contacts_search` | Manage contacts |

**OAuth Redirect URIs:**
OAuth redirect URIs are derived at runtime from the request/WebSocket context via `server/services/oauth_utils.py`. No environment variable needed -- works automatically in dev (`http://localhost:3010`) and production (`https://domain.com`).

**Google API Pricing:** All Google Workspace APIs are free with rate limits. See `server/config/pricing.json` for configured limits.

### Email Nodes (3 nodes)
IMAP/SMTP email integration via the [Himalaya CLI](https://github.com/pimalaya/himalaya). Supports any IMAP/SMTP provider -- Gmail, Outlook/Office 365, Yahoo, iCloud, ProtonMail (Bridge), Fastmail, and custom/self-hosted. Credentials are stored via `auth_service.store_api_key()` and TOML config files are generated on the fly for each operation.

- **emailSend**: **Dual-purpose node** - Send emails via SMTP. Works as workflow node OR AI Agent tool. Group: `['email', 'tool']`. Parameters: provider, to, subject, body, cc, bcc, body_type (text/html).
- **emailRead**: **Dual-purpose node** - Read and manage emails via IMAP. Works as workflow node OR AI Agent tool. Group: `['email', 'tool']`. Operations:
  - `list`: List envelopes in a folder with pagination
  - `search`: Search emails by query string (e.g., `from:john subject:meeting`)
  - `read`: Read full message content by ID
  - `folders`: List all mailbox folders
  - `move`: Move message to target folder
  - `delete`: Delete message
  - `flag`: Add/remove flag (Seen, Answered, Flagged, Draft, Deleted)
- **emailReceive**: Polling-based trigger that fires on new emails. Group: `['email', 'trigger']`. Parameters: provider, folder (default `INBOX`), poll_interval (30-3600s), filter_query, mark_as_read. Polls IMAP via Himalaya at the configured interval.

**Architecture:**
```
emailSend/Read/Receive node
    |
    v
handle_email_send/read/receive (handlers/email.py)
    |
    v
EmailService (email_service.py)    <-- credential resolution + provider defaults
    |
    v
HimalayaService (himalaya_service.py)    <-- CLI wrapper
    |
    v
himalaya CLI binary    <-- generates TOML config, calls IMAP/SMTP
```

**Key Files:**
| File | Description |
|------|-------------|
| `client/src/assets/icons/email/` | Email icons (send, read, receive) loaded via `?raw` + `svgToDataUri` |
| `client/src/components/CredentialsModal.tsx` | Email credentials panel (provider dropdown, email/password, conditional custom IMAP/SMTP) |
| `server/services/himalaya_service.py` | Himalaya CLI wrapper -- send, list_envelopes, search, read, move, delete, flag, list_folders |
| `server/services/email_service.py` | EmailService orchestrator (credential resolution, operation dispatch, polling helpers) |
| `server/services/handlers/email.py` | handle_email_send, handle_email_read, handle_email_receive (thin handlers) |
| `server/services/deployment/manager.py` | `_create_email_poll_coroutine` - deployment-mode continuous polling |
| `server/services/event_waiter.py` | `TRIGGER_REGISTRY['emailReceive']` + `build_email_filter` |
| `server/config/email_providers.json` | IMAP/SMTP provider presets, defaults, polling config (zero magic numbers) |

**Credential Storage** (via `auth_service.store_api_key()`):

| Key | Scope | Description |
|-----|-------|-------------|
| `email_provider` | Always | One of `gmail`, `outlook`, `yahoo`, `icloud`, `protonmail`, `fastmail`, `custom` |
| `email_address` | Always | Account email (IMAP/SMTP login) |
| `email_password` | Always | Password or App Password (stored as secret) |
| `email_imap_host` | Custom only | Fallback when preset empty -- required for `custom` provider |
| `email_imap_port` | Custom only | Stored as string, coerced to int via `_coerce_port` |
| `email_imap_encryption` | Custom only | `tls` / `start-tls` / `none` |
| `email_smtp_host` | Custom only | Fallback SMTP hostname |
| `email_smtp_port` | Custom only | |
| `email_smtp_encryption` | Custom only | |

**Credential Resolution Precedence** (per field, in `EmailService.resolve_credentials`):

1. **Node parameter** (per-node override on the workflow node)
2. **Provider preset** from `email_providers.json` (e.g., `gmail` preset populates `imap_host = "imap.gmail.com"`)
3. **Stored custom API key** (`email_imap_host`, etc.) -- only reached when the preset is empty, i.e. `provider == 'custom'`

For named providers the preset always wins before the custom-key fallback. Credentials panel saves the custom IMAP/SMTP keys only when the user selects "Custom / Self-hosted".

**Installation Requirement:** The `himalaya` CLI binary must be installed and on PATH. Install via `cargo install himalaya`, `brew install himalaya`, or download from https://github.com/pimalaya/himalaya/releases. `HimalayaService.ensure_binary()` caches the resolved path on the singleton after first detection; missing binary raises `RuntimeError` with install instructions.

**Authentication:** Email/password (or App Password for Gmail/Outlook/Yahoo) stored per provider. ProtonMail requires running the ProtonMail Bridge locally (IMAP: `localhost:1143`, SMTP: `localhost:1025`, encryption: `none`).

See **[Email Service](./docs-internal/email_service.md)** for the full architecture, API reference, and operational details.

---

### Browser Nodes (1 node)
Interactive browser automation via the [agent-browser](https://www.npmjs.com/package/agent-browser) CLI binary. Wraps a persistent headless Chromium browser with an accessibility-tree-based interaction model designed for AI agents.

- **browser**: **Dual-purpose node** - Interactive browser automation. Works as workflow node OR AI Agent tool. Group: `['browser', 'tool']`. Session persistence across chained operations via `session` parameter (auto-derived from execution_id as `machina_{execution_id}` if not specified). Operations:
  - `navigate`: Open a URL
  - `click`: Click an element (CSS selector or `@eN` ref from snapshot)
  - `type`: Type text keystroke-by-keystroke
  - `fill`: Clear and fill an input field
  - `screenshot`: Take visible/full-page screenshot
  - `snapshot`: Get accessibility tree with `@eN` element refs (AI-optimized, stable within session)
  - `get_text`: Extract text from element
  - `get_html`: Extract innerHTML from element
  - `eval`: Execute JavaScript in page context
  - `wait`: Wait for element to appear
  - `scroll`: Scroll the page (up/down/left/right)
  - `select`: Select dropdown option
  - `batch`: Execute multiple commands at once

**Recommended Workflow (AI agents):** `navigate` -> `snapshot` -> `click`/`type`/`fill` using `@eN` refs -> `snapshot` again to verify. Prefer `@eN` refs over CSS selectors (more stable), and prefer `snapshot` + `click`/`fill` over raw `eval`.

**Key Files:**
| File | Description |
|------|-------------|
| `client/src/assets/icons/browser/` | Chrome browser icon (from @ant-design/icons-svg) |
| `server/services/browser_service.py` | BrowserService singleton -- thin subprocess wrapper, reads first JSON line from stdout |
| `server/services/handlers/browser.py` | Handler dispatcher mapping operation+params to agent-browser CLI args |
| `server/skills/web_agent/browser-skill/SKILL.md` | AI agent skill documenting snapshot-act-snapshot loop |

**Installation Requirement:** `agent-browser` is a pinned project dependency in [package.json](../package.json). `pnpm install` places it under `node_modules/.pnpm/`, and `scripts/install.js` runs `npx agent-browser install` during postinstall to download the Chromium runtime. The handler returns an installation-instruction error if the binary cannot be located.

**Implementation Detail:** `browser_service.py` invokes the binary via `[shutil.which("npx"), "--no-install", "agent-browser", ...args]` -- the same pattern used by [claude_code_service.py](../server/services/claude_code_service.py). This avoids custom cross-platform shim detection: `npx --no-install` handles local `node_modules/.bin/` resolution, Windows/POSIX shim differences, and shebang interpretation internally. The service reads only the first JSON output line from agent-browser (the daemon holds stdout open), truncates at 100KB, and kills the process tree via `psutil`. All subprocess calls use `shell=False` with list argv to avoid BatBadBut (CVE-2024-1874) on Windows.

---

### Crawlee Nodes (1 node)
Python-based web scraping via the [crawlee](https://github.com/apify/crawlee-python) library. Supports static HTML scraping (BeautifulSoup) and JS-rendered content (Playwright) with built-in concurrency, retries, and anti-bot handling.

- **crawleeScraper**: **Dual-purpose node** - Web scraper. Works as workflow node OR AI Agent tool. Group: `['scraper', 'tool']`. Configuration:
  - **Crawler Type**: `beautifulsoup` (static HTML), `playwright` (full browser), `adaptive` (auto-detect)
  - **Mode**: `single` (scrape one URL) or `crawl` (follow links with pattern matching)
  - **Parameters**: url, cssSelector, extractLinks, linkSelector, urlPattern, maxPages, maxDepth, waitForSelector, waitTimeout, takeScreenshot
- Useful for JavaScript-rendered SPAs where a simple HTTP scraper fails, and for multi-page crawls where concurrency matters.

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/handlers/crawlee.py` | Handler using `BeautifulSoupCrawler` + `PlaywrightCrawler` from crawlee library |
| `server/skills/web_agent/crawlee-scraper-skill/SKILL.md` | AI agent skill for web scraping |

**Installation Requirement:** The `crawleeScraper` node requires `playwright` chromium for JS-rendered content. The Docker image pre-installs Playwright chromium; for local dev run `playwright install chromium`.

---

### Apify Nodes (1 node)
Web scraping service for social media, search engines, and websites using pre-built actors.

- **apifyActor**: **Dual-purpose node** - Run Apify actors (web scrapers) for Instagram, TikTok, Twitter/X, LinkedIn, Facebook, YouTube, Google Search, Google Maps, and website crawling. Works as workflow node OR AI Agent tool. Group: `['api', 'scraper', 'tool']`. Pre-built actor dropdown with quick input helpers per actor type. Parameters: actorId, actorInput (JSON), maxResults, timeout, memory.

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/handlers/apify.py` | Actor execution via apify-client SDK |
| `server/skills/web_agent/apify-skill/SKILL.md` | AI agent skill for web scraping |
| `client/src/components/CredentialsModal.tsx` | Apify API token panel |

**Authentication:** Single API token (Personal or Organization) from Apify Console -> Settings -> Integrations.

### Proxy Nodes (3 nodes)
Residential proxy provider management with geo-targeting, session control, and automatic failover.

- **proxyRequest**: Make HTTP requests through residential proxy providers with geo-targeting and failover. Parameters: method, url, headers, body, timeout, proxyProvider (auto-select or specific), proxyCountry (ISO code), sessionType (rotating/sticky), stickyDuration, maxRetries, followRedirects.
- **proxyConfig**: **Dual-purpose node** - Configure proxy providers and routing rules. Works as workflow node OR AI Agent tool. Operations: list_providers, add_provider, update_provider, remove_provider, set_credentials, test_provider, get_stats, add_routing_rule, list_routing_rules, remove_routing_rule.
- **proxyStatus**: View proxy provider health, scores, and usage statistics. Filter by specific provider name or view all.

**Key Architecture:**
- **TemplateProxyProvider**: Single class using JSON `url_template` to format proxy URLs for any provider (username-based, password-based, or no encoding)
- **Auto-selection**: ProxyService ranks providers by health score (success rate, latency) and selects the best one
- **Transparent proxy on HTTP nodes**: `httpRequest` and `httpScraper` nodes support `useProxy: true` flag. The proxy service handles provider selection, geo-targeting, and session type automatically.

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/proxy/service.py` | ProxyService singleton with provider selection and URL generation |
| `server/services/proxy/providers.py` | TemplateProxyProvider for JSON url_template formatting |
| `server/services/proxy/models.py` | ProxyProvider, RoutingRule, SessionType dataclasses |
| `server/nodes/proxy/proxy_config.py`, `proxy_request.py`, `proxy_status.py` | Plugin nodes (Wave 11); ProxyConfig 10-op matrix lives in `proxy_config.py` |
| `server/skills/web_agent/proxy-config-skill/SKILL.md` | AI agent skill for proxy configuration |
| `server/skills/web_agent/http-request-skill/SKILL.md` | HTTP request skill with proxy usage docs |

### Workflow Nodes (2 nodes)
- **start**: Manual workflow trigger to start workflow execution
- **taskTrigger**: Event-driven trigger that fires when a delegated child agent completes its task (success or error). Filters by task_id, agent_name, status (all/completed/error), and parent_node_id. Output includes task_id, status, agent_name, result/error, workflow_id.

### Code Nodes (3 nodes)
- **pythonExecutor**: **Dual-purpose node** - Execute Python code with syntax-highlighted editor, input_data access, and console output. Works as workflow node OR AI Agent tool (`python_code`). Available libraries: math, json, datetime, timedelta, re, random, Counter, defaultdict.
- **javascriptExecutor**: **Dual-purpose node** - Execute JavaScript code via persistent Node.js server with syntax-highlighted editor and console output. Works as workflow node OR AI Agent tool (`javascript_code`).
- **typescriptExecutor**: **Dual-purpose node** - Execute TypeScript code via persistent Node.js server with type safety, syntax-highlighted editor and console output. Works as workflow node OR AI Agent tool (`typescript_code`).

### Filesystem & Shell Nodes (4 nodes)
Dual-purpose tool nodes wrapping `deepagents.backends.LocalShellBackend`. Per-workflow workspace from execution context (`context["workspace_dir"]` = `data/workspaces/<workflow_id>/`). Fallback uses `Settings().workspace_base_dir` (never `os.getcwd()`).

**Path safety**: `nodes/filesystem/_backend.py` exposes `normalize_virtual_path()` which uses `pathlib.PureWindowsPath` (host-OS independent) to strip Windows drives, POSIX root, and UNC anchors uniformly, then delegates to `deepagents.backends.utils.validate_path` for `..`/`~` rejection. Wired into `file_read`, `file_modify`, and `fs_search` so LLM-emitted paths in any flavour (`C:\foo`, `/tmp/foo`, `\\server\share\x`, `foo\bar`) all map to a virtual path under the workspace. `virtual_mode=True` only sandboxes filesystem ops — `execute()` itself is never path-restricted (deepagents documents this).

**Shell backend is Nushell** (`NushellBackend` subclassing `LocalShellBackend`). Cross-platform parity — same grammar on Windows/macOS/Linux. `inherit_env=True` so `npm`, `node`, `python`, `git`, etc. are reachable on PATH. Nu stdlib is loaded (no `--no-std-lib`). Falls back to upstream `LocalShellBackend.execute()` (POSIX `sh` / cmd.exe) when `nu` isn't on PATH. **Bash idioms (`&&`, `||`, `$VAR`, backticks, `>`) do not work** — see `server/skills/terminal/shell-skill/SKILL.md` for the Nu equivalents.

- **fileRead**: **Dual-purpose node** - Read file contents with line numbers and pagination. Works as workflow node OR AI Agent tool (`file_read`). Parameters: file_path, offset, limit.
- **fileModify**: **Dual-purpose node** - Write new files or edit existing files with string replacement. Works as workflow node OR AI Agent tool (`file_modify`). Operations: write (wholesale create-or-replace, no overwrite flag), edit (find and replace with old_string/new_string/replace_all).
- **shell**: **Dual-purpose node** - Execute Nushell commands with timeout. Works as workflow node OR AI Agent tool (`shell_execute`). PATH inherited; external tools (npm, node, python, git, ...) work. All backend calls wrapped in `asyncio.to_thread()` to avoid blocking the event loop. Returns stdout, exit_code, truncated flag.
- **fsSearch**: **Dual-purpose node** - Search the filesystem with three modes. Works as workflow node OR AI Agent tool (`fs_search`). Modes: ls (list directory), glob (pattern match), grep (search file contents).

**Key Files:**
| File | Description |
|------|-------------|
| `server/nodes/filesystem/_backend.py` | `NushellBackend`, `get_backend()`, `normalize_virtual_path()` |
| `server/skills/coding_agent/` | Skills: file-read-skill, file-modify-skill, fs-search-skill |
| `server/skills/terminal/` | Skills: shell-skill (Nushell), process-manager-skill, bash-skill, powershell-skill, wsl-skill |

### Process Manager Node (1 node)
Cross-platform process manager for long-running subprocesses (dev servers, watchers, build tools). Uses `asyncio.create_subprocess_exec` with full system PATH (`env={**os.environ}`). Output streams to Terminal tab via `broadcast_terminal_log()` and persists to log files in the workspace.

- **processManager**: **Dual-purpose node** - Start, stop, restart, and manage long-running processes. Works as workflow node OR AI Agent tool (`process_manager`). Operations: start, stop, restart, list, send_input, get_output. Max concurrent processes configurable in Settings (default: 10).

**Key Architecture:**
- **ProcessService** singleton (`server/services/process_service.py`) tracks running processes per workflow
- Each process writes stdout/stderr to `{workspace}/{agent_node_id}/.processes/{name}/stdout.log` and `stderr.log`
- AI agents read output selectively via `get_output(name, stream, tail)` with pagination
- Process tree cleanup via `psutil.children(recursive=True)` on stop and server shutdown
- `PYTHONUNBUFFERED=1` injected for line-buffered Python output

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/process_service.py` | ProcessService singleton with start/stop/restart/list/send_input/get_output/shutdown |
| `server/services/handlers/process.py` | Dual-purpose handler (workflow + AI tool) |
| `server/skills/terminal/process-manager-skill/` | Skill for AI agents |

### Utility Nodes (6 nodes)
- **httpRequest**: Make HTTP requests to external APIs (GET, POST, PUT, DELETE, PATCH) with configurable headers, body, timeout, and optional proxy support (`useProxy: true` routes through configured residential proxy)
- **webhookTrigger**: Event-driven trigger that waits for incoming HTTP requests at `/webhook/{path}` with method filtering and authentication options
- **webhookResponse**: Send custom response back to webhook caller with configurable status code, body, and content type
- **chatTrigger**: Trigger node that receives messages from the Console Panel chat interface
- **console**: Console output node for logging workflow execution data
- **teamMonitor**: Real-time monitoring of Agent Team operations. Connect to AI Employee or Orchestrator to display team status, active tasks, and event stream

### Document Processing Nodes (6 nodes)
RAG pipeline nodes for document ingestion, processing, and vector storage. Supports multiple providers and backends.

- **httpScraper**: Scrape links from web pages with date/page pagination support. Modes: single request, date range iteration, page pagination. Outputs: items array with URLs. Supports `useProxy: true` for proxy routing.
- **fileDownloader**: Download files from URLs in parallel using semaphore-based concurrency. Parameters: output directory, max workers (1-32), skip existing, timeout.
- **documentParser**: Parse documents to text using configurable parsers. Parsers: PyPDF (fast), Marker (GPU OCR), Unstructured (multi-format), BeautifulSoup (HTML).
- **textChunker**: Split text into overlapping chunks for embedding. Strategies: recursive (recommended), markdown, token. Parameters: chunk size (100-8000), overlap (0-1000).
- **embeddingGenerator**: Generate vector embeddings from text chunks. Providers: HuggingFace (local), OpenAI, Ollama. Default model: BAAI/bge-small-en-v1.5.
- **vectorStore**: Store and query vector embeddings. Operations: store, query, delete. Backends: ChromaDB (local), Qdrant (production), Pinecone (cloud).

### Chat Nodes (2 nodes)
- **chatSend**: Send messages to chat conversations
- **chatHistory**: Retrieve chat conversation history

### Scheduler Nodes (2 nodes)
- **timer**: Timer-based trigger with configurable delay
- **cronScheduler**: Cron expression-based scheduling trigger

#### Document Processing Dependencies
```
# Required (in server/requirements.txt)
beautifulsoup4>=4.12.0
langchain-text-splitters>=0.3.0
langchain-huggingface>=0.1.0
chromadb>=0.5.0
qdrant-client>=1.12.0
sentence-transformers>=3.0.0
pypdf>=4.0.0

# Optional (GPU OCR and multi-format parsing)
# marker-pdf>=1.0.0    # Requires CUDA
# unstructured>=0.16.0  # Multi-format document parsing
```

#### Document Node Architecture
```
server/nodes/document/
├── http_scraper.py       # each plugin emits its own NodeSpec
├── file_downloader.py
├── document_parser.py
├── text_chunker.py
├── embedding_generator.py
└── vector_store.py
```

## Backend Services

### Python Backend (FastAPI)
- **Port**: 3010
- **Base URL**: http://localhost:3010
- **Main File**: `server/main.py`

### API Endpoints
#### Android Services (`server/routers/android.py`)
- `GET /api/android/devices` - List connected Android devices via ADB with model and state info
- `POST /api/android/port-forward` - Setup ADB port forwarding for device communication
- `POST /api/android/{service_id}/{action}` - Execute Android service actions with parameters
- `GET /api/android/health` - Android service health check

#### Remote Android WebSocket
- **WebSocket**: Configurable via environment variable - Persistent WebSocket connection for remote Android devices
- **Health Check**: `{relay-url}/ws-health` - WebSocket proxy health status
- **Stats**: `{relay-url}/ws-stats` - Active connection statistics
- **Implementation**: `server/services/websocket_client.py` - Persistent WebSocket client with background tasks
  - Background message receiver continuously queues incoming messages
  - Keepalive loop sends ping every 25 seconds to maintain connection
  - Message queue (asyncio.Queue) for async message handling
  - Connection reuse across multiple API requests
  - Message filtering to skip non-response messages (presence, pong, ping)

#### Webhook Router (`server/routers/webhook.py`)
- `ANY /webhook/{path}` - Dynamic webhook endpoint for incoming HTTP requests (GET, POST, PUT, DELETE, PATCH)
- Dispatches `webhook_received` event via `broadcaster.send_custom_event()` to trigger waiting webhookTrigger nodes
- Returns immediate 200 OK response (responseNode mode planned for future)
- `GET /webhook/` - Webhook endpoint info and usage documentation

#### Workflow Services (`server/services/workflow.py`)
- Node execution handlers for all 27 node types (including httpRequest, webhookResponse)
- Parameter resolution and template variable substitution
- Result formatting and error handling

#### Frontend-Backend WebSocket (`server/routers/websocket.py`)
- **WebSocket Endpoint**: `/ws/status` - Real-time status updates between React and Python
- **REST Endpoint**: `GET /ws/info` - WebSocket connection info and current status
- **Message Types**:
  - `android_status` - Android device connection status updates
  - `node_status` - Individual node execution status
  - `node_output` - Node execution output data
  - `variable_update` - Single variable value change
  - `variables_update` - Batch variable updates
  - `workflow_status` - Workflow execution progress
  - `ping/pong` - Keep-alive messages

### Development Scripts
- `stop.bat` / `stop.sh` - Stops all development servers with duplicate Python process detection and verification
- `restart.bat` / `restart.sh` - Restarts all services cleanly
- `start.bat` / `start.sh` - Starts frontend and backend servers

### Concurrently Process Management Fix
**Problem**: Starting external services (WhatsApp, etc.) after the dev server would kill the frontend client.
- Root cause: `--kill-others` flag in concurrently npm script
- When uvicorn reloads (exit code 1), concurrently kills all processes including frontend

**Fix Applied**:
1. Removed `--kill-others` from `npm run dev` in package.json
2. Added named colored output: `-n client,python -c blue,green`
3. Added uvicorn reload controls: `--reload-dir .` and `--reload-exclude` patterns

**Result**: Frontend and backend run independently, uvicorn reloads don't cascade

### Temporal Distributed Execution

Workflows execute via Temporal for durability and horizontal scaling. Three dispatch paths (legacy `execute_node_activity` / per-type `node.{type}.v{version}` (F4.A) / Agent-as-child-workflow (F4.B)) gated by `TEMPORAL_PER_TYPE_DISPATCH` and `TEMPORAL_AGENT_WORKFLOW_ENABLED` settings flags.

Full architecture, dispatch matrix, per-node lifecycle, heartbeat semantics, the 14 migrating agent types + 2 bypass agents (rlm_agent / claude_code_agent), the 6 F4.B agent activities (`prepare_payload.v1` / `execute_llm_step.v1` / `persist_turn.v1` / `compact_memory.v1` / `store_output.v1` / `broadcast_progress.v1`), and the future `TemporalWorkerPool` per-queue routing live in [docs-internal/TEMPORAL_ARCHITECTURE.md](./docs-internal/TEMPORAL_ARCHITECTURE.md). Tool-call dispatch under F4.A documented at [docs-internal/tool_building_pipeline.md §9](./docs-internal/tool_building_pipeline.md).

**Configuration** (`.env`):
```env
TEMPORAL_SERVER_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=machina-tasks
TEMPORAL_PER_TYPE_DISPATCH=true         # F4.A flag — per-type activity dispatch
TEMPORAL_AGENT_WORKFLOW_ENABLED=true    # F4.B flag — agent-as-child-workflow
```

**Temporal binary + persistence** are managed in-process by the plugin-folder pattern under [`server/services/temporal/`](./server/services/temporal/) — the `temporal-server` npm package is no longer used. Binary distribution + lifecycle = standard libs, zero custom code:

- **`_install.py`** — [`pooch`](https://pypi.org/project/pooch/) downloads `temporal-server` + `temporal-sql-tool` from `temporalio/temporal` GitHub releases (SHA-256 verified, XDG-cached cross-platform). Falls back to `shutil.which("temporal-server")` when a system install is on PATH (brew / scoop / cargo).
- **`_runtime.py`** — `PostgresRuntime` (subclasses `BaseSupervisor`; wraps [`pgserver`](https://pypi.org/project/pgserver/) which bundles PostgreSQL 16.2 binaries cross-platform) + `TemporalServerRuntime` (subclasses `BaseProcessSupervisor`; runs the binary with a Python-rendered YAML config). Same singleton + lifecycle surface every other supervised service uses ([`nodes/whatsapp/_runtime.py`](./server/nodes/whatsapp/_runtime.py) is the template).
- **`_config.py`** — `pyyaml.safe_dump` of the Temporal cluster config; `temporal-sql-tool create-database` + `setup-schema` + `update-schema` for both `temporal` and `temporal_visibility` Postgres databases (idempotent).
- **`_handlers.py` + `_refresh.py`** — `temporal_status` / `_start` / `_stop` WS commands and the WS-connect status snapshot, registered through `services.ws_handler_registry` and `services.status_broadcaster.register_service_refresh`.

**Backend selection** via `TEMPORAL_BACKEND` env var:
- `sqlite` (dev default) — single ServiceSpec runs `temporal api` against an in-memory / file-based SQLite. Same dev experience as before.
- `postgres` (prod) — two ServiceSpecs run via the `_supervised_runtime.py` shim: `temporal-postgres` (pgserver) then `temporal-server` (Temporal binary). The supervisor's TCP readiness probe orders postgres → temporal automatically.

**Ports:**
| Service   | Port | URL                    |
|-----------|------|------------------------|
| gRPC      | 7233 | localhost:7233          |
| HTTP API  | 8233 | http://localhost:8233   |
| Web UI    | 8080 | http://localhost:8080   |
| Metrics   | 9090 | http://localhost:9090   |
| Postgres  | dyn  | postgresql://...:NNNNN  (`postgres` backend; pgserver picks a free port — read via `get_postgres_runtime().uri`) |

**CLI commands** (still available when `temporal` is on PATH or pooch-installed):
```bash
temporal-server start --config <yaml>   # what TemporalServerRuntime invokes
temporal api                            # what the sqlite ServiceSpec invokes
temporal --version
```

**Start script integration** ([`machina/commands/start.py`](./machina/commands/start.py) + [`machina/commands/dev.py`](./machina/commands/dev.py)): both call `temporal_specs(root, cfg)` ([`machina/commands/_temporal_specs.py`](./machina/commands/_temporal_specs.py)) which returns the right ServiceSpec set for the chosen backend. The supervisor's `_temporal_running()` probe still short-circuits if Temporal is already running on 7233 (lets you run `temporal-server start` standalone for debugging).

**Docker**: Optional. The Python supervisor handles cross-platform lifecycle without Docker; production-scale Temporal+Postgres runs identically on Linux / macOS / Windows via pip-installed binaries.

**Execution Routing** (`workflow.py`):
1. If `TEMPORAL_ENABLED=true` and Temporal configured → `_execute_temporal()`
2. Else if Redis available → `_execute_parallel()` (local parallel)
3. Else → `_execute_sequential()` (fallback)

**Running with Temporal**:
Temporal server and embedded worker start automatically with all launch scripts:
```bash
npm run start            # Starts Temporal server + all services
npm run dev              # Starts Temporal server + all services (dev mode)
npm run stop             # Stops all services including Temporal
```
The embedded Temporal worker runs inside the Python backend (`main.py` lifespan via `TemporalWorkerManager`), not as a separate process.

**Standalone Worker** (horizontal scaling):
```bash
cd server
python -m services.temporal.worker
```

**Node Filtering** (`workflow.py`):
- Config nodes filtered by handle: `CONFIG_HANDLES = {input-tools, input-memory, input-model, input-skill, input-task, input-teammates}`
- Trigger nodes (`TRIGGER_NODE_TYPES`) auto-completed if not pre-executed (prevents blocking on event waiter)
- Android service nodes and skill nodes filtered by type

**Key Files**:
- `services/temporal/workflow.py` - MachinaWorkflow orchestrator (FIRST_COMPLETED pattern, `_resolve_activity` dispatch)
- `services/temporal/activities.py` - Legacy `execute_node_activity` (WebSocket round-trip path)
- `services/temporal/plugin_activities.py` - `collect_plugin_activities` → per-type activities for F4.A
- `services/temporal/agent_workflow.py` - `AgentWorkflow` child workflow for F4.B agent loops; its first step calls `agent.prepare_payload.v1` to resolve the DB-backed payload, then loops LLM steps + tool dispatch + per-turn persist + compaction
- `services/temporal/agent_activities.py` - F4.B activities (6 total): `agent.prepare_payload.v1` (resolves DB params + runs `_param_resolver.resolve` so `{{templates}}` work inside the agent's own prompt), `agent.execute_llm_step.v1` (rebuilds tools via `ai_service._build_tool_from_node`; uses LangChain `messages_to_dict`/`messages_from_dict` so Gemini `thought_signature` survives), `agent.persist_turn.v1`, `agent.compact_memory.v1`, `agent.store_output.v1` (wraps `workflow_service.store_node_output` so downstream nodes can resolve `{{aiAgent.response}}`), `agent.broadcast_progress.v1` (emits CloudEvents-shaped `agent_progress` for every phase: `starting` / `llm_step` / `executing_tool` / `tool_completed` / `completed`, optionally driving raw-dict `update_node_status` for canvas-glow color)
- `services/temporal/worker.py` - TemporalWorkerManager (registers MachinaWorkflow + AgentWorkflow + activities) + `run_standalone_worker()` + `TemporalWorkerPool` (multi-queue, future enhancement)
- `services/temporal/executor.py` - TemporalExecutor interface matching WorkflowExecutor
- `services/temporal/client.py` - Client wrapper with runtime heartbeat disabled
- `services/plugin/base.py::BaseNode.as_activity()` - Per-type activity body for F4.A (mirrors the legacy pipeline)

**Runtime Configuration**: Worker heartbeating is disabled via `Runtime(worker_heartbeat_interval=None)` to avoid warnings on older Temporal server versions.

**Temporal UI**: http://localhost:8080 (Web UI), http://localhost:8233 (HTTP API)

## Development Commands

### CLI Commands (npx or global install)
```bash
npx machina start      # Start all services
npx machina stop       # Stop all services
npx machina build      # Build for production
npx machina clean      # Clean build artifacts
npx machina docker:up  # Start with Docker
npx machina help       # Show all commands
```

### npm Scripts
```bash
# Core
npm run start            # Start all services (client, backend, WhatsApp, Temporal)
npm run stop             # Stop all services
npm run build            # Build for production
npm run clean            # Clean build artifacts

# Docker (development)
npm run docker:up        # Start dev stack
npm run docker:down      # Stop dev stack
npm run docker:build     # Build images
npm run docker:logs      # View logs

# Docker (production)
npm run docker:prod:up   # Start production
npm run docker:prod:down # Stop production
npm run deploy           # Deploy to server
```

### Cross-Platform Scripts
All scripts in `scripts/` are cross-platform Node.js (Windows, macOS, Linux, WSL, Git Bash):
- `start.js` - Starts services, auto-installs deps, frees ports
- `stop.js` - Kills processes on configured ports
- `build.js` - Full production build (client, Python, WhatsApp)
- `clean.js` - Removes node_modules, dist, .venv
- `docker.js` - Docker Compose wrapper with v2 detection and Redis profile support

See **[Scripts Reference](./docs-internal/SCRIPTS.md)** for full documentation.

## Current Status
✅ **Plugin-first architecture (Wave 11)**: every plugin is a self-contained folder under `server/nodes/<group>/<plugin>/` rooted at `__init__.py`; backend NodeSpec is the SSOT for icon, colour, handles, params, output schema, uiHints. Frontend renders via `useNodeSpec` + `componentKind` dispatch.
✅ **WebSocket-First Architecture**: most frontend-backend RPC goes through WebSocket; live handler set lives in `MESSAGE_HANDLERS` in `server/routers/websocket.py`
✅ **Code Editor**: Python, JavaScript, and TypeScript executors with syntax-highlighted editor (react-simple-code-editor + prismjs) and console output
✅ **Node.js Executor**: Persistent Node.js server (Express + tsx) for fast JS/TS execution, replacing subprocess spawning
✅ **Component Palette**: Emoji icons with distinct dracula-themed category colors, localStorage persistence for collapsed sections
✅ **Android Integration**: 16 Android service nodes with ADB automation and remote WebSocket support
✅ **Conditional Parameter Display**: Dynamic UI rendering based on parameter values (displayOptions.show)
✅ **Execution Engine**: Full component execution with result display
✅ **Parameter Mapping**: Drag-and-drop output to parameter connections
✅ **AI Integration**: API key management and model selection
✅ **Location Services**: Interactive map picker with coordinate handling, Google Maps API key fetched from backend credentials
✅ **Code Cleanup**: Dead code removed, unused files deleted
✅ **Process Management**: Robust stop scripts with duplicate process detection
✅ **WhatsApp Integration**: Square node design with QR code viewer, group/sender name persistence, newsletter channel support (send, query, follow/unfollow, create, mute, mark viewed, react, live updates), media download, profile pics, and proper error handling
✅ **Backend Stability**: Fixed dependency injection and error handling preventing crashes
✅ **Development Server**: Running at **http://localhost:3001** (frontend) and **http://localhost:3010** (backend)
✅ **WebSocket Integration**: Persistent WebSocket connections for remote Android devices with background tasks and message queue
✅ **Real-time Status WebSocket**: Frontend-backend WebSocket at `/ws/status` for live Android status, node status, and variable updates
✅ **Event-Driven Trigger Nodes**: WhatsApp Receive and Webhook Trigger with asyncio.Future-based event waiting, filter builders, and cancel support
✅ **Continuous Scheduling Execution**: Temporal/Conductor pattern using `asyncio.wait(FIRST_COMPLETED)` for true parallel pipelines where dependent nodes start immediately when their specific dependency completes
✅ **Event-Driven Deployment**: n8n-style architecture where each trigger event spawns an independent, concurrent execution run (no iteration loop)
✅ **HTTP/Webhook Nodes**: HTTP Request for external APIs, Webhook Trigger for incoming requests, Webhook Response for custom responses
✅ **Theme System**: Solarized + Dracula dual-palette theming with dark mode support, vibrant action buttons, and themed React Flow edges
✅ **Modular Backend Architecture**: workflow.py refactored from 2068 to 460 lines using facade pattern with NodeExecutor, ParameterResolver, and DeploymentManager modules
✅ **Node Rename System**: n8n-style node renaming via F2 keyboard shortcut, double-click on label, or right-click context menu with inline editing
✅ **UI State Persistence**: localStorage persistence for sidebar visibility, component palette visibility, dev mode, and collapsed sections
✅ **Normal/Dev Mode**: Toggle in toolbar to filter Component Palette - Normal mode shows only AI Agents, Models, and Skills; Dev mode shows all categories
✅ **Production Deployment**: Docker Compose deployment (4 containers: Redis, Backend, Frontend, WhatsApp), nginx reverse proxy, and Let's Encrypt SSL
✅ **Authentication System**: n8n-style JWT authentication with HttpOnly cookies, single-owner and multi-user modes
✅ **Cache System**: n8n-pattern cache with Redis (production) / SQLite (local dev) / Memory fallback hierarchy
✅ **AI Thinking/Reasoning**: Extended thinking for Claude, Gemini 2.5/3, OpenAI GPT-5/o-series, Groq Qwen3 with output available in Input Data & Variables for downstream nodes
✅ **Onboarding Service**: 5-step welcome wizard with Ant Design UI, database persistence, skip/resume/replay support
✅ **Proxy System**: Residential proxy provider management with template-based URL formatting, auto-selection by health score, transparent proxy injection on httpRequest/httpScraper nodes via `useProxy: true`
✅ **Markdown Formatter**: GFM markdown to platform-native formatting (Telegram HTML, WhatsApp syntax, plain text) using markdown-it-py

## Key Features

### Parameter System
- **Universal Renderer**: Supports both INodeProperties and NodeParameter interfaces
- **Type-Specific Controls**: String, number, boolean, select, slider, file, array types
- **Drag-and-Drop**: Map outputs from connected nodes to parameters
- **Validation**: Required field checking and type constraints
- **Conditional Display**: Dynamic parameter visibility using displayOptions.show pattern
  - Implemented in `MiddleSection.tsx` with `shouldShowParameter()` function
  - Supports array-based conditions (e.g., `messageType: ['text']`)
  - Filters parameters before rendering based on other parameter values

### Node Rename System (n8n-style)
Three methods for renaming nodes, following n8n UX patterns:
- **F2 Keyboard Shortcut**: Press F2 with a node selected to enter rename mode
- **Double-click on Label**: Click the node label twice to edit inline
- **Right-click Context Menu**: "Rename" option in the context menu

#### Architecture
```
Global State (useAppStore)          Node Components
├── renamingNodeId: string | null   ├── SquareNode.tsx
├── setRenamingNodeId()             ├── TriggerNode.tsx
        ↓                           ├── GenericNode.tsx
   Coordinates which node           └── StartNode.tsx
   is currently being renamed           ↓
                                    Local State:
                                    ├── isRenaming: boolean
                                    ├── editLabel: string
                                    └── inputRef: HTMLInputElement
```

#### Implementation Files
- **`client/src/store/useAppStore.ts`** - Global rename state (`renamingNodeId`, `setRenamingNodeId`)
- **`client/src/components/ui/NodeContextMenu.tsx`** - Right-click menu with Rename, Copy, Delete
- **`client/src/Dashboard.tsx`** - Context menu handler, F2 keyboard handler
- **`client/src/components/SquareNode.tsx`** - Inline rename for square nodes (Android, WhatsApp)
- **`client/src/components/TriggerNode.tsx`** - Inline rename for trigger nodes
- **`client/src/components/GenericNode.tsx`** - Inline rename for generic colored nodes
- **`client/src/components/StartNode.tsx`** - Inline rename with label support (was hardcoded "Start")

#### Key Pattern (shared by all node components)
```typescript
// Sync with global renaming state
useEffect(() => {
  if (renamingNodeId === id) {
    setIsRenaming(true);
    setEditLabel(data?.label || definition?.displayName || type || '');
  } else {
    setIsRenaming(false);
  }
}, [renamingNodeId, id, data?.label, definition?.displayName, type]);

// Handle save - only save if changed and non-empty
const handleSaveRename = useCallback(() => {
  const newLabel = editLabel.trim();
  if (newLabel && newLabel !== originalLabel) {
    updateNodeData(id, { ...data, label: newLabel });
  }
  setIsRenaming(false);
  setRenamingNodeId(null);
}, [...]);
```

#### NodeContextMenu Features
- Rename (F2), Copy (Ctrl+C), Delete (Del) with keyboard shortcuts shown
- Uses existing `useCopyPaste.copySelectedNodes()` for Copy
- Uses existing `onNodesDelete` for Delete
- Keyboard navigation (Arrow keys, Enter)
- Click outside to close
- Dracula-themed styling

### UI State Persistence
The application persists UI state to localStorage for a consistent user experience across sessions:

#### Persisted Settings
| Setting | Storage Key | Default | Location |
|---------|-------------|---------|----------|
| Sidebar visibility | `ui_sidebar_visible` | `true` | `useAppStore.ts` |
| Component palette visibility | `ui_component_palette_visible` | `true` | `useAppStore.ts` |
| Pro mode | `ui_pro_mode` | `false` | `useAppStore.ts` |
| Collapsed palette sections | `component_palette_collapsed_sections` | All collapsed | `useComponentPalette.ts` |

#### Implementation Pattern
```typescript
// In useAppStore.ts
const STORAGE_KEYS = {
  sidebarVisible: 'ui_sidebar_visible',
  componentPaletteVisible: 'ui_component_palette_visible',
};

const loadBooleanFromStorage = (key: string, defaultValue: boolean): boolean => {
  try {
    const saved = localStorage.getItem(key);
    if (saved !== null) return saved === 'true';
  } catch { /* Ignore storage errors */ }
  return defaultValue;
};

// Initial state loads from localStorage
sidebarVisible: loadBooleanFromStorage(STORAGE_KEYS.sidebarVisible, true),

// Toggle functions save to localStorage
toggleSidebar: () => {
  set((state) => {
    const newValue = !state.sidebarVisible;
    saveBooleanToStorage(STORAGE_KEYS.sidebarVisible, newValue);
    return { sidebarVisible: newValue };
  });
},
```

### Normal/Dev Mode Toggle
The toolbar includes a mode toggle that filters the Component Palette for different user experience levels:

| Mode | Description | Visible Categories |
|------|-------------|-------------------|
| **Normal** (default) | Simplified view for AI-focused workflows | AI Agents, AI Models, AI Skills, AI Abilities, AI Tools |
| **Dev** | Full access to all node types | All categories |

#### Implementation
- **State**: `proMode` boolean in `useAppStore.ts` with localStorage persistence (internal name unchanged for compatibility)
- **Toggle UI**: Segmented control in toolbar with "Normal" and "Dev" labels
- **Filtering**: `ComponentPalette.tsx` filters by `SIMPLE_MODE_CATEGORIES = ['agent', 'model', 'skill', 'tool']`
- **Category Merging**: WhatsApp and social nodes are merged into "Social Media Platforms" category via `SOCIAL_CATEGORIES = ['whatsapp', 'social']`

```typescript
// In ComponentPalette.tsx
const SIMPLE_MODE_CATEGORIES = ['agent', 'model', 'skill', 'tool'];
const SOCIAL_CATEGORIES = ['whatsapp', 'social'];

// Filter nodes based on mode
if (!proMode) {  // proMode=false means Normal mode
  const categoryKey = (definition.group?.[0] || '').toLowerCase();
  if (!SIMPLE_MODE_CATEGORIES.includes(categoryKey)) {
    return false;
  }
}

// Merge whatsapp and social categories
if (SOCIAL_CATEGORIES.includes(categoryKey.toLowerCase())) {
  categoryKey = 'social';
}
```

### Console Panel
The Console Panel provides a resizable bottom panel with three sections: Chat (AI conversation), Console (node execution logs), and Terminal (planned).

#### Features
- **Resizable**: Drag handle at top to resize, persisted to localStorage
- **Three Tabs**: Chat, Console, Terminal (placeholder)
- **Chat Section**: Send messages to Chat Trigger nodes, view conversation history
- **Console Section**: View and filter node execution logs

#### Node Selector Dropdowns
When multiple chatTrigger or console nodes exist in the workflow, dropdowns appear to select which node to target:

| Selector | Location | Behavior |
|----------|----------|----------|
| Chat Trigger | Chat section header | Select which chatTrigger node receives messages. "All" broadcasts to all triggers |
| Console | Console section controls | Filter logs to show only output from selected console node |

**Implementation** (`client/src/components/ui/ConsolePanel.tsx`):
```typescript
// Node type constants for filtering
const CHAT_TRIGGER_TYPES = ['chatTrigger'];
const CONSOLE_NODE_TYPES = ['console'];

// Filter workflow nodes
const chatTriggerNodes = useMemo(() =>
  nodes.filter(n => CHAT_TRIGGER_TYPES.includes(n.type || '')),
  [nodes]
);
const consoleNodes = useMemo(() =>
  nodes.filter(n => CONSOLE_NODE_TYPES.includes(n.type || '')),
  [nodes]
);

// State for selected nodes
const [selectedChatTriggerId, setSelectedChatTriggerId] = useState<string>('');
const [selectedConsoleId, setSelectedConsoleId] = useState<string>('');
```

#### Chat Message Persistence
Chat messages are persisted to SQLite database and survive server restarts.

**Database Model** (`server/models/database.py`):
```python
class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(default="default", index=True, max_length=255)
    role: str = Field(max_length=20)  # 'user' or 'assistant'
    message: str = Field(max_length=50000)
    created_at: datetime
```

**WebSocket Handlers** (`server/routers/websocket.py`):
| Handler | Description |
|---------|-------------|
| `send_chat_message` | Send message to chat, optionally targeting specific node via `node_id` |
| `get_chat_messages` | Retrieve chat history for session |
| `clear_chat_messages` | Clear all messages for session |

**Database Methods** (`server/core/database.py`):
- `add_chat_message(session_id, role, message)` - Add message to database
- `get_chat_messages(session_id, limit)` - Get messages with pagination
- `clear_chat_messages(session_id)` - Delete all messages for session

#### Console Log Persistence
Console logs are persisted to SQLite database and loaded on page refresh.

**WebSocket Handlers**:
| Handler | Description |
|---------|-------------|
| `get_console_logs` | Retrieve console logs from database (limit: 100) |
| `clear_console_logs` | Clear all console logs from database |

**Database Methods** (`server/core/database.py`):
- `add_console_log(log_data)` - Add console log to database
- `get_console_logs(limit)` - Get console logs
- `clear_console_logs()` - Delete all console logs

#### Key Files
| File | Description |
|------|-------------|
| `client/src/components/ui/ConsolePanel.tsx` | Main panel component with chat/console/terminal tabs |
| `server/models/database.py` | ChatMessage and ConsoleLog SQLModel definitions |
| `server/core/database.py` | Chat message and console log CRUD methods |
| `server/routers/websocket.py` | WebSocket handlers for chat and console operations |

### Per-Workflow Workspace Directory
Each workflow execution gets a persistent workspace directory where nodes save output files and AI agents (especially Deep Agent) access them via filesystem tools.

**Directory**: `data/workspaces/<workflow_id>/`

**Configuration** (`server/core/config.py`):
```python
workspace_base_dir: str = Field(default="data/workspaces", env="WORKSPACE_BASE_DIR")
```

**How it works:**
- `workflow.py` creates the workspace dir and injects `workspace_dir` into the execution context
- `fileDownloader` saves to `{workspace_dir}/downloads/` by default
- Code executors (Python/JS/TS) receive `workspace_dir` in their execution namespace
- Deep Agent uses `FilesystemBackend(root_dir=workspace_dir, virtual_mode=True)` -- its filesystem tools (`read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`) operate within the workspace
- `virtual_mode=True` sandboxes paths to prevent traversal outside workspace

**Key Files:**
| File | Description |
|------|-------------|
| `server/core/config.py` | `workspace_base_dir` setting |
| `server/services/workflow.py` | `_get_workspace_dir()`, injects into context |
| `server/services/handlers/document.py` | `fileDownloader` uses workspace for downloads |
| `server/services/handlers/code.py` | `workspace_dir` available in Python/JS/TS execution |
| `server/nodes/filesystem/_backend.py` | `NushellBackend(root_dir=workspace_dir, virtual_mode=True)` for the file/shell plugins |

### Execution System
- **Supported Components**: AI models, location services, Android automation, WhatsApp messaging, HTTP requests, webhooks
- **Android Integration**: ADB-based device control with 17 service nodes across monitoring, apps, automation, sensors, and media
- **Result Display**: Formatted output panel with success/error states
- **Performance Metrics**: Execution time and status tracking
- **Error Handling**: Comprehensive error reporting and logging
- **Dynamic Options**: Load options from backend (e.g., Android device list, service actions)
- **Continuous Scheduling**: Temporal/Conductor pattern using `asyncio.wait(FIRST_COMPLETED)` - dependent nodes start immediately when their specific dependency completes
- **Event-Driven Deployment**: n8n-style architecture where triggers spawn independent concurrent execution runs (no iteration loop)

### Event-Driven Deployment Architecture (n8n Pattern)
The deployment system follows modern workflow engine patterns from n8n, Temporal, and Conductor:

```
deploy_workflow() -> Sets up triggers, returns immediately
                 |
                 +-> cronScheduler fires -> spawns ExecutionRun 1
                 +-> cronScheduler fires -> spawns ExecutionRun 2 (concurrent)
                 +-> whatsappReceive fires -> spawns ExecutionRun 3 (concurrent)
                 +-> webhookTrigger fires -> spawns ExecutionRun 4 (concurrent)
```

**Key Concepts:**
- **Workflow Template**: The deployed workflow is a template stored in memory
- **Execution Run**: Each trigger event spawns an independent, isolated run
- **Concurrent Runs**: Multiple runs execute simultaneously without interference
- **No Iteration Loop**: Purely event-driven, not polling or sequential iterations
- **Pre-Executed Triggers**: The firing trigger is marked complete before downstream execution. All other trigger nodes in the run are also marked `_pre_executed` with `{not_triggered: True}` to prevent them from blocking as event waiters

**Implementation Files:**
- `server/services/workflow.py`: Thin facade (~460 lines) delegating to specialized modules
- `server/services/node_executor.py`: Single node execution with registry-based dispatch
- `server/services/parameter_resolver.py`: Template variable resolution (`{{node.field}}`)
- `server/services/deployment/manager.py`: Deployment lifecycle, spawn runs, cancel
- `server/services/deployment/triggers.py`: Cron and event trigger management
- `server/services/deployment/state.py`: DeploymentState, TriggerInfo dataclasses
- `server/services/execution/models.py`: `ExecutionContext.create()` with `_pre_executed` support
- `server/services/execution/executor.py`: Continuous scheduling with `asyncio.wait(FIRST_COMPLETED)`

### AI Chat Model System (5-Layer Architecture)
- **Visual Components**: Circular node design with real-time status indicators (green/yellow/red)
- **Node Definitions**: Backend NodeSpec (`server/nodes/models/<provider>.py`) is the single source of truth; the frontend renders via `lib/nodeSpec.ts` + `adapters/nodeSpecToDescription.ts`
- **Parameter System**: Universal renderer with drag-and-drop template variables (`{{variable}}`)
- **API Key Management**: Secure localStorage with base64 encryption and LangChain validation
- **Execution Engine**: Routes AI nodes to Python Flask backend with auto-injection of API keys

#### Supported AI Providers & Models
9 providers are available for aiAgent, chatAgent (Zeenie), and all specialized agents: OpenAI, Anthropic, Google (Gemini), DeepSeek, Kimi (Moonshot), Mistral, OpenRouter, **Ollama**, **LM Studio**. Groq and Cerebras are available as standalone chat model nodes only. Model parameters (max output, context length, thinking type, temperature range) are managed by `ModelRegistryService` (`server/services/model_registry.py`) which fetches from OpenRouter for cloud models, and from the user's running local server (via `ollama.AsyncClient.ps()` / `lmstudio.AsyncClient.llm.list_loaded()`) for Ollama / LM Studio. Falls back to `server/config/llm_defaults.json` only when neither source is available.

**Dual-path execution architecture:**
- **Native SDK path** (`execute_chat()`, `fetch_models()`): OpenAI, Anthropic, Gemini, OpenRouter, xAI, DeepSeek, Kimi, Mistral, Ollama, LM Studio use the `openai` Python SDK via `services/llm/` layer with config-driven `base_url`. Local providers (Ollama, LM Studio) ride the same OpenAI-compat path — `OpenAIProvider` is constructed with `base_url={user-stored URL}` so requests stay on `http://localhost:…` and never hit api.openai.com. Factory: `create_provider(name, api_key)` with lazy imports.
- **LangChain agent path** (`execute_agent()`, `execute_chat_agent()`): All providers use `ChatOpenAI` with `base_url` from `llm_defaults.json` + `chat_model.bind_tools` for tool calling, driven by the plain-async `_run_agent_loop`. OpenAI-compatible providers (DeepSeek, Kimi, Mistral) pass `max_tokens` via `extra_body` to bypass LangChain's `max_completion_tokens` conversion.
- **LangChain fallback** (`execute_chat()`): Groq, Cerebras still use LangChain `create_model()` + `chat_model.invoke()`.
- Native types aliased in ai.py: `NativeMessage`, `NativeThinkingConfig`, `LLMResponse` to avoid naming conflicts with LangChain's `ThinkingConfig`.
- Provider configs (base_url, models_endpoint, supported_params, temperature constraints) are driven by `server/config/llm_defaults.json` -- no hardcoded URLs in Python code.

**Local LLM SDKs (Ollama, LM Studio):** the validator at [`server/nodes/model/_local_validator.py`](./server/nodes/model/_local_validator.py) probes the user's running server through the official Python SDKs (`ollama>=0.6.0`, `lmstudio>=1.5.0`) and reads only typed fields — no Modelfile-parameters parsing, no `/api/show` modelinfo dict-key hunting. `ollama.AsyncClient.ps()` returns a typed `ProcessResponse.Model` per loaded model with typed `context_length` + typed `ModelDetails` (family, parameter_size, quantization_level, format). `lmstudio.AsyncClient.llm.list_loaded()` returns `AsyncModelHandle` objects whose `get_info()` is a typed `LlmInstanceInfo` (`context_length`, `max_context_length`, `vision`, `trained_for_tool_use`, `architecture`, `params_string`, `format`). The validator persists every probed field into `EncryptedAPIKey.models["model_params"]` (the same JSON column as the model list) and calls `model_registry.register_local_model()` so the sync `get_context_length()` / `get_max_output_tokens()` lookups return real values. The registry also writes through to `model_registry.json` so per-model params survive process restart — the user doesn't have to re-click "Fetch" every time the server bounces.

**Open-world providers** (`openrouter`, `ollama`, `lmstudio`) skip the model-name pattern check in `is_model_valid_for_provider` — local model names like `qwen/qwen3.6-27b` don't contain provider substrings, and the cloud-style filter would have produced a "invalid for provider … using default: <same name>" log line on every chat call. The upstream server still rejects genuinely missing models with a clear 404.

**Provider detection** (`detect_ai_provider` in `server/constants.py`) substring-matches the node type's lowercase form. Local-server providers MUST be listed there — without `ollama` / `lmstudio` branches, `lmstudioChatModel` falls through to `'openai'` and `execute_chat` ends up calling api.openai.com with the local-server placeholder key. Same fix is required in the agent `provider` Literal in `ai_agent.py` / `chat_agent.py` / `_specialized.py`: the dropdown only offers the values listed there, and any local-LLM user picking a missing entry silently falls back to OpenAI cloud.

| Provider | Key Models | Context | Max Output | Thinking | Temp Range |
|----------|-----------|---------|-----------|----------|------------|
| **OpenAI** | GPT-5.2/5.1/5/5-mini/5-nano | 400K | 128K | effort (hybrid) | 0-2 |
| **OpenAI** | GPT-4.1/4.1-mini/4.1-nano | 1M | 32K | none | 0-2 |
| **OpenAI** | o1, o3, o3-mini, o4-mini | 200K | 100K | effort (reasoning) | fixed 1.0 |
| **OpenAI** | GPT-4o/4o-mini | 128K | 16K | none | 0-2 |
| **Anthropic** | Claude Opus 4.6 | 200K (1M beta) | 128K | budget | 0-1 |
| **Anthropic** | Claude Sonnet 4.6/4.5/4, Opus 4.5, Haiku 4.5 | 200K (1M beta) | 64K | budget | 0-1 |
| **Anthropic** | Claude Opus 4.1/4 | 200K | 32K | budget | 0-1 |
| **Google** | Gemini 3-pro/flash, 2.5-pro/flash/flash-lite | 1M | 65K | budget | 0-2 |
| **DeepSeek** | deepseek-chat (V3), deepseek-reasoner (CoT) | 128K | 8-64K | always-on (reasoner) | 0-2 |
| **Kimi** | kimi-k2.5, kimi-k2-thinking | 256K | 96K | on by default (disabled for agents) | fixed 0.6/1.0 |
| **Mistral** | mistral-large, mistral-small, codestral | 256K | 131K | none | 0-1.5 |
| **Groq** | Llama 4 Scout, Llama 3.x, Qwen3-32b, GPT-OSS | 131K | 8-131K | format (Qwen3) | 0-2 |
| **OpenRouter** | 200+ models from multiple providers | varies | varies | varies | 0-2 |
| **Cerebras** | Llama 3.1-8b, GPT-OSS-120b, Qwen-3-235b | 32-131K | 8K | format (Qwen) | 0-1.5 |
| **Ollama** | Whatever the user has pulled (qwen2.5, llama3.x, phi-3, deepseek-r1, ...) | per-loaded-model (typed via `ps()`) | ctx ÷ 4 (capped 4096) | none (per-model) | 0-2 |
| **LM Studio** | Whatever the user has loaded in the LM Studio UI | per-loaded-model (typed via `LlmInstanceInfo.context_length`) | ctx ÷ 4 (capped 4096) | none (per-model) | 0-2 |

`_resolve_max_tokens()` in `ai.py` clamps user-requested max_tokens to the model's actual limit.

#### Key Features
- Visual configuration with status indicators and parameter buttons
- Template variable system for dynamic parameter binding from connected nodes
- Provider-specific parameter sets (temperature, max tokens, penalties, safety settings)
- Secure API key validation with automatic model discovery and 30-day expiration
- Execution routing to Python Flask backend for AI model processing
- **Proxy-based authentication** for routing through local servers (Ollama pattern)
- **Provider default parameters** configurable in Credentials Modal (temperature, max_tokens, thinking settings)

#### Provider Default Parameters
Users can configure default parameter values per LLM provider in the Credentials Modal. These defaults are applied to new AI nodes using that provider.

**Configurable Parameters:**
- `temperature`: Controls randomness (range varies: Anthropic 0-1, Cerebras 0-1.5, others 0-2; o-series fixed at 1.0)
- `max_tokens` (1-200000): Maximum response length (clamped to model's actual limit by `_resolve_max_tokens()`)
- `thinking_enabled`: Enable extended thinking for supported models
- `thinking_budget` (1024-16000): Token budget for thinking (Claude, Gemini)
- `reasoning_effort` (low/medium/high): For OpenAI o-series and GPT-5 hybrid reasoning
- `reasoning_format` (parsed/hidden): For Groq Qwen3 models

**Database Model** (`server/models/database.py`):
```python
class ProviderDefaults(SQLModel, table=True):
    provider: str           # openai, anthropic, gemini, groq, openrouter, cerebras
    temperature: float
    max_tokens: int
    thinking_enabled: bool
    thinking_budget: int
    reasoning_effort: str   # low, medium, high
    reasoning_format: str   # parsed, hidden
```

**Key Files:**
| File | Description |
|------|-------------|
| `server/models/database.py` | `ProviderDefaults` SQLModel |
| `server/core/database.py` | `get_provider_defaults()`, `save_provider_defaults()` CRUD |
| `server/routers/websocket.py` | `get_provider_defaults`, `save_provider_defaults` handlers |
| `client/src/hooks/useApiKeys.ts` | `getProviderDefaults()`, `saveProviderDefaults()` methods |
| `client/src/components/CredentialsModal.tsx` | Default Parameters UI section |

#### Proxy-Based Authentication (Ollama Pattern)
AI providers support optional proxy-based authentication, allowing requests to route through a local proxy server that handles authentication. This follows the [Ollama Claude Code integration](https://docs.ollama.com/integrations/claude-code) pattern.

**How it works:**
1. User configures a proxy URL in the Credentials Modal (e.g., `http://localhost:11434`)
2. Requests route through the proxy instead of directly to the provider API
3. Proxy handles authentication (token set to "ollama" automatically)
4. No API key storage needed in MachinaOs - auth delegated to proxy

**Configuration:**
- Proxy URLs stored in database via `{provider}_proxy` pattern (e.g., `anthropic_proxy`, `openai_proxy`)
- Configured in Credentials Modal under each AI provider
- Falls back to direct API key if no proxy configured

**Key Files:**
| File | Description |
|------|-------------|
| `server/services/ai.py` | Native path: `create_provider(name, api_key, proxy_url=url)`. LangChain path: `create_model()` with `base_url` kwarg. |
| `client/src/components/CredentialsModal.tsx` | Proxy URL input for AI providers |

**Backend Implementation** (`server/services/ai.py`):
```python
def create_model(self, provider: str, api_key: str, model: str,
                temperature: float, max_tokens: int,
                thinking: Optional[ThinkingConfig] = None,
                proxy_url: Optional[str] = None):
    # ...
    if proxy_url:
        kwargs['base_url'] = proxy_url
        kwargs[config.api_key_param] = "ollama"  # Ollama-style token
```

**Use Cases:**
- Claude Code CLI proxy for Anthropic models
- **Native Ollama / LM Studio support** — these are first-class providers (see "Local LLM SDKs" above). The user enters their server URL (e.g. `http://localhost:11434/v1`) in the Credentials Modal, the validator probes via the official SDK, and `{provider}_proxy` carries the URL into `OpenAIProvider`'s `base_url` at runtime. Identical mechanism, but the `base_url` resolves to the user's machine, not OpenAI cloud.
- Custom authentication proxies
- Development/testing with mock servers

## AI Chat Model Implementation Details

### Component Architecture
AI chat model nodes (`openaiChatModel`, `anthropicChatModel`, `geminiChatModel`, etc.) render through `SquareNode` via the spec-driven `COMPONENT_BY_KIND['model']` dispatch in `Dashboard.tsx`. Per-provider visual config (icon, color, displayName, parameter schema) comes from the backend `NodeSpec` declared in `server/nodes/model/<provider>_chat_model.py`. Provider icons use `lobehub:<brand>` strings resolved by `resolveLibraryIcon` against `@lobehub/icons`; brand-specific SVGs (deepseek/kimi/mistral) live under `client/src/assets/icons/llm/` and are addressed via `asset:<key>`. There are no per-provider wrapper components anymore — adding a new provider takes a single Python file under `server/nodes/model/`.

### API Key Management (`src/services/apiKeyManager.ts`)
- **Validation**: Uses LangChain for real API testing with provider-specific chat models
- **Storage**: localStorage with base64 encryption and key hashing for security
- **Models**: Automatic discovery and caching of available models per provider
- **Expiration**: 30-day validation period with automatic cleanup

### Execution Flow (`src/services/executionService.ts`)
- **Detection**: `isAIModelNode()` identifies AI chat models for Python routing
- **Enhancement**: `injectStoredApiKeys()` auto-injects stored credentials and models
- **Routing**: AI nodes → Python Flask backend, other nodes → Node.js backend
- **Logging**: Comprehensive debug output for API key injection and model selection
- **Supported Types**: `isNodeTypeSupported()` controls which nodes show Run button - includes AI models, agents, Android, WhatsApp, Twitter, Google Workspace, code executors, schedulers, utilities, and document processing nodes

### Parameter System Integration
- **Template Variables**: Support for `{{nodeId.output}}` syntax in all text parameters
- **Drag-and-Drop**: Visual parameter mapping from connected node outputs
- **Type-Specific**: Provider-specific parameters (OpenAI response format, Gemini safety settings)
- **Validation**: Real-time parameter validation with visual feedback

### AI Thinking/Reasoning System
Extended thinking and reasoning capabilities for supported AI models. When enabled, the model's internal reasoning process is captured and available for downstream nodes.

#### Supported Providers & Configuration

| Provider | Models | Parameter | Thinking Type | Notes |
|----------|--------|-----------|---------------|-------|
| **Claude** | All Claude 4.x/3.5 | `thinkingBudget` (1024-16000 tokens) | budget | Requires `max_tokens > budget_tokens`. Temperature auto-set to 1. |
| **Gemini** | gemini-3.x, gemini-2.5-pro/flash | `thinkingBudget` (token count) | budget | Uses `thinking_budget` API parameter |
| **OpenAI** | o1, o3, o3-mini, o4-mini | `reasoningEffort` (low/medium/high) | effort | Reasoning-only models. Temperature fixed at 1.0. |
| **OpenAI** | GPT-5.2/5.1/5/5-mini/5-nano | `reasoningEffort` (low/medium/high/xhigh) | effort | Hybrid reasoning: can operate with or without thinking. |
| **Groq** | qwen3-32b | `reasoningFormat` ('parsed' or 'hidden') | format | 'parsed' returns reasoning, 'hidden' returns only final answer |
| **Cerebras** | qwen-3-235b | `reasoningFormat` ('parsed' or 'hidden') | format | Same format-based reasoning as Groq Qwen |

#### Thinking/Reasoning Parameters
The thinking/reasoning fields (`thinkingEnabled`, `thinkingBudget`, `reasoningEffort`, `reasoningFormat`) live in the backend NodeSpec for each chat model (`server/nodes/models/<provider>.py`) and are surfaced through `AIChatModelParams` in `server/models/nodes.py`. The frontend renders them automatically via the universal parameter panel.

#### Backend Implementation (`server/services/ai.py`)
The AI service extracts thinking content from LangChain response objects:

```python
def extract_thinking_from_response(response, provider: str) -> Optional[str]:
    """Extract thinking/reasoning from AI response based on provider."""
    # Claude: content_blocks with type='thinking'
    # Gemini: response_metadata.candidates[0].content.parts with thought=True
    # Groq: additional_kwargs.reasoning or response_metadata.reasoning
    # OpenAI o-series: requires organization verification
```

**Response Structure:**
```python
{
    "success": True,
    "result": {
        "response": "The final answer text",
        "thinking": "The model's internal reasoning (if available)",
        "model": "claude-3-5-sonnet-20241022",
        "provider": "anthropic",
        "finish_reason": "stop",
        "timestamp": "2025-01-23T..."
    }
}
```

#### Output Schema for Connected Nodes
The `thinking` field is available in Input Data & Variables for downstream nodes. This schema applies to all AI nodes including chat models and specialized agents.

**Source of truth (Wave 3, April 2026):** runtime output shapes live on the **backend**, not the frontend. Declared in Pydantic models at `server/services/node_output_schemas.py` and served lazy via `GET /api/schemas/nodes/{node_type}.json` / the `get_node_output_schema` WebSocket handler. The frontend's InputSection prefers real execution data, falls back to the backend schema, then an empty state. See [docs-internal/schema_source_of_truth_rfc.md](./docs-internal/schema_source_of_truth_rfc.md).

```python
# server/services/node_output_schemas.py
class AIAgentOutput(_OutputBase):
    response: Optional[str] = None
    thinking: Optional[str] = None  # Available for drag-and-drop mapping
    model: Optional[str] = None
    provider: Optional[str] = None
    finish_reason: Optional[str] = None
    timestamp: Optional[str] = None

# Shared across every LLM-backed agent + chat model:
_AGENT_TYPES = ['aiAgent', 'chatAgent', 'android_agent', 'coding_agent',
                'web_agent', 'task_agent', 'social_agent', 'travel_agent',
                'tool_agent', 'productivity_agent', 'payments_agent',
                'consumer_agent', 'autonomous_agent', 'orchestrator_agent',
                'ai_employee', 'rlm_agent', 'claude_code_agent']
_CHAT_MODEL_TYPES = ['openaiChatModel', 'anthropicChatModel', 'geminiChatModel', ...]
NODE_OUTPUT_SCHEMAS = {
    **{t: AIAgentOutput for t in _AGENT_TYPES},
    **{t: AIAgentOutput for t in _CHAT_MODEL_TYPES},
    # ...
}
```

Adding a new node type's output shape: define one Pydantic model, register it in `NODE_OUTPUT_SCHEMAS`. Zero frontend change.

#### UI Display (`client/src/components/ui/NodeOutputPanel.tsx`)
- **ThinkingBlock Component**: Collapsible display for thinking content
- **Default Expanded**: Thinking block is expanded by default when present
- **Provider-Aware**: Shows appropriate label based on provider (e.g., "Claude Extended Thinking")

#### Limitations
- **OpenAI o-series**: Reasoning summaries are only available to organizations that have completed verification at platform.openai.com. Without verification, `thinking` will be `null`.
- **Claude**: `max_tokens` must be greater than `thinkingBudget`. Temperature is automatically set to 1 when thinking is enabled.
- **Groq**: Only Qwen3-32b supports reasoning (QwQ removed from Groq). Format 'hidden' suppresses reasoning output.
- **Cerebras**: Qwen-3-235b supports format-based reasoning (same as Groq Qwen).

## AI Agent Node Architecture

### Agent Loop Termination
The standard agent path (`_run_agent_loop` in `services/ai.py`) routes purely on `response.tool_calls` — no custom iteration counter beyond `max_iterations`. The cap is sourced from `agent.recursion_limit` in `llm_defaults.json` (currently 500 — generous backstop, not the load-bearing termination signal).

The token-based compaction threshold (`agent.compaction.ratio` × context_length, ≈100K for claude-sonnet-4-6) is the real termination signal: `_track_token_usage` runs after each agent turn, and when cumulative tokens cross the threshold it invokes `CompactionService.compact_context` to summarise the transcript before the next turn.

If the iteration cap is ever hit anyway, `_run_agent_loop` appends a terminal `AIMessage` carrying a truncation note and returns `truncated=True` so `_extract_text_content` returns a usable partial response and the workflow continues — `_track_token_usage` and any post-loop persistence still run.

### Spec-driven component design (Wave 10.D)

`AIAgentNode.tsx` knows nothing about specific agent types. It calls `useNodeSpec(type)` once and reads `handles`, `color`, `displayName`, `subtitle`, and `uiHints` from the cached `NodeSpec` ([`AIAgentNode.tsx:55-68`](./client/src/components/AIAgentNode.tsx#L55-L68)). The earlier 60-line `AGENT_CONFIGS` map (one entry per agent type, each with hardcoded `themeColorKey` + handle topology) was retired in Wave 10.D.

Component dispatch lives in [`Dashboard.tsx:74-105`](./client/src/Dashboard.tsx#L74-L105):

```typescript
const COMPONENT_BY_KIND: Record<string, React.ComponentType<any>> = {
  start: StartNode,
  trigger: TriggerNode,
  agent: AIAgentNode,    // every plugin with component_kind="agent" routes here
  chat: AIAgentNode,
  model: SquareNode,
  square: SquareNode,
  tool: SquareNode,
  generic: SquareNode,
};

const createNodeTypes = () => {
  const types: Record<string, React.ComponentType<any>> = {};
  listCachedNodeSpecs().forEach(spec => {
    const kind = spec.componentKind;
    if (kind && COMPONENT_BY_KIND[kind]) {
      types[spec.type] = COMPONENT_BY_KIND[kind];
    } else if (spec.type === 'teamMonitor') {
      types[spec.type] = TeamMonitorNode;
    } else if ((spec.uiHints as any)?.isMasterSkillEditor === true) {
      types[spec.type] = ToolkitNode;
    } else {
      types[spec.type] = SquareNode;
    }
  });
  return types;
};
```

The dispatch keys off `spec.componentKind` (a backend-declared string), never `spec.type`. Specialized agent visuals (icon, color, handles, subtitle, width, height) all come from the spec. Icons live as `icon.svg` in the plugin folder (served via `/api/schemas/nodes/<type>/icon`); colors live as `meta.json` in the plugin folder (post-F2). [`server/nodes/visuals.json`](./server/nodes/visuals.json) is the fallback registry for emoji / `lobehub:<brand>` icons + the skill reverse-map.

### AI Agent vs Zeenie

| Feature | AI Agent | Zeenie |
|---------|----------|------------|
| Tool Calling | Yes (agent loop) | Yes (agent loop) |
| Memory Support | Yes | Yes |
| Skill Support | Yes | Yes |
| Task Input | Yes (input-task) | Yes (input-task) |
| Bottom Handles | Skill, Tools | Skill, Tools |
| Left Handles | Input, Memory, Task | Input, Memory, Task |
| Backend Method | `execute_agent()` | `execute_chat_agent()` |
| Async Delegation | Yes (fire-and-forget) | Yes (fire-and-forget) |

### Unified Tool Calling Architecture

Both AI Agent and Zeenie use the **same tool calling pattern**:

1. **Tool Building**: Both use `_build_tool_from_node()` to create schema-only tools
2. **Tool Execution**: Both use `execute_tool()` from `handlers/tools.py`
3. **Supported Tools**: `calculatorTool`, `currentTimeTool`, `duckduckgoSearch`, `androidTool`, `httpRequest`, `braveSearch`, `serperSearch`, `perplexitySearch`

**Tool Execution Flow:**
```
Tool Node (connected to input-tools)
        ↓
Handler collects tool_data: {node_id, node_type, parameters, label, connected_services}
        ↓
AIService._build_tool_from_node() → creates schema-only StructuredTool
        ↓
_run_agent_loop binds tools via chat_model.bind_tools(tools)
        ↓
LLM decides to call tool with arguments
        ↓
tool_executor callback → execute_tool() from handlers/tools.py
        ↓
Dispatch to handler: _execute_http_request(), _execute_calculator(), etc.
```

**Key Files:**
| File | Purpose |
|------|---------|
| `server/services/ai.py` | `_build_tool_from_node()` - builds schema-only tools for both agents |
| `server/services/handlers/tools.py` | `execute_tool()` - dispatches to specific handlers |
| `server/services/handlers/ai.py` | Collects `tool_data` from `input-tools` handle |

### Zeenie Input Methods

Zeenie accepts input in two ways:

1. **Template Variable (Explicit)**: Set the Prompt field to `{{chatTrigger.message}}` or `{{whatsappReceive.text}}`
   - Templates are resolved by `ParameterResolver` before handler execution
   - Supports nested paths: `{{nodeName.nested.field}}`

2. **Auto-Fallback (Implicit)**: Leave the Prompt field empty
   - Handler detects nodes connected to `input-main` handle
   - Reads output from `context.get('outputs', {}).get(source_node_id)`
   - Extracts text from `message`, `text`, `content` fields (in order)
   - Falls back to string representation of entire output

**Example Workflow:**
```
Chat Trigger → Zeenie ← HTTP Skill (SKILL.md context)
                         ← HTTP Request (tool node)
```

The Zeenie will:
- Load SKILL.md instructions from connected skill nodes
- Build tools from connected tool nodes (httpRequest, calculatorTool, etc.)
- Use `_run_agent_loop` for tool execution when tools are connected

### Backend Handlers
Wave 11: `handle_ai_agent` / `handle_chat_agent` were deleted. Agent execution now flows through `BaseNode.execute()` + `NodeContext.from_legacy()` via the node registry (`server/nodes/agent/`). Connection collection is handled by the agent node classes, which internally call `_collect_agent_connections()` to:
- Scans edges for nodes connected to `input-memory`, `input-skill`, `input-tools`, `input-main`/`input-chat` handles
- Returns a 4-tuple: `(memory_data, skill_data, tool_data, input_data)`
- Handles MasterSkill expansion into individual skill entries

Both call corresponding AI service methods:
- `AIService.execute_agent()` - Runs `_run_agent_loop` with tool binding
- `AIService.execute_chat_agent()` - Runs `_run_agent_loop` (when tools) or single `ainvoke` (no tools), with skills providing context + tools

### Async Agent Delegation (Nested Agents)

AI Agents can delegate tasks to other agents connected to their `input-tools` handle. This enables hierarchical agent architectures where a parent agent can spawn child agents that work independently.

**Architecture: Fire-and-Forget Pattern**
```
Parent Agent calls "delegate_to_ai_agent" tool
       |
Tool handler spawns asyncio.Task for Child Agent
       |
Returns immediately: {"status": "delegated", "task_id": "..."}
       |
Parent Agent continues working
       |
Child Agent executes independently in background
       |
Child broadcasts its own status updates (executing, success, error)
```

**How It Works:**
1. Connect a Child Agent (aiAgent/chatAgent) to Parent Agent's `input-tools` handle
2. Parent sees a tool like `delegate_to_ai_agent` with schema `{task: string, context?: string}`
3. When Parent calls the tool, handler spawns Child as `asyncio.create_task()`
4. Tool returns immediately with `{"status": "delegated", "task_id": "..."}`
5. Parent continues without waiting
6. Child executes with its own connected tools, skills, and memory
7. Both agents can execute simultaneously with independent status indicators

**Key Files:**
| File | Purpose |
|------|---------|
| `server/services/ai.py` | `DelegateToAgentSchema` in `_get_tool_schema()`, injects `ai_service`, `database`, `nodes`, `edges` into tool config |
| `server/services/handlers/tools.py` | `_execute_delegated_agent()` - spawns child as background task, `get_delegated_task_status()` utility |
| `server/services/handlers/ai.py` | Passes `context` to `execute_agent()`/`execute_chat_agent()` for nested delegation |

**Design Decisions:**
- **Memory Isolation**: Child uses its own connected memory, not shared with Parent
- **Error Isolation**: Child errors don't propagate to Parent - logged and broadcast independently
- **Task Tracking**: Background tasks tracked in `_delegated_tasks` dict, cleaned up on completion

### Specialized AI Agents

The system ships specialized agent variants — each is a folder under [`server/nodes/agent/`](./server/nodes/agent/) with `__init__.py` declaring a `SpecializedAgentBase` subclass. Authoritative list: glob that folder. Per-type display name / subtitle / description live on the plugin class; icon comes from `<plugin>/icon.svg` (or visuals.json emoji fallback); color comes from `<plugin>/meta.json` (post-F2). Do not maintain a hand-list of agent types in this doc — it drifts on every plugin add.

Standard handle topology (declared on `SpecializedAgentBase` via `std_agent_handles()`):
- **Left**: `input-main` (Input, 30%), `input-memory` (Memory, 55%), `input-task` (Task, 85%)
- **Bottom**: `input-skill` (Skill, 25%), `input-tools` (Tool, 75%)
- **Top**: `output-top` (Output)

**Team Lead Agents** (`orchestrator_agent`, `ai_employee`) declare an extra `input-teammates` handle on the bottom (50%) for delegation. They are listed in `TEAM_LEAD_TYPES` in [`client/src/components/TeamMonitorNode.tsx`](./client/src/components/TeamMonitorNode.tsx) (frontend tribal array — flagged as tech debt; see "remaining tribal arrays" note).

`AIAgentNode.tsx` is type-agnostic: it calls `useNodeSpec(type)` and renders whatever handles/icon/color the spec returns — no `AGENT_CONFIGS` map.

## Architecture Patterns
- **Plugin-first (Wave 11).** One folder per plugin under [`server/nodes/<group>/<plugin>/`](./server/nodes/) rooted at `__init__.py`, subclassing `BaseNode` / `ActionNode` / `TriggerNode` / `ToolNode`. Auto-registers via `__init_subclass__`. See [`docs-internal/plugin_system.md`](./docs-internal/plugin_system.md).
- **Backend NodeSpec is the SSOT** for icon, colour, handles, params, output schema, uiHints, palette group. Frontend consumes via `useNodeSpec(type)` and adapts the JSON Schema → `INodeTypeDescription` shape through [`adapters/nodeSpecToDescription.ts`](./client/src/adapters/nodeSpecToDescription.ts) (legacy interface kept as a render contract; not a parallel schema system).
- **Component-driven frontend.** shadcn/ui primitives + Tailwind tokens; canvas nodes are spec-driven (see "Spec-driven component design" above).
- **State management.** TanStack Query owns server-backed data; Zustand (`useAppStore`, `nodeStatusStore`) owns UI state and slice-subscribed high-frequency push state. Slice-selector reads only — never whole-store destructure (see "Frontend Performance Architecture").
- **Execution pipeline.** Temporal-distributed activities for plugin execution; `WorkflowExecutor` for parallel orchestration; per-node retry / timeout / heartbeat declared on the plugin class.

## File Structure Cleanup
**Removed Files:**
- `src/nodeDefinitions.ts` + `src/nodeDefinitions/` (27 files) — superseded by backend NodeSpec SSOT; frontend resolves specs via `lib/nodeSpec.ts` + `adapters/nodeSpecToDescription.ts`
- `src/nodeDefinitions.backup.ts` (backup file)
- `src/schemas/` directory (unused schema system)
- `src/utils/schemaParser.ts` (legacy parser)
- `src/utils/nodeSchemaParser.ts` (unused modern parser)
- `src/types/NodeSchema.ts` (legacy schema types)

**Cleaned Code:**
- Removed unused imports and dead functions
- Eliminated legacy NodeDefinition interface  
- Streamlined parameter handling logic
- Maintained backward compatibility only where actively used

## Testing & Validation
```bash
# Development server test
curl -I http://localhost:3001

# TypeScript validation
npx tsc --noEmit

# Build verification
npm run build

# Test WebSocket connection for remote Android devices
python test_websocket.py
```

### WebSocket Testing (`test_websocket.py`)
- Tests health endpoint configured via `WEBSOCKET_URL` environment variable
- Tests stats endpoint
- Tests WebSocket connection
- Validates connection establishment and ping/pong messaging
- Comprehensive test results with pass/fail status

## Production Deployment

### Docker Deployment
The project deploys using Docker Compose with nginx reverse proxy.

#### Deploy Script (`deploy.sh`)
```bash
# Deploy to server (configure DEPLOY_HOST in .env)
./deploy.sh [HOST]
```

**Deployment Steps:**
1. Build Docker images locally (`docker-compose -f docker-compose.prod.yml build`)
2. Save and compress images (`docker save | gzip`)
3. Upload to GCP via SCP
4. Deploy on remote (`docker-compose up -d`)
5. Configure nginx reverse proxy with SSL
6. Auto-cleanup dangling Docker images

#### Docker Configuration (4-Container Stack)

**Services:**
| Container | Image | Port | Description |
|-----------|-------|------|-------------|
| redis | redis:7-alpine | 6379 | Cache and pub/sub for workflows |
| backend | machinaos-backend | 3010 | FastAPI Python backend |
| frontend | machinaos-frontend | 3000 | React app via nginx |
| whatsapp | machinaos-whatsapp | 5000 | Go WhatsApp bridge service |

**Frontend (`client/Dockerfile`):**
- Multi-stage build: Node.js builder → nginx:alpine production
- Serves static files via nginx on port 80 (mapped to 3000)
- Size: ~54 MB

**Backend (`server/Dockerfile`):**
- Python 3.12-slim base with Node.js 22.x for JS/TS execution
- Includes Playwright chromium for JS-rendered web scraping (crawleeScraper node)
- Includes persistent Node.js server (Express + tsx) on port 3020
- Optimized bytecode compilation (`python -O -m compileall`)
- Health check endpoint on port 3010
- Startup script (`start.sh`) runs both Python and Node.js servers (must have LF line endings, not CRLF)
- Depends on: redis, whatsapp
- Size: ~800 MB (includes Playwright chromium)

**WhatsApp (`docker/Dockerfile.whatsapp`):**
- Uses npm package `whatsapp-rpc` with pre-built binaries
- Node.js 20-alpine base with `npx whatsapp-rpc api --foreground`
- Binary downloaded from GitHub releases during npm postinstall
- Exposed on port 9400 (configurable via `PORT`, `WHATSAPP_RPC_PORT` env vars, or `--port` CLI flag)
- QR codes generated as base64 PNG in memory (no file I/O)
- Also published to PyPI as `whatsapp-rpc` (async Python client)
- Size: ~150 MB (includes Node.js runtime)

**Redis:**
- Official redis:7-alpine image
- Healthcheck: `redis-cli ping`
- Persistent volume: `redis_data`
- No authentication (internal network only)

**Development Compose (`docker-compose.yml`):**
```yaml
services:
  # Redis uses profiles - only starts when REDIS_ENABLED=true
  redis:
    image: redis:7-alpine
    profiles:
      - redis  # Only starts with --profile redis flag
    ports: ["${REDIS_PORT:-6379}:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  backend:
    build: ./server
    ports: ["${PYTHON_BACKEND_PORT:-3010}:${PYTHON_BACKEND_PORT:-3010}"]
    volumes:
      - ./server:/app
      - /app/nodejs/node_modules  # Preserve Linux binaries (prevents Windows esbuild conflict)
    depends_on:
      whatsapp: { condition: service_healthy }  # No Redis dependency
    environment:
      - REDIS_ENABLED=${REDIS_ENABLED:-false}
      - REDIS_URL=redis://redis:6379

  frontend:
    build: ./client
    ports: ["${VITE_CLIENT_PORT:-3000}:${VITE_CLIENT_PORT:-3000}"]

  whatsapp:
    build:
      context: .
      dockerfile: docker/Dockerfile.whatsapp
    ports: ["${WHATSAPP_RPC_PORT:-9400}:${WHATSAPP_RPC_PORT:-9400}"]
```

**Docker Scripts Wrapper (`scripts/docker.js`):**
Auto-detects `REDIS_ENABLED` in `.env` and adds `--profile redis` flag when enabled:
```javascript
// Reads .env and checks REDIS_ENABLED value
function isRedisEnabled() {
  const content = readFileSync(resolve(ROOT, '.env'), 'utf8');
  const match = content.match(/^REDIS_ENABLED\s*=\s*(.+)$/m);
  const value = match?.[1].trim().toLowerCase();
  return value === 'true' || value === '1' || value === 'yes';
}

// Adds --profile redis when enabled
if (isRedisEnabled()) {
  composeArgs.push('--profile', 'redis');
}
```

**Production Compose (`docker-compose.prod.yml`):**
```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: ["redis_data:/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  whatsapp:
    build:
      context: .
      dockerfile: docker/Dockerfile.whatsapp
    ports: ["9400:9400"]
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:9400/health"]

  backend:
    build: ./server
    ports: ["3010:3010"]
    depends_on:
      redis: { condition: service_healthy }
      whatsapp: { condition: service_healthy }
    environment:
      - REDIS_ENABLED=true
      - REDIS_URL=redis://redis:6379

  frontend:
    build: ./client
    ports: ["3000:80"]
```

#### Nginx Configuration
Located at `/etc/nginx/sites-available/flow.zeenie.xyz`:
- Frontend: `/` → `http://127.0.0.1:3000`
- Backend API: `/api/` → `http://127.0.0.1:3010/api/`
- WebSocket: `/ws/` → `http://127.0.0.1:3010/ws/` (with upgrade headers)
- Webhook: `/webhook/` → `http://127.0.0.1:3010/webhook/`
- Health: `/health` → `http://127.0.0.1:3010/health`
- SSL via Let's Encrypt certbot

#### Environment Configuration

**Development** (`server/.env`):
- `DEBUG=true`
- `CORS_ORIGINS` includes localhost ports
- `REDIS_ENABLED=false` (uses SQLite cache for local dev)

**Production** (Docker environment variables):
- `DEBUG=false`
- `CORS_ORIGINS=["https://your-domain.com"]`
- `REDIS_ENABLED=true` (Docker Redis container)
- `REDIS_URL=redis://redis:6379`
- Environment set in `docker-compose.prod.yml`, not `.env` file

#### Frontend API URL Resolution
The frontend automatically detects production vs development:

```typescript
// client/src/config/api.ts
const isProduction = typeof window !== 'undefined' &&
  !window.location.hostname.includes('localhost') &&
  !window.location.hostname.includes('127.0.0.1');

return {
  PYTHON_BASE_URL: isProduction ? '' : 'http://localhost:3010',
};
```

- **Production**: Empty base URL = relative URLs (same origin)
- **Development**: Explicit `http://localhost:3010`

WebSocket URL derived from base URL:
```typescript
// client/src/contexts/WebSocketContext.tsx
if (!baseUrl) {
  // Production: use current origin
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${wsProtocol}://${window.location.host}/ws/status`;
}
```

#### Resource Usage (GCP e2-micro)
| Resource | Value |
|----------|-------|
| CPU | 2 cores (Intel Xeon @ 2.20GHz) |
| RAM | 1.9 GB total, ~820 MB used |
| Disk | 14 GB total, ~9.2 GB used |
| Backend Memory | ~144 MB |
| Frontend Memory | ~3.4 MB |

#### Useful Commands
```bash
# View logs (all containers)
ssh $DEPLOY_HOST 'cd /opt/machinaos && docker-compose logs -f'

# View specific service logs
ssh $DEPLOY_HOST 'cd /opt/machinaos && docker-compose logs -f backend'
ssh $DEPLOY_HOST 'cd /opt/machinaos && docker-compose logs -f whatsapp'

# Restart all services
ssh $DEPLOY_HOST 'cd /opt/machinaos && docker-compose restart'

# Restart specific service
ssh $DEPLOY_HOST 'cd /opt/machinaos && docker-compose restart backend'

# Check container status
ssh $DEPLOY_HOST 'docker ps'

# Check resource usage
ssh $DEPLOY_HOST 'docker stats --no-stream'

# Check Redis connection
ssh $DEPLOY_HOST 'docker exec machinaos-redis-1 redis-cli ping'

# Check backend health (shows redis_enabled status)
curl -s https://$DEPLOY_DOMAIN/health | jq

# Clean up Docker resources (if disk full)
ssh $DEPLOY_HOST 'docker system prune -af && docker builder prune -af'
```

### Local Docker Development
For testing the full production stack locally:

```bash
# Build and start all containers
docker-compose -f docker-compose.prod.yml up --build

# Access locally
# Frontend: http://localhost:3000
# Backend API: http://localhost:3010
# WhatsApp RPC: http://localhost:9400
# Redis: localhost:6379

# Stop all containers
docker-compose -f docker-compose.prod.yml down

# Remove volumes (clean slate)
docker-compose -f docker-compose.prod.yml down -v
```

### Local Development Build
```bash
# Create optimized build
npm run build

# Serve built files locally
npm run preview
```

## Authentication System

### Overview
n8n-inspired authentication system with JWT tokens stored in HttpOnly cookies. Authentication can be completely disabled for development or supports two deployment modes for different use cases.

### Authentication Toggle
| Setting | Environment Variable | Description |
|---------|---------------------|-------------|
| **Enabled** | `VITE_AUTH_ENABLED=true` | Require login (default) |
| **Disabled** | `VITE_AUTH_ENABLED=false` | Bypass authentication, anonymous access |

When `VITE_AUTH_ENABLED=false`:
- Frontend skips login page entirely
- User is set to anonymous with owner privileges
- No backend auth API calls are made
- Useful for local development and testing

### Deployment Modes (when auth enabled)
| Mode | Environment Variable | Description |
|------|---------------------|-------------|
| **Single Owner** | `AUTH_MODE=single` | First user becomes owner, registration disabled after |
| **Multi User** | `AUTH_MODE=multi` | Open registration for cloud deployments |

### Architecture
```
Frontend (LoginPage.tsx) → AuthContext → Backend (/api/auth/*) → JWT Cookie
                                              ↓
                                        AuthMiddleware
                                              ↓
                                      Protected Routes
```

### Backend Implementation

#### User Model (`server/models/auth.py`)
```python
class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    display_name: str
    is_owner: bool = Field(default=False)
    is_active: bool = Field(default=True)
    created_at: datetime
    last_login: Optional[datetime]

    def set_password(self, password: str) -> None:
        # Uses bcrypt for secure hashing

    def verify_password(self, password: str) -> bool:
        # Verifies against bcrypt hash
```

#### Auth Service (`server/services/user_auth.py`)
- `register_user()` - Creates new user, sets as owner if first user in single mode
- `authenticate_user()` - Validates credentials, returns user
- `create_token()` - Generates JWT token
- `verify_token()` - Validates JWT token
- `get_auth_status()` - Returns mode, registration availability, user count

#### Auth Router (`server/routers/auth.py`)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/status` | GET | Get auth mode and registration status |
| `/api/auth/register` | POST | Register new user |
| `/api/auth/login` | POST | Login and set cookie |
| `/api/auth/logout` | POST | Clear auth cookie |
| `/api/auth/me` | GET | Get current user info |

#### Auth Middleware (`server/middleware/auth.py`)
Protects all routes except public paths:
```python
PUBLIC_PATHS = frozenset([
    "/health", "/docs", "/openapi.json", "/redoc",
    "/api/auth/status", "/api/auth/login", "/api/auth/register", "/api/auth/logout",
])
PUBLIC_PREFIXES = ("/webhook/",)
```

### Frontend Implementation

#### Auth Context (`client/src/contexts/AuthContext.tsx`)
```typescript
interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  authMode: 'single' | 'multi';
  canRegister: boolean;
  login: (email: string, password: string) => Promise<boolean>;
  register: (email: string, password: string, displayName: string) => Promise<boolean>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}
```

#### Protected Route (`client/src/components/auth/ProtectedRoute.tsx`)
Wraps protected content, shows LoginPage if not authenticated:
```typescript
const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <LoadingSpinner />;
  if (!isAuthenticated) return <LoginPage />;
  return <>{children}</>;
};
```

#### Login Page (`client/src/components/auth/LoginPage.tsx`)
- Dracula-themed login/register form
- Switches between login and register based on `canRegister`
- Displays errors from auth context

### Configuration
Environment variables in `.env`:
```bash
# Authentication Toggle (frontend - Vite)
VITE_AUTH_ENABLED=true              # 'true' or 'false' - disable to bypass login

# Authentication Mode (backend)
AUTH_MODE=single                    # 'single' or 'multi'
JWT_SECRET_KEY=your-secret-key-32   # Min 32 chars
JWT_EXPIRE_MINUTES=10080            # 7 days
JWT_COOKIE_NAME=machina_token
JWT_COOKIE_SECURE=false             # true for HTTPS
JWT_COOKIE_SAMESITE=lax
```

### Race Condition Handling
The AuthContext includes retry logic with exponential backoff to handle the case where frontend starts before backend is ready:
- 5 retries with exponential backoff (1s, 2s, 4s, 8s, 16s)
- Shows "Failed to connect to server" only after all retries exhausted
- Prevents false "not authenticated" errors during startup

### Cookie-Based Auth for API Calls
All API calls must include `credentials: 'include'` for HttpOnly cookie:
```typescript
// In workflowApi.ts, all fetch calls include:
fetch(url, { credentials: 'include' })
```

### WebSocket Authentication
WebSocket checks cookie before accepting connection:
```python
# In websocket.py
token = websocket.cookies.get(settings.jwt_cookie_name)
if not token:
    await websocket.close(code=4001, reason="Not authenticated")
    return
```

WebSocketProvider only connects when authenticated:
```typescript
// In WebSocketContext.tsx
const { isAuthenticated, isLoading: authLoading } = useAuth();

useEffect(() => {
  if (authLoading || !isAuthenticated) {
    // Disconnect if logged out
    return;
  }
  connect();
}, [isAuthenticated, authLoading]);
```

### Key Files
| File | Description |
|------|-------------|
| `client/src/config/api.ts` | API config with AUTH_ENABLED toggle |
| `client/src/contexts/AuthContext.tsx` | React auth state with retry logic |
| `client/src/components/auth/LoginPage.tsx` | Login UI |
| `client/src/components/auth/ProtectedRoute.tsx` | Route guard |
| `server/models/auth.py` | User SQLModel with bcrypt |
| `server/services/user_auth.py` | JWT creation/verification |
| `server/routers/auth.py` | REST endpoints |
| `server/middleware/auth.py` | Route protection |
| `server/core/config.py` | Settings with vite_auth_enabled field |

### Dependencies
```
# server/pyproject.toml
bcrypt>=4.1.0
python-jose[cryptography]>=3.3.0
email-validator>=2.0.0
```

## Encrypted Credentials System

### Overview
API keys and OAuth tokens are stored in a separate encrypted database (`credentials.db`) using Fernet encryption (AES-128-CBC + HMAC-SHA256). Following the n8n pattern, the encryption key is derived from a server-scoped config key (`API_KEY_ENCRYPTION_KEY` in `.env`) using PBKDF2, initialized at startup and persisting across restarts.

### Security Architecture
```
Server Startup
       ↓
API_KEY_ENCRYPTION_KEY (from .env) + Salt (from credentials.db)
       ↓
PBKDF2HMAC (SHA256, 600K iterations)
       ↓
Fernet Key (in-memory for application lifetime)
       ↓
EncryptionService.encrypt()/decrypt()
       ↓
credentials.db (encrypted ciphertext)
```

**Key Security Properties:**
- Server-scoped encryption key from `API_KEY_ENCRYPTION_KEY` in `.env` (n8n pattern)
- Key initialized at startup, persists across application lifetime
- Not tied to user sessions -- survives server restarts with valid JWT
- Salt stored in credentials database (not the main database)
- OWASP 2024 compliant: 600,000 PBKDF2 iterations

### Single Point of Access Pattern
**IMPORTANT**: All credential operations MUST go through `AuthService`. Routers should NEVER access `CredentialsDatabase` directly.

```python
# Correct: Use auth_service
auth_service = get_auth_service()
await auth_service.store_oauth_tokens(provider="google", ...)
tokens = await auth_service.get_oauth_tokens("google", customer_id="owner")

# Wrong: Direct database access
credentials_db = get_credentials_db()  # Don't do this in routers
await credentials_db.save_oauth_tokens(...)  # Don't do this
```

### Backend Implementation

#### EncryptionService (`server/core/encryption.py`)
```python
class EncryptionService:
    """Fernet encryption with PBKDF2 key derivation."""

    def initialize(self, password: str, salt: bytes) -> None:
        """Derive key from password using PBKDF2HMAC."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600_000,  # OWASP 2024 recommendation
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt and return base64 ciphertext."""

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64 ciphertext."""

    def clear(self) -> None:
        """Clear encryption key from memory."""

    def is_initialized(self) -> bool:
        """Check if encryption is ready."""
```

#### CredentialsDatabase (`server/core/credentials_database.py`)
```python
class CredentialsDatabase:
    """Async SQLite database for encrypted credentials."""

    async def initialize(self) -> bytes:
        """Initialize database, create tables, return salt."""

    async def save_api_key(self, provider: str, key: str, metadata: Dict = None) -> bool
    async def get_api_key(self, provider: str) -> Optional[str]
    async def delete_api_key(self, provider: str) -> bool

    async def save_oauth_tokens(self, provider, access_token, refresh_token, ...) -> bool
    async def get_oauth_tokens(self, provider, customer_id="owner") -> Optional[Dict]
    async def delete_oauth_tokens(self, provider, customer_id="owner") -> bool
```

#### AuthService OAuth Methods (`server/services/auth.py`)
```python
class AuthService:
    """Single point of access for all credentials."""

    # Memory-only cache for decrypted credentials
    _api_key_cache: Dict[str, str] = {}
    _oauth_cache: Dict[str, Dict[str, Any]] = {}

    async def store_api_key(self, provider: str, key: str) -> bool
    async def get_api_key(self, provider: str) -> Optional[str]
    async def delete_api_key(self, provider: str) -> bool

    async def store_oauth_tokens(self, provider, access_token, refresh_token, ...) -> bool
    async def get_oauth_tokens(self, provider, customer_id="owner") -> Optional[Dict]
    async def remove_oauth_tokens(self, provider, customer_id="owner") -> bool

    def clear_cache(self) -> None:
        """Clear all cached credentials."""
```

#### UserAuthService Integration (`server/services/user_auth.py`)
```python
async def login(self, email: str, password: str):
    # ... verify credentials ...
    # Initialize encryption with user's password
    await self._initialize_encryption(password)
    return user, None

async def _initialize_encryption(self, password: str) -> None:
    salt = await self.credentials_db.initialize()
    self.encryption.initialize(password, salt)

def logout(self) -> None:
    self.encryption.clear()  # Clear key from memory
```

### Multi-Backend Support

For deployment flexibility, credentials can be stored in different backends:

#### Backend Options
| Backend | Use Case | Configuration |
|---------|----------|---------------|
| **Fernet** (default) | Local development, single-server | `CREDENTIAL_BACKEND=fernet` |
| **Keyring** | Desktop apps (OS-native storage) | `CREDENTIAL_BACKEND=keyring` |
| **AWS Secrets Manager** | Cloud deployments | `CREDENTIAL_BACKEND=aws` |

#### credential_backends.py
```python
class CredentialBackend(ABC):
    async def store(self, key: str, value: str, metadata: Dict = None) -> bool
    async def retrieve(self, key: str) -> Optional[str]
    async def delete(self, key: str) -> bool
    def is_available(self) -> bool

class FernetBackend(CredentialBackend):
    """Default: Fernet-encrypted SQLite."""

class KeyringBackend(CredentialBackend):
    """OS-native: Windows Credential Locker, macOS Keychain, Linux Secret Service."""
    SERVICE_NAME = "MachinaOS"

class AWSSecretsBackend(CredentialBackend):
    """AWS Secrets Manager for cloud deployments."""

def create_backend(settings, credentials_db=None) -> CredentialBackend:
    """Factory with automatic fallback to Fernet."""
```

### Configuration

Environment variables in `.env`:
```bash
# Credentials Database
CREDENTIALS_DB_PATH=credentials.db

# Backend Selection
CREDENTIAL_BACKEND=fernet    # fernet, keyring, or aws

# AWS Secrets Manager (when CREDENTIAL_BACKEND=aws)
AWS_SECRET_ARN=arn:aws:secretsmanager:us-east-1:123456789:secret:machinaos-creds
AWS_REGION=us-east-1
```

### Key Files
| File | Description |
|------|-------------|
| `server/core/encryption.py` | Fernet encryption with PBKDF2 key derivation |
| `server/core/credentials_database.py` | Async SQLite for encrypted credentials |
| `server/core/credential_backends.py` | Multi-backend abstraction (Fernet, Keyring, AWS) |
| `server/services/auth.py` | AuthService with OAuth methods (single point of access) |
| `server/services/user_auth.py` | Encryption initialization on login/logout |
| `server/core/config.py` | credential_backend, aws_secret_arn settings |

### Dependencies
```toml
# server/pyproject.toml
[project]
dependencies = [
    "cryptography>=44.0.0",  # Fernet encryption
]

[project.optional-dependencies]
keyring = ["keyring>=25.0.0"]  # OS-native credential storage
aws = ["boto3>=1.34.0"]        # AWS Secrets Manager
```

### Design Decisions
- **No Migration**: Users re-enter API keys after upgrade (simpler, more secure)
- **Memory-Only Cache**: Decrypted credentials never written to disk/Redis
- **Separate Database**: `credentials.db` isolated from main `machina.db`
- **Password-Derived Key**: Encryption key not stored anywhere
- **Single Point of Access**: AuthService prevents direct database access from routers

## Example Workflows

### Overview
Example workflows are pre-built workflow templates that auto-load on first use. They provide users with starting points to explore the platform's capabilities. Examples are stored as JSON files in the `workflows/` folder at the project root.

### Architecture
```
workflows/                        # Example workflow JSON files (project root)
├── hello_world.json
├── zeenie_chat.json
└── ...

server/services/
└── example_loader.py             # Loads and imports examples

server/models/database.py         # UserSettings.examples_loaded flag
server/core/database.py           # Migration for examples_loaded column
server/routers/database.py        # Auto-load logic in get_all_workflows
```

### How It Works
1. **First Fetch Detection**: When `get_all_workflows` API is called, it checks `UserSettings.examples_loaded`
2. **Auto-Import**: If `examples_loaded=false`, imports all JSON files from `workflows/` folder
3. **Mark Complete**: Sets `examples_loaded=true` to prevent re-import on subsequent fetches
4. **Anonymous Support**: Uses `user_id="default"` when `VITE_AUTH_ENABLED=false`

### Workflow JSON Format
Example workflows use the same format as UI exports:
```json
{
  "id": "hello_world",
  "name": "Hello World",
  "description": "A simple workflow with a start node",
  "nodes": [
    {
      "id": "start_1",
      "type": "start",
      "position": {"x": 250, "y": 150},
      "data": {"label": "Start"}
    }
  ],
  "edges": [],
  "nodeParameters": {
    "start_1": { "someParam": "value" }
  },
  "version": "0.0.36"
}
```

**Fields:**
| Field | Description |
|-------|-------------|
| `id` | Unique identifier (prefixed with `example_` when imported) |
| `name` | Display name in workflow sidebar |
| `description` | Optional description |
| `nodes` | Array of node objects with id, type, position, data |
| `edges` | Array of edge connections between nodes |
| `nodeParameters` | Optional map of node_id to parameter objects (saved to DB on import) |
| `version` | App version (e.g., "0.0.36") |

### Key Files
| File | Description |
|------|-------------|
| `workflows/*.json` | Example workflow JSON files |
| `server/services/example_loader.py` | `get_example_workflows()`, `import_examples_for_user()` |
| `server/models/database.py` | `UserSettings.examples_loaded` field |
| `server/core/database.py` | Migration adds `examples_loaded` column |
| `server/routers/database.py` | Auto-load check in `get_all_workflows` |

### Example Loader Service
```python
# server/services/example_loader.py
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "workflows"

def get_example_workflows() -> List[Dict[str, Any]]:
    """Load all example workflow JSON files from disk."""

async def import_examples_for_user(database) -> int:
    """Import all examples using existing database.save_workflow().
    Returns count of workflows imported."""
```

### Auto-Load Logic
```python
# server/routers/database.py - get_all_workflows endpoint
user_id = "default"
settings = await database.get_user_settings(user_id)

if not settings or not settings.get("examples_loaded", False):
    count = await import_examples_for_user(database)
    if count > 0:
        logger.info(f"Auto-loaded {count} example workflows")
    current = settings or {}
    current["examples_loaded"] = True
    await database.save_user_settings(current, user_id)
```

### Adding Custom Examples
1. Export a workflow from the UI (File > Export)
2. Copy the JSON file to `workflows/` folder at project root
3. Edit the `id` and `name` fields as needed
4. Delete `server/machina.db` (or set `examples_loaded=false` in database)
5. Restart server - examples auto-load on first workflow list fetch

### Database Migration
The `examples_loaded` column is automatically added to existing databases:
```python
# server/core/database.py - _migrate_user_settings()
if "examples_loaded" not in columns:
    await conn.execute(text(
        "ALTER TABLE user_settings ADD COLUMN examples_loaded BOOLEAN DEFAULT 0"
    ))
```

## Onboarding Service

### Overview
Multi-step welcome wizard that appears after first launch, guiding users through platform capabilities. Database-backed, skippable, resumable, and replayable from Settings.

See **[Onboarding Service](./docs-internal/onboarding.md)** for full documentation.

### Architecture
- **5-step wizard** using existing `Modal` component (autoHeight, maxWidth 580px, maxHeight 70vh) + Ant Design `Steps`, `Card`, `Button`, `Typography`, `Tag`
- **Database persistence** via `UserSettings.onboarding_completed` + `UserSettings.onboarding_step`
- **No new WebSocket handlers** -- reuses `get_user_settings` / `save_user_settings`
- **Existing users** auto-skip via migration (`examples_loaded=1` -> `onboarding_completed=1`)

### Steps

| Step | Component | Title | Purpose |
|------|-----------|-------|---------|
| 0 | `WelcomeStep` | Welcome to MachinaOs | Platform intro + feature highlights |
| 1 | `ConceptsStep` | Key Concepts | Nodes, Edges, Agents, Skills, Normal/Dev Mode |
| 2 | `ApiKeyStep` | API Key Setup | Provider list + "Open Credentials" button |
| 3 | `CanvasStep` | Canvas Tour | Visual UI layout diagram + keyboard shortcuts |
| 4 | `GetStartedStep` | Get Started | Example workflows, quick recipe, tips |

### Key Files
| File | Description |
|------|-------------|
| `client/src/hooks/useOnboarding.ts` | State hook with WebSocket persistence |
| `client/src/components/onboarding/OnboardingWizard.tsx` | Main wizard with Ant Design Steps |
| `client/src/components/onboarding/steps/*.tsx` | 5 step components using antd + @ant-design/icons |
| `client/src/Dashboard.tsx` | Renders wizard + passes `reopenTrigger` |
| `client/src/components/ui/SettingsPanel.tsx` | "Replay Welcome Guide" button in Help section |
| `server/models/database.py` | `UserSettings.onboarding_completed`, `onboarding_step` |
| `server/core/database.py` | Migration + CRUD for onboarding fields |

### Replay from Settings
- SettingsPanel has a "Replay Welcome Guide" button in the Help section
- Clicking it: closes Settings, increments `onboardingReopenTrigger` in Dashboard
- `useOnboarding` detects trigger change, resets state, reopens wizard from step 0

### Adding New Steps
1. Create `client/src/components/onboarding/steps/NewStep.tsx` using Ant Design components
2. Import in `OnboardingWizard.tsx`, add to `renderStep()` switch and `stepItems` array
3. Update `TOTAL_STEPS` in `useOnboarding.ts`

## AI Chat Model Development Guide

### Adding a new AI provider (post-Wave-11)

A new chat-model provider is **a self-contained folder** under `server/nodes/model/<provider>_chat_model/` with `__init__.py` declaring a `ChatModelBase` subclass. The plugin auto-registers via `BaseNode.__init_subclass__`; the frontend renders it through `SquareNode` from the emitted NodeSpec without a single TS change.

```python
# server/nodes/model/openrouter_chat_model/__init__.py
class OpenRouterChatModel(ChatModelBase):
    type = "openrouterChatModel"
    metadata = NodeMetadata(
        display_name="OpenRouter",
        icon="lobehub:openrouter",   # asset:<key>, lobehub:<brand>, or emoji
        color="#6366F1",
        component_kind="model",       # routes to SquareNode in Dashboard.tsx
    )

    class Params(ChatModelBase.Params):
        # provider-specific overrides; everything else inherits from ChatModelBase
        ...
```

The native chat path lives in `server/services/llm/providers/<provider>.py` (Protocol-based, see [Native LLM SDK](./docs-internal/native_llm_sdk.md)). The LangChain agent path uses `ChatOpenAI` with a custom `base_url` from `server/config/llm_defaults.json` -- no Python branching needed for OpenAI-compatible APIs.

Credentials live in `server/nodes/model/_credentials.py` (one `Credential` subclass per provider) and surface in the Credentials Modal automatically. There is no `nodeDefinitions/` to edit, no `ModelNode.tsx` to update, no `Dashboard.tsx` switch to extend.

### Key implementation files

| File | Purpose |
|---|---|
| `server/nodes/model/<provider>_chat_model.py` | Plugin entry — metadata + Params + auto-registers |
| `server/nodes/model/_credentials.py` | `Credential` subclass per provider |
| `server/config/llm_defaults.json` | base_url + supported_params + temperature constraints (no hardcoded URLs in Python) |
| `server/services/llm/providers/<provider>.py` | Native SDK provider (Protocol-based) |
| `server/services/ai.py` | Routing: native chat path + LangChain agent path |
| `client/src/Dashboard.tsx` | Generic `COMPONENT_BY_KIND` dispatch — no per-provider entry needed |

## Simple Memory System

### Overview
The Simple Memory node provides markdown-based conversation history storage for AI agents. It connects to the AI Agent's `input-memory` handle to provide context from previous conversations. Memory is stored in markdown format, visible and editable directly in the parameter panel, with optional long-term vector storage for semantic retrieval.

### Architecture
```
Simple Memory Node → (memory output) → AI Agent (input-memory handle)
     ↓                                      ↓
   Markdown editor                    Parses markdown, saves new exchanges
   (visible in UI)                    Trims to window, archives to vector DB
```

### Memory Format
Conversation history is stored in markdown format with timestamps:
```markdown
# Conversation History

### **Human** (2025-01-30 14:23:45)
What is the weather like today?

### **Assistant** (2025-01-30 14:23:48)
I don't have access to real-time weather data...
```

### Key Features
- **Editable UI**: Conversation history visible in markdown editor in parameter panel
- **Window-Based Trimming**: Keeps last N message pairs, archives old messages
- **Long-Term Memory**: Optional vector DB storage for semantic retrieval of archived messages
- **Uses LangChain's InMemoryVectorStore**: Per-session vector stores with HuggingFaceEmbeddings (BAAI/bge-small-en-v1.5)

### Key Files
- **Node Definition**: `server/nodes/skill/simple_memory.py` - simpleMemory plugin (NodeSpec + execute). Carries an internal `last_session_id` field (hidden in UI) used by the claude_code_agent bridge for native session resume.
- **Memory Package**: `server/services/memory/` — promoted from the old `services/memory.py` so each concern owns its own module:
  - `services/memory/markdown.py` — `parse_memory_markdown`, `append_to_memory_markdown`, `trim_markdown_window` (the canonical helpers every agent uses; **previously inlined into `services/ai.py`**, now imported from here)
  - `services/memory/jsonl.py` — standalone JSONL `parse_jsonl` / `append_message` / `trim_window` (Anthropic Messages API shape, kept for future SDK migration; not used by any agent bridge today)
  - `services/memory/vector_store.py` — `get_memory_vector_store` + per-session `InMemoryVectorStore` cache
  - `services/memory/state.py` — `clear_agent_session_state(session_id, workflow_id, clear_long_term, memory_node_id)`; when `memory_node_id` is supplied it resets `memory_content` to the default placeholder AND wipes `last_session_id` server-side (single DB write)
  - `services/memory/__init__.py` — re-exports the full surface so existing `from services.memory import …` callers keep working
- **AI Integration (aiAgent / chatAgent / rlm_agent)**: `server/services/ai.py` — `execute_agent` / `execute_chat_agent` consume `memory_data`, call the markdown helpers via the package re-exports
- **CLI bridge (`claude_code_agent`)**: `server/services/cli_agent/service.py:_persist_memory` — appends turns to `memory_content` via the same markdown helpers AND saves `simpleMemory.last_session_id` from the most recent successful run's `r.session_id`. Always broadcasts `node_parameters_updated` so the simpleMemory parameter panel refetches live (no page reload needed).
- **Edge walker**: `server/services/plugin/edge_walker.py:_build_memory_entry` — surfaces `memory_content` + `window_size` + `long_term_enabled` + `retrieval_count` + `last_session_id` to every consuming agent

### Node Properties
| Property | Type | Default | Description |
|----------|------|---------|-------------|
| sessionId | string | 'default' | Unique identifier for conversation session |
| windowSize | number | 10 | Number of message pairs to keep in short-term memory |
| memoryContent | string (markdown) | Initial template | Editable conversation history in markdown format |
| longTermEnabled | boolean | false | Archive old messages to vector DB for semantic retrieval |
| retrievalCount | number | 3 | Number of relevant memories to retrieve from long-term storage (shown when longTermEnabled=true) |

### Memory Flow
1. **Load**: AI Agent reads `memoryContent` markdown from connected Simple Memory node
2. **Parse**: `_parse_memory_markdown()` converts markdown to LangChain messages
3. **Retrieve** (if enabled): Semantic search of vector store for relevant context
4. **Execute**: AI processes prompt with conversation history
5. **Append**: New human/AI messages appended to markdown
6. **Trim**: `_trim_markdown_window()` keeps last N pairs, returns removed texts
7. **Archive**: Removed messages stored in InMemoryVectorStore (if longTermEnabled)
8. **Save**: Updated markdown saved back to node parameters

### Usage Flow
1. Add Simple Memory node to workflow
2. Connect its output to AI Agent's `input-memory` handle (bottom-left diamond)
3. Configure session ID (use different IDs for separate conversations)
4. Optionally enable long-term memory for semantic retrieval
5. Run AI Agent - it automatically reads/updates memory content
6. View/edit conversation history in the markdown editor

### Design Decisions
- **Passive Node**: Memory node has no Run button - AI Agent reads its configuration directly when executed
- **Markdown Storage**: Human-readable format, editable in UI, easy to debug
- **Per-Session Vector Stores**: `_memory_vector_stores` dict keyed by session_id
- **Window + Archive**: Short-term (recent messages) + long-term (semantic retrieval) memory pattern
- **Auto-Save**: AI Agent automatically saves updated markdown content to node parameters

### claude_code_agent bridge — `--continue` + warm subprocess pool (NOT markdown injection)

When `simpleMemory` is connected to `claude_code_agent`, memory continuity is handled by claude itself — the agent does NOT re-inject markdown as a system prompt. Claude stores per-session transcripts under `<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_id>.jsonl` where `project_key = re.sub(r"[^a-zA-Z0-9.-]", "-", str(cwd))` (verified against the on-disk `data/claude-machina/projects/` listing).

**The three coupled mechanisms** (see [docs-internal/cli_agent_framework.md → Memory bridge](./docs-internal/cli_agent_framework.md#memory-bridge--simplememory--claude_code_agent) for full plumbing):

1. **Stable cwd.** The pool keeps `cwd = repo_root` for every memory-bound spawn (no per-task worktree carving). Same cwd every run → claude's `project_key` is constant → `--continue` / `--resume <UUID>` resolve against the same JSONL across batches.
2. **`--continue` first, intra-process thereafter.** [`claude_code_agent.execute_op`](./server/nodes/agent/claude_code_agent/__init__.py) sets `spec.continue_session = bool(memory_data)` so the first cold spawn for a memory-wired run emits `--continue` (claude auto-loads the most recent conversation under the cwd's `project_key`, per [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference)). Subsequent turns are written as stream-json to the SAME warm subprocess's `proc.stdin` — claude keeps the conversation in-process with the same `session_id` (verified: turn 1 + turn 2 share UUID within one process).
3. **`--resume <UUID>` crash recovery.** If the warm subprocess dies between batches, [`ClaudeSessionPool.acquire`](./server/services/cli_agent/session_pool.py) detects `process.returncode is not None`, captures the dead session's `current_session_uuid` (last seen on a `result` event), and respawns with `--resume <captured_uuid>` so the same on-disk JSONL keeps growing. Mutually exclusive with `--continue` in `providers/anthropic_claude.py:interactive_argv`.

**`memory_content` is the UI mirror, not the resume channel.** Claude reads its own JSONL via `--continue` / `--resume`. The markdown in `memory_content` is appended every successful run (same `append_to_memory_markdown` + `trim_markdown_window` helpers aiAgent uses) so the human reader sees the conversation. User edits to `memory_content` do not influence claude's next response.

**`simpleMemory.last_session_id` is display-only.** Pre-cutover code persisted the session UUID back to the memory node and emitted `--session-id <UUID5>` / `--resume <UUID>` on subsequent runs. That dance is gone — `claude_code_agent` no longer reads `last_session_id` from the memory data. The field is kept on the Pydantic params for back-compat and diagnostic display; clearing the memory wipes it.

**Parallel-batch guard.** When memory is wired AND `len(tasks) > 1`, `claude_code_agent` raises `NodeUserError` at handler entry — concurrent `--continue` spawns against one project_key would race claude's session-resolution.

**Live UI refresh — CloudEvents v1.0 envelope.** `_persist_memory` calls `broadcaster.broadcast_node_parameters_updated(...)` after `save_node_parameters`. The broadcast carries a `WorkflowEvent` envelope (`type: "com.machinaos.node.parameters.updated"`, `subject: <memory_node_id>`, `data: {node_id, parameters, version, source: "cli"}`) wrapped in the wire-key `{type: "node_parameters_updated", data: <envelope>}`. The FE handler in `client/src/contexts/WebSocketContext.tsx` casts to `WorkflowEvent<{node_id, parameters, version, source}>`, reads `inner.node_id || envelope.subject`, and routes the inner payload to `setNodeParameters` + `queryClient.setQueryData` so the simpleMemory parameter panel auto-refreshes the moment the turn completes. Pre-cutover the FE read the old flat top-level keys (`message.parameters`, `message.node_id`) and silently dropped every broadcast — symptom was "memory only updates after a page reload."

**Workspace dir routed via `--add-dir`.** The per-workflow workspace (`data/workspaces/<workflow_id>/`, injected into `ctx.raw` by `workflow.py:_get_workspace_dir`) is spliced into each task's `add_dir` list in `AICliService.run_batch` so the spawned claude can read files dropped by upstream nodes (`fileDownloader`, `documentParser`, code executors). Without this the workspace is invisible: memory-bound runs spawn with `cwd=repo_root` (stable for `--continue`'s `project_key`) and non-memory runs with `cwd=worktree`, neither of which sees the workspace files. Mirrors the ai_agent pattern (`services/ai.py:1899` — `config['workspace_dir'] = context.get('workspace_dir', '')`) but uses claude's native `--add-dir` instead of MCP-tool-config injection because claude has its own filesystem tools.

**Stale-model coercion.** `ClaudeCodeAgentParams.model` is a `Literal` of supported model IDs + aliases — strict validation would reject legacy saved values like `"claude-sonnet-4.6"` (dot-spelled), `"claude-3-5-sonnet-20241022"` (date-suffixed), `""`. A `field_validator("model", "fallback_model", mode="before")` coerces unknown values to the default (`"claude-sonnet-4-6"` for `model`, `None` for `fallback_model`) so old workflows keep loading; the UI dropdown still constrains new edits.

**Strict tool allowlist — no claude built-ins (except conditional `Skill`), gated by `--permission-mode dontAsk`.** The spawn argv emits `--permission-mode dontAsk` (documented as *"only pre-approved tools, no prompts"* per [code.claude.com/docs/en/permission-modes](https://code.claude.com/docs/en/permission-modes)) so `--allowedTools` is actually enforced. The previous default `bypassPermissions` *"skips the permission layer entirely"* — same doc — which turned the allowlist into documentation-only and let claude's built-in `Read` / `Edit` / `Bash` / `Glob` / `Grep` / `Write` / `Skill` / `WebSearch` / `WebFetch` fire regardless of wiring. The new `--allowedTools` list contains exactly: (a) one `mcp__machinaos__<node_type>` per node wired through the agent's `input-tools` handle, (b) the claude built-in `Skill` **conditionally** — only when at least one skill is wired through `input-skill` (paired with the materialisation helper below; never enabled when no skill is connected), (c) the five MachinaOs MCP infrastructure tools (`getWorkspaceFiles`, `listSkills`, `getSkill`, `getCredential`, `broadcastLog`). Every filesystem / shell / web operation flows through wired MCP tools (`fileRead` / `fileModify` / `fsSearch` / `shell` / `browser` / `perplexitySearch`) — explicit wiring, no built-in escape hatches. Defaults live in [`server/config/ai_cli_providers.json`](./server/config/ai_cli_providers.json) (`default_allowed_tools: ""`, `default_permission_mode: "dontAsk"`); per-task override via `ClaudeTaskSpec.allowed_tools` is honored verbatim (no auto-merge).

**Skill materialisation — per-workflow workspace, live-watched, diff-based.** [`services/cli_agent/_skills.py::materialise_skills`](./server/services/cli_agent/_skills.py) writes connected-AND-enabled SKILL.md trees under `<workspace_dir>/.claude/skills/<name>/` where `workspace_dir = data/workspaces/<workflow_id>/`. The workspace is passed via `--add-dir`, and claude scans `.claude/skills/` inside every `--add-dir` path per [the skills spec's "Automatic discovery from parent and nested directories" rule](https://code.claude.com/docs/en/skills#automatic-discovery-from-parent-and-nested-directories) — gives us per-workflow isolation (workflow A's skills can't bleed into workflow B's subprocess even when both spawn with `cwd=repo_root`). Pool tracks the materialised set in `PooledClaudeSession.materialised_skills: frozenset[str]`; on warm reuse, `acquire` calls `materialise_skills(workspace_dir, new_set, previous_skill_names=session.materialised_skills)` and the helper applies only the diff — `rmtree`'d skills disappear from claude's registry, new ones become invocable, all without respawning. Claude's [live filesystem watcher](https://code.claude.com/docs/en/skills#live-change-detection) picks up both edge events. Same helper drives both pool path and `AICliSession._pre_spawn` (non-pool) — uniform behaviour across transports. **Migration note**: pre-cutover the pool path wrote into `<repo_root>/.claude/skills/` so SKILL.md trees accumulated in the user's actual repo (gitignored but visible). Run `rm -rf .claude/skills/` once from the repo root to clean accumulated trees; future runs only write into per-workflow workspaces. We deliberately do NOT auto-prune because the repo's `.claude/` may contain user-authored skills outside the MachinaOs registry. **Architecture rationale**: surveyed alternatives — MCP `resources`/`prompts` require `@mention`/`/command` (not auto-loaded), hooks fire after skill discovery, `settings.json` doesn't expose per-session paths. Anthropic's own [Agent SDK Skills](https://code.claude.com/docs/en/agent-sdk/skills) uses the same filesystem-first pattern. Workspace-dir + live-watch is canonical.

**Warm-subprocess tool-surface rebind — no respawn cost.** Claude bakes the MCP bearer token into argv (`--mcp-config`) at spawn time and can't rotate without respawning. The pool stashes the spawn-time token on `PooledClaudeSession.batch_token` so subsequent batches can rebind the same persistent `BatchContext` in place. On warm reuse, `ClaudeSessionPool.acquire` calls [`rebind_batch(existing.batch_token, connected_tools=new_ctx.connected_tools, ...)`](./server/services/cli_agent/mcp_server.py) — diffs old vs new tool lists, calls `unexpose_workflow_tools(removed)` to decrement FastMCP refcounts (so a disconnected `duckduckgoSearch` falls off the tool list when its refcount hits zero), `expose_workflow_tools(added)` for newly-wired ones, then unregisters the redundant new token. `_terminate_locked` calls `unregister_batch(session.batch_token)` so refcounts drain on pool eviction / `clear` / `shutdown_all` (closes the long-standing leak where every pooled run permanently incremented every wired tool's refcount). The per-handler scope check in `workflow_tools._build_handler` reads `ctx.connected_tools` at call time, so even if claude's frozen `--allowedTools` argv still references a now-disconnected tool, the handler returns `{"error": "...not connected to this batch", "status": 403}` — double-lock against tool leakage across warm-reuse turns.

**Logs to grep on a live run:**
- `[Claude Code memory] memory_node=<id> -> --continue (claude auto-finds latest session under cwd)`
- `[ClaudeSessionPool] spawned new session memory_node=<id> pid=<N>` (cold spawn) or `warm reuse memory_node=<id> pid=<N> uuid=<UUID>` (intra-process turn)
- `[CC-Agent _persist_memory] saved memory_node=<id> last_session_id=<UUID> appended_turns=1 ... content_length=<N>`
- On tool-surface change between batches: `[CC-Agent MCP rebind_batch] node=<id> wf=<id> token=<8hex>... +<added> -<removed> kept=<n> (now <total> tools)` — confirms the in-place rebind ran instead of a respawn.
- On crash recovery: `[ClaudeSessionPool] dropping dead session memory_node=<id> ... — will respawn` followed by spawn with `--resume <UUID>` argv.

## Memory Compaction, Token Tracking, and Cost Calculation

### Overview
The compaction service enables automatic memory compaction, token tracking, and **cost calculation** for all LLM providers. Cost is calculated using official pricing (per 1M tokens) and stored alongside token metrics. The Credentials Modal displays per-provider usage and costs.

### Architecture
```
AI Agent Execution
       ↓
CompactionService.track() → PricingService.calculate_cost()
       ↓                   → Save token metrics + cost to DB
       ↓                   → Update cumulative state + cost
       ↓                   → Check if threshold exceeded
       ↓
If needs_compaction:
  - Anthropic: Native compaction via context_management API
  - OpenAI: Native compaction via compact_threshold
  - Others: Client-side summarization fallback
       ↓
CompactionService.record() → Save compaction event to DB
                           → Reset cumulative token count
```

### Native Provider APIs

**Threshold strategy:** per-session `custom_threshold` > model-aware threshold (50% of context window) > global default.

**Anthropic SDK (tool_runner):**
```python
# Model-aware: threshold computed from model's context window
compaction_control = svc.anthropic_config(model="claude-opus-4.6", provider="anthropic")
# Returns: {"enabled": True, "context_token_threshold": 500000}  (50% of 1M)
```

**Anthropic Messages API:**
```python
api_config = svc.anthropic_api_config(model="claude-sonnet-4.5", provider="anthropic")
# Returns: {"betas": ["compact-2026-01-12"], "context_management": {"edits": [...]}}
# Threshold auto-computed from model's context window
```

**OpenAI:**
```python
openai_config = svc.openai_config(model="gpt-5.2", provider="openai")
# Returns: {"context_management": {"compact_threshold": 200000}}  (50% of 400K)
```

### Key Files
| File | Description |
|------|-------------|
| `server/services/compaction.py` | CompactionService with model-aware thresholds, track(), record(), stats(), configure(), compact_context() methods |
| `server/services/model_registry.py` | ModelRegistryService providing context_length for model-aware threshold computation |
| `server/services/pricing.py` | PricingService with official pricing registry for all 6 providers |
| `server/services/ai.py` | `_track_token_usage()` with automatic compaction triggering |
| `server/models/database.py` | TokenUsageMetric, CompactionEvent, SessionTokenState tables (with cost fields) |
| `server/core/database.py` | CRUD methods for token metrics, compaction events, and `get_provider_usage_summary()` |
| `server/core/config.py` | compaction_enabled, compaction_threshold settings |
| `server/core/container.py` | Dependency injection for compaction_service |
| `server/main.py` | Wires AI service to compaction service at startup |
| `server/routers/websocket.py` | get_compaction_stats, configure_compaction, get_provider_usage_summary handlers |
| `client/src/hooks/useApiKeys.ts` | `getProviderUsageSummary()` hook method |
| `client/src/components/CredentialsModal.tsx` | Usage & Costs collapsible section per provider |

### Database Models

**TokenUsageMetric** - Per-execution token usage and cost:
```python
class TokenUsageMetric(SQLModel, table=True):
    session_id: str          # Memory session identifier
    node_id: str             # Agent node ID
    provider: str            # openai, anthropic, gemini, groq, cerebras, openrouter
    model: str               # Model name
    input_tokens: int        # Input token count
    output_tokens: int       # Output token count
    total_tokens: int        # Total tokens
    cache_creation_tokens: int  # Anthropic cache creation
    cache_read_tokens: int      # Anthropic cache read
    reasoning_tokens: int       # OpenAI o-series reasoning
    # Cost fields (USD)
    input_cost: float        # Cost for input tokens
    output_cost: float       # Cost for output tokens
    cache_cost: float        # Cost for cache tokens
    total_cost: float        # Total cost
```

**SessionTokenState** - Cumulative state per session:
```python
class SessionTokenState(SQLModel, table=True):
    session_id: str              # Unique session identifier
    cumulative_total: int        # Running total tokens
    custom_threshold: int        # Per-session threshold override
    compaction_count: int        # Number of compactions
    last_compaction_at: datetime # Last compaction timestamp
    # Cumulative cost fields (USD)
    cumulative_input_cost: float
    cumulative_output_cost: float
    cumulative_total_cost: float
```

**CompactionEvent** - Compaction history:
```python
class CompactionEvent(SQLModel, table=True):
    session_id: str
    trigger_reason: str      # "native" or "threshold"
    tokens_before: int
    tokens_after: int
    summary_content: str     # Compacted summary (if available)
```

### CompactionService API

```python
from services.compaction import get_compaction_service

svc = get_compaction_service()

# Get model-aware provider config (threshold = 50% of context window)
anthropic_cfg = svc.anthropic_config(model="claude-opus-4.6", provider="anthropic")
# threshold: 500000 (50% of 1M context)
openai_cfg = svc.openai_config(model="gpt-5.2", provider="openai")
# threshold: 200000 (50% of 400K context)

# Or override with explicit threshold
anthropic_cfg = svc.anthropic_config(threshold=100000)

# Track token usage after AI execution (threshold auto-computed from model)
result = await svc.track(
    session_id="user-123",
    node_id="agent-1",
    provider="anthropic",
    model="claude-opus-4.6",
    usage={"input_tokens": 5000, "output_tokens": 1000, "total_tokens": 6000}
)
# result: {"total": 6000, "total_cost": 0.021, "threshold": 500000, "needs_compaction": False}

# Record compaction event after native API handles it
await svc.record(
    session_id="user-123",
    node_id="agent-1",
    provider="anthropic",
    model="claude-opus-4.6",
    tokens_before=505000,
    tokens_after=15000,
    summary="Compacted conversation summary..."
)

# Get session statistics (model-aware threshold when model/provider given)
stats = await svc.stats("user-123", model="claude-opus-4.6", provider="anthropic")
# {"session_id": "user-123", "total": 15000, "threshold": 500000, "count": 1}

# Configure per-session custom threshold (overrides model-aware threshold)
await svc.configure("user-123", threshold=50000, enabled=True)
```

### WebSocket Handlers

| Handler | Description |
|---------|-------------|
| `get_compaction_stats` | Get token statistics for a session |
| `configure_compaction` | Update threshold/enabled settings for a session |
| `get_provider_usage_summary` | Get aggregated usage and cost by provider for Credentials Modal |

### PricingService

Calculates cost based on official pricing (USD per 1M tokens):

```python
from services.pricing import get_pricing_service

pricing = get_pricing_service()

# Calculate cost for token usage
cost = pricing.calculate_cost(
    provider="anthropic",
    model="claude-3-5-sonnet",
    input_tokens=5000,
    output_tokens=1000,
    cache_read_tokens=500
)
# cost: {"input_cost": 0.015, "output_cost": 0.015, "cache_cost": 0.00015, "total_cost": 0.03015}

# Get pricing for a model (supports partial matching)
pricing_info = pricing.get_pricing("anthropic", "claude-3-5-sonnet-20241022")
# pricing_info: ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0, cache_read_per_mtok=0.30)
```

**Supported Providers & Pricing** (February 2026):
| Provider | Example Models | Input $/MTok | Output $/MTok |
|----------|---------------|--------------|---------------|
| OpenAI | gpt-5, gpt-4o, o3 | 1.25-15.00 | 10.00-60.00 |
| Anthropic | claude-opus-4.6, claude-sonnet-4 | 3.00-5.00 | 15.00-25.00 |
| Gemini | gemini-2.5-pro, gemini-2.0-flash | 0.10-1.25 | 0.40-10.00 |
| Groq | llama-4-scout, qwen3-32b | 0.05-0.59 | 0.08-0.79 |
| Cerebras | llama-3.1-70b | 0.10-0.60 | 0.10-0.60 |
| OpenRouter | Pass-through | Varies | Varies |

### Configuration

The compaction threshold is fully JSON-driven via `server/config/llm_defaults.json`:

```json
"agent": {
  "recursion_limit": 500,
  "default_temperature": 0.7,
  "compaction": { "ratio": 0.5 }
}
```

Effective threshold = `providers.<provider>.context_length.<model>` × `agent.compaction.ratio` (e.g. claude-sonnet-4-6 → 200K × 0.5 = 100K input tokens). When model/provider are unknown, `model_registry.get_context_length` falls through to the provider's `_default` entry in the same JSON — never a Python constant.

Only the global on/off toggle stays in `.env`:
```bash
COMPACTION_ENABLED=true       # Enable/disable compaction globally
```

**Threshold priority:** per-session `custom_threshold` > model-aware (`context_length × ratio`).

### Client-Side Compaction

When the token threshold is exceeded, the service automatically triggers compaction using the AI service to generate a structured summary:

```python
# Automatic compaction in _track_token_usage()
if tracking.get('needs_compaction') and memory_content and api_key:
    result = await svc.compact_context(
        session_id=session_id,
        node_id=node_id,
        memory_content=memory_content,  # Current conversation markdown
        provider=provider,
        api_key=api_key,
        model=model
    )
    # result: {"success": True, "summary": "# Conversation Summary (Compacted)...", "tokens_before": 105000, "tokens_after": 0}
```

**Summary Structure** (Claude Code pattern):
```markdown
# Conversation Summary (Compacted)
*Generated: 2025-02-12T10:30:00Z*

## Task Overview
What the user is trying to accomplish.

## Current State
What's been completed and what's in progress.

## Important Discoveries
Key findings, decisions, or problems encountered.

## Next Steps
What needs to happen next.

## Context to Preserve
Details that must be retained for continuity.
```

### WebSocket Broadcasts

Real-time updates broadcast to frontend:

| Event | Description |
|-------|-------------|
| `token_usage_update` | After each AI execution: `{session_id, data: {total, threshold, needs_compaction}}` |
| `compaction_starting` | Before compaction begins: `{session_id, node_id}` |
| `compaction_completed` | After compaction: `{session_id, success, tokens_before, tokens_after, error}` |

### Token Usage UI

The MiddleSection displays a Token Usage panel for memory nodes with:
- **Progress bar**: Visual tokens used vs threshold
- **Statistics**: Current token count, threshold, compaction count
- **Editable threshold**: Click edit icon to change per-session threshold

```typescript
// In client/src/components/parameterPanel/MiddleSection.tsx
<Collapse.Panel header="Token Usage" key="tokenUsage">
  <Progress percent={Math.round((total / threshold) * 100)} />
  <Statistic title="Tokens Used" value={`${total} / ${threshold}`} />
  <Statistic title="Compactions" value={count} />
  <InputNumber onChange={updateThreshold} />  {/* Edit threshold */}
</Collapse.Panel>
```

### Service Wiring

The compaction service requires AI service for summarization, wired at startup:

```python
# In server/main.py
from services.compaction import get_compaction_service
compaction_svc = container.compaction_service()
compaction_svc.set_ai_service(container.ai_service())
```

### Design Decisions
- **Hybrid Approach**: Native APIs for Anthropic/OpenAI configs, client-side summarization for actual compaction
- **5-Section Summary**: Follows Claude Code's structured summary format for continuity
- **Automatic Triggering**: Compaction triggered in `_track_token_usage()` when threshold exceeded
- **Per-Session State**: Each memory session has independent token tracking and thresholds
- **Model-Aware Threshold**: 50% of model's context window (e.g., 500K for Claude Opus 4.6 with 1M context, 200K for GPT-5.2 with 400K context). Falls back to global `COMPACTION_THRESHOLD` when model info unavailable. Per-session `custom_threshold` always takes priority.
- **Singleton Pattern**: Service accessible via `get_compaction_service()`

## API Cost Tracking

Centralized cost tracking for third-party API services (Twitter/X, Google Maps). See [Pricing Service](./docs-internal/pricing_service.md) for full documentation.

### Two Tracking Methods

**1. Manual Tracking** - For services using native SDKs:
```python
# server/services/handlers/twitter.py
await _track_twitter_usage(node_id, 'tweet', 1, workflow_id, session_id)

# server/services/maps.py
await _track_maps_usage(node_id, 'geocode', 1, workflow_id, session_id)
```

**2. Automatic HTTPX Tracking** - For services using httpx client:
```python
from services.tracked_http import get_tracked_client, set_tracking_context

set_tracking_context(node_id="twitter-1", session_id="user-123")
client = get_tracked_client()
response = await client.post("https://api.twitter.com/2/tweets", json={...})
# Automatically tracked via HTTPX response event hook!
```

### Pricing Configuration

All pricing in `server/config/pricing.json` (user-editable):
- `llm`: Per-model token pricing (USD/MTok)
- `api`: Per-service operation pricing (USD/request)
- `operation_map`: Maps handler actions to pricing operations
- `url_patterns`: Regex patterns for automatic HTTPX tracking

### Database Storage

`APIUsageMetric` table stores: service, operation, endpoint, resource_count, cost (USD)

### Frontend Display

`CredentialsModal.renderApiUsagePanel()` shows per-service usage and costs.

## AI Agent Tool System

### Overview
Tool nodes provide capabilities that AI Agents can invoke during reasoning. Each tool node connects to the AI Agent's `input-tools` handle and defines a schema for the LLM to understand how to call it.

### Architecture
```
Tool Node (calculatorTool) → (tool output) → AI Agent (input-tools handle)
                                                    ↓
                                            _run_agent_loop builds tools from schemas
                                                    ↓
                                            LLM decides when to call tools
                                                    ↓
                                            Tool executor runs handler
                                                    ↓
                                            Result returned to LLM
```

### Tool Execution Flow
1. **Tool Discovery**: AI Agent scans edges for nodes connected to `input-tools` handle
2. **Schema Building**: `_get_tool_schema()` in `ai.py` creates Pydantic schema for each tool type
3. **Tool Binding**: `_run_agent_loop` binds tools via `chat_model.bind_tools(tools)`
4. **LLM Decision**: LLM decides when to call tools based on user query
5. **Status Broadcast**: `executing_tool` status broadcast with tool_name for UI animation
6. **Tool Execution**: `execute_tool()` in `tools.py` dispatches to appropriate handler
7. **Result Return**: Tool result returned to LLM for continued reasoning

### Key Files
| File | Description |
|------|-------------|
| `server/services/handlers/tools.py` | Tool execution handlers |
| `server/services/ai.py` | `_get_tool_schema()` - Pydantic schemas for tools |
| `server/services/handlers/ai.py` | Tool discovery from edges |

### Adding a new tool or specialized agent (Wave 11)

**Single source of truth: [`server/nodes/README.md`](./server/nodes/README.md)** (5-minute recipe) and [`docs-internal/plugin_system.md`](./docs-internal/plugin_system.md) (full reference). The pre-Wave-11 `toolNodes.ts` / `specializedAgentNodes.ts` / `AGENT_CONFIGS` files do not exist — the canonical authoring shape is one Python file.

The whole workflow:

```python
# server/nodes/tool/<plugin>/__init__.py     ← for a tool
# server/nodes/agent/<plugin>/__init__.py    ← for a specialized agent
class MyTool(ToolNode):              # or SpecializedAgentBase for an agent
    type = "myTool"
    display_name = "My Tool"
    group = ("tool", "ai")
    component_kind = "tool"          # or "agent"
    Params = MyParams                # Pydantic — feeds UI + AI tool schema
    Output = MyOutput
    @Operation("run")
    async def run(self, ctx, params): ...
```

`BaseNode.__init_subclass__` registers the class into `_NODE_CLASS_REGISTRY` (first), then into `NODE_METADATA`, `_DIRECT_MODELS`, `NODE_OUTPUT_SCHEMAS`, and `_HANDLER_REGISTRY` on import. The class-registry-first order matters: `_metadata_dict` calls `get_plugin_icon_path(cls.type)` which goes through `get_node_class()`. NodeSpec emits at `GET /api/schemas/nodes/<type>/spec.json`. The frontend auto-discovers via `useNodeSpec` + `componentKind` dispatch (see "Spec-driven component design" above). Icon goes in `<plugin>/icon.svg` (or `visuals.json` for emoji / library brand); color goes in `<plugin>/meta.json` (or `visuals.json` legacy fallback).

**Cross-cutting edits that are still required (small):**
- New specialized agent: add the agent's `type` string to the delegation-check tuple in [`server/services/handlers/tools.py:execute_tool()`](./server/services/handlers/tools.py) (~line 224) so the parent agent's `delegate_to_*` tool finds it. Also update `AI_AGENT_TYPES` frozenset in [`server/constants.py`](./server/constants.py) — used for legacy delegation checks. Both are flagged as tech debt; see "remaining tribal arrays" note.
- Brand-new uiHint flag: add to `INodeUIHints` in [`client/src/types/INodeProperties.ts`](./client/src/types/INodeProperties.ts) AND to the `known` set in `test_ui_hints_only_carry_known_flags` (`server/tests/test_node_spec.py`).

**No edits needed:** any TypeScript node definition file, `_get_tool_schema()`, `AGENT_CONFIGS`, `AGENT_WITH_SKILLS_TYPES`, `aiAgentTypes`, `Dashboard.tsx`, `MiddleSection.tsx`, `InputSection.tsx`, or any of the other arrays the pre-Wave-11 docs listed.

### Tool Execution Animation
Tool nodes display execution status via the standard node status system:
- Backend broadcasts `executing` status to tool node when AI Agent calls it
- `SquareNode.tsx` uses `getNodeStatus()` from WebSocket context
- Tool nodes show cyan border and pulse animation when `isExecuting` is true
- **Minimum glow duration**: 500ms ensures fast-executing tools are visible (via `isGlowing` state)
- Dual-purpose tools (Python/JavaScript) fall back to node params when LLM returns empty args

### Implemented Tools
| Tool | Schema | Handler | Description |
|------|--------|---------|-------------|
| calculatorTool | CalculatorSchema | `_execute_calculator()` | Math operations |
| currentTimeTool | CurrentTimeSchema | `_execute_current_time()` | Date/time with timezone |
| duckduckgoSearch | DuckDuckGoSearchSchema | `_execute_duckduckgo_search()` | DuckDuckGo web search (free) |
| taskManager | TaskManagerSchema | `_execute_task_manager()` | Task creation and management |
| writeTodos | WriteTodosSchema | `execute_write_todos()` / `handle_write_todos()` | Structured task list planning with checklist rendering |
| braveSearch | BraveSearchSchema | `handle_brave_search()` | Brave Search API web results |
| serperSearch | SerperSearchSchema | `handle_serper_search()` | Google SERP via Serper API |
| perplexitySearch | PerplexitySearchSchema | `handle_perplexity_search()` | AI-powered search with citations |
| androidTool | AndroidToolSchema | `_execute_android_toolkit()` | Android device control via connected services |
| Android service nodes | Per-service schema | `_execute_android_service()` | Direct Android service tools (see below) |

### Direct Android Service Tools
Android service nodes (batteryMonitor, wifiAutomation, etc.) can be connected directly to any agent's `input-tools` handle without using the androidTool aggregator. The `execute_tool()` function detects these via `ANDROID_SERVICE_NODE_TYPES` and routes to `_execute_android_service()`.

**Service ID Mapping** (camelCase node type -> snake_case service ID):
```python
service_id_map = {
    'batteryMonitor': 'battery',
    'networkMonitor': 'network',
    'systemInfo': 'system_info',
    'location': 'location',
    'appLauncher': 'app_launcher',
    'appList': 'app_list',
    'wifiAutomation': 'wifi_automation',
    'bluetoothAutomation': 'bluetooth_automation',
    'audioAutomation': 'audio_automation',
    'deviceStateAutomation': 'device_state',
    'screenControlAutomation': 'screen_control',
    'airplaneModeControl': 'airplane_mode',
    'motionDetection': 'motion_detection',
    'environmentalSensors': 'environmental_sensors',
    'cameraControl': 'camera_control',
    'mediaControl': 'media_control',
}
```

### Android Toolkit Pattern
The androidTool follows n8n Sub-Node and LangChain Toolkit patterns:
- **Gateway Pattern**: Single tool node aggregates multiple Android service nodes
- **Dynamic Schema**: Schema built at runtime from connected services only
- **Service Routing**: Tool execution routes to appropriate connected Android node

```
[Battery Monitor] --+
                    +--> [Android Toolkit] --> [AI Agent]
[WiFi Automation] --+
```

The AI sees a single `android_device` tool with schema showing only connected services:
- `service_id`: Which service to use (e.g., "battery", "wifi_automation")
- `action`: Action to perform (e.g., "status", "enable", "disable")
- `parameters`: Action-specific parameters

### Tool Schema Editor
The Android Toolkit node includes a schema editor UI for customizing the LLM-visible schema of connected services.

#### Architecture
```
ToolSchemaEditor Component
        ↓
  useToolSchema Hook (WebSocket CRUD)
        ↓
  Database (tool_schemas table)
        ↓
  AI Service reads schemas at execution
```

#### Key Files
| File | Description |
|------|-------------|
| `client/src/components/parameterPanel/ToolSchemaEditor.tsx` | Schema editor UI component |
| `client/src/hooks/useToolSchema.ts` | WebSocket hook for schema CRUD operations |
| `server/models/database.py` | `ToolSchema` SQLModel table definition |
| `server/core/database.py` | Database CRUD methods for tool schemas |
| `server/routers/websocket.py` | WebSocket handlers for schema operations |

#### Database Model
```python
class ToolSchema(SQLModel, table=True):
    __tablename__ = "tool_schemas"
    node_id: str          # Service node ID (unique key)
    tool_name: str        # Display name (e.g., "Battery Monitor")
    tool_description: str # Description shown to LLM
    schema_config: Dict   # Schema fields and types (JSON)
    connected_services: Optional[Dict]  # For toolkit aggregation
```

#### UI Features
- **Service Selector**: Dropdown showing only Android service nodes connected to the toolkit
- **Schema Fields Editor**: Add/remove/edit fields with name, type, description, required flag
- **Per-Service Schema**: Each connected service has its own independent schema stored by service node ID
- **Save/Reset**: Changes tracked locally, saved to database on demand

#### WebSocket Messages
| Message Type | Description |
|--------------|-------------|
| `get_tool_schema` | Get schema for a node by ID |
| `save_tool_schema` | Save/update schema for a node |
| `delete_tool_schema` | Delete schema for a node |
| `get_all_tool_schemas` | Get all stored schemas |

#### Default Schema Generation
When no custom schema exists, service-specific defaults are generated:
```typescript
{
  description: `Control ${serviceName} on Android device`,
  fields: {
    action: { type: 'string', description: `Action to perform on ${serviceName}`, required: true },
    parameters: { type: 'object', description: `Parameters for the ${serviceName} action`, required: false }
  }
}
```

### Web Search Implementation

#### DuckDuckGo (duckduckgoSearch - free, no API key)
Uses `ddgs` library for web results:
```python
from ddgs import DDGS
def do_search():
    ddgs = DDGS()
    return list(ddgs.text(query, max_results=max_results))
search_results = await asyncio.get_event_loop().run_in_executor(None, do_search)
```

#### Search API Nodes (braveSearch, serperSearch, perplexitySearch)
Dedicated handlers in `server/services/handlers/search.py` using `httpx.AsyncClient`:
- **Brave Search**: `GET https://api.search.brave.com/res/v1/web/search` with `X-Subscription-Token` header. Returns `{query, results: [{title, snippet, url}], result_count, provider}`.
- **Serper**: `POST https://google.serper.dev/search` with `X-API-KEY` header. Supports web/news/images/places search types. Returns `{query, results, result_count, search_type, provider}` with optional `knowledge_graph`.
- **Perplexity Sonar**: `POST https://api.perplexity.ai/chat/completions` with Bearer token. Returns `{query, answer (markdown), citations: [url], results: [{url}], model, provider}` with optional `images` and `related_questions`.

All handlers fetch API keys via `auth_service.get_api_key()` and track usage via `_track_search_usage()` for cost calculation.

## Config Node Architecture

### Overview
Config nodes (memory, tools, models) connect to parent nodes via special "config handles" (e.g., `input-memory`, `input-tools`). These are auxiliary connections for configuration, not main data flow. The UI intelligently handles visibility of connected inputs based on this architecture.

### Config Handle Convention
Config handles follow the pattern `input-<type>` where type is NOT 'main':
- `input-memory` - Memory/context nodes
- `input-tools` - Tool nodes
- `input-model` - Model configuration nodes
- `input-skill` - Skill nodes
- `input-task` - Task completion trigger nodes
- `input-teammates` - Team member agent nodes
- `input-main` - Main data flow (NOT a config handle)

**Note**: Trigger nodes (e.g., `taskTrigger`) connecting via config handles are excluded from downstream inclusion in `_get_downstream_nodes()` to prevent them from blocking as event waiters.

### Config Node Detection
Nodes are identified as config nodes by their `group` array in the node definition:
```typescript
// Config node example (simpleMemory)
group: ['skill', 'memory']  // 'memory' or 'tool' indicates config node
```

### Input Inheritance
Config nodes automatically inherit their parent node's main inputs in the parameter panel:
```
WhatsApp Trigger → AI Agent ← Simple Memory
       ↓              ↑
   main input    config handle

When viewing Simple Memory's parameters:
- Shows: "WhatsApp Trigger (via AI Agent)"
- Can drag WhatsApp outputs into Memory's parameters
```

### Filtering Logic
Located in `InputSection.tsx` and `OutputPanel.tsx`:
1. **Parent nodes** (AI Agent): Skip showing config node connections as inputs
2. **Config nodes** (Memory): Inherit parent's main input connections with "(via Parent)" label

### Key Functions
```typescript
// Check if handle is for config nodes (not main data flow)
const isConfigHandle = (handle: string | null | undefined): boolean => {
  if (!handle) return false;
  return handle.startsWith('input-') && handle !== 'input-main';
};

// Check if node is a config/auxiliary node — reads the backend-derived
// uiHint, not a frontend group-string heuristic.
const isConfigNode = (nodeType: string | undefined): boolean => {
  if (!nodeType) return false;
  const definition = resolveNodeDescription(nodeType);
  return definition?.uiHints?.isConfigNode === true;
};
```

The `isConfigNode` flag is **auto-derived on the backend** by `_derive_auto_ui_hints` in [`server/services/plugin/base.py`](./server/services/plugin/base.py): plugins whose `group` tuple contains `memory` or `tool` (the centralized `_CONFIG_NODE_GROUPS = frozenset({"memory", "tool"})`) automatically export `uiHints.isConfigNode: True`. Explicit `cls.ui_hints` always wins (merge order: auto-derived first, then `dict.update` with the plugin's declaration). Pytest invariant `test_ui_hints_only_carry_known_flags` locks the flag name in `server/tests/test_node_spec.py`.

### Adding New Config Node Types
1. Put the plugin in `('memory',)` or `('tool',)` (or any tuple containing one of those). The backend auto-derivation does the rest — do NOT declare `isConfigNode` in `ui_hints` unless you want to override.
2. Use `input-<type>` naming for the target handle on the parent node.
3. Input inheritance and filtering work automatically — the frontend reads `definition.uiHints.isConfigNode`, never the group strings.

### Toolkit Sub-Node Execution Pattern

Toolkit nodes (like `androidTool`) aggregate sub-nodes that should only execute when called via the toolkit's tool interface, not as independent workflow nodes.

**Problem**: In parallel execution mode, Kahn's algorithm schedules nodes with in-degree 0 first. Sub-nodes connect TO the toolkit (not from it), so they have in-degree 0 and would be incorrectly scheduled in layer 0.

**Solution**: The executor detects and excludes toolkit sub-nodes from execution layers.

**Key Constants** (`server/constants.py`):
```python
# Toolkit node types that aggregate sub-nodes
TOOLKIT_NODE_TYPES: FrozenSet[str] = frozenset([
    'androidTool',  # Aggregates Android service nodes
])

# Config nodes excluded from execution (includes tool nodes)
CONFIG_NODE_TYPES: FrozenSet[str] = (
    AI_MEMORY_TYPES | AI_TOOL_TYPES | AI_CHAT_MODEL_TYPES
)
```

**Detection Logic** (in `ExecutionContext.create()` and `_compute_execution_layers()`):
```python
# Find toolkit sub-nodes (nodes that connect TO a toolkit)
toolkit_node_ids = {n.get("id") for n in nodes if n.get("type") in TOOLKIT_NODE_TYPES}
subnode_ids: set = set()
for edge in edges:
    source = edge.get("source")
    target = edge.get("target")
    # Any node that connects TO a toolkit is a sub-node
    if target in toolkit_node_ids and source:
        subnode_ids.add(source)
```

**Example Workflow**:
```
[WhatsApp Trigger] → [AI Agent] ← [Android Toolkit] ← [Battery Monitor]
                                                    ← [Location]
```
- `Battery Monitor` and `Location` connect TO `Android Toolkit`
- They are detected as sub-nodes and excluded from execution layers
- They only execute when AI Agent calls the toolkit's tool interface

## Android Services Development Guide

### Architecture
Android services use a factory pattern with `createAndroidServiceNode()` for consistent node structure:
- **SquareNode Component**: Visual representation with configuration status indicators
- **Dynamic Actions**: Load available actions from backend via `loadOptionsMethod`
- **ADB Integration**: All services communicate with Android devices via ADB commands
- **Parameter System**: Flexible JSON parameters for service-specific configuration

### Adding New Android Services

**Wave 11+**: Android service nodes are authored as backend plugins
under `server/nodes/android/<service>.py`. Each plugin subclasses the
shared `AndroidServiceBase` (see `server/nodes/android/_base.py`), which
handles ADB dispatch via `SERVICE_ID_MAP`. See the
[Android Services Development Guide](./docs-internal/plugin_system.md#android).

Adding a new Android service:

1. **Create the plugin** at `server/nodes/android/<service_name>.py`
   subclassing `AndroidServiceBase` — the base handles `service_id`
   routing, argument translation, and broadcast status updates.
2. **Register the service id** in `SERVICE_ID_MAP` on
   `server/nodes/android/_base.py` (camelCase node type → snake_case
   service id).
3. **Implement the execution path** in the plugin's `execute` method;
   shared ADB infrastructure lives in `AndroidService`.

### Key Files
- **Shared base**: `server/nodes/android/_base.py` — `AndroidServiceBase`, `SERVICE_ID_MAP`, `execute_android_toolkit`, `execute_android_service_tool`
- **Backend Router**: `server/routers/android.py` - API endpoints for Android operations
- **Workflow Handler**: `server/services/workflow.py` - Execution logic for all nodes
- **Execution Service**: `src/services/executionService.ts` - Routes Android nodes to Python backend

### Requirements
- **Device Connection**: Configure Android connection via Credentials Modal (Android panel)
- **Permissions**: Android app must have necessary permissions for services

### Android Device Connection
Android device connection is configured via the **Credentials Modal** (Android panel), not via workflow nodes.

**Connection Types:**
1. **Remote Relay** (recommended): Connect to Android device via relay server (QR code pairing)
2. **Local ADB**: Connect via USB with ADB port forwarding

**WebSocket Handlers** (`server/routers/websocket.py`):
- `android_relay_connect` - Connect to relay server, get QR code for pairing
- `android_relay_disconnect` - Disconnect from relay server
- `android_relay_reconnect` - Reconnect to relay server

### Android Relay Client
Located in `server/services/android/`:

**Key Components:**
- `client.py` - RelayWebSocketClient manages persistent connection
- `broadcaster.py` - Status broadcast functions (connected, paired, disconnected)
- `manager.py` - Global client instance management
- `protocol.py` - JSON-RPC 2.0 message handling

**Message Filtering:**
```python
async def receive_message(self, timeout: float = 10.0):
    """Receive response message, skipping non-response types"""
    skip_types = {'presence', 'pong', 'ping', 'connected'}

    while True:
        data = await asyncio.wait_for(self._message_queue.get(), timeout)
        msg_type = data.get('type', '')

        if msg_type in skip_types:
            continue  # Skip and wait for next message

        return data  # Return actual response
```

**Performance Benefits:**
- Initial connection: ~0.18s (WebSocket handshake + registration)
- Reused connection: ~0.0003s (600x faster)
- Background tasks maintain connection health
- Message queue decouples receiving from service execution

### Android Relay Connection vs Device Pairing

The Android relay system uses a **two-state model** for connection status:

| State | Description | Frontend Indicator |
|-------|-------------|-------------------|
| `connected` | WebSocket connection to relay server is active | N/A (not shown directly) |
| `paired` | Android device has scanned QR and is paired via relay | Green/Red status dot |

**Key Concepts:**
- **Relay Connection**: The WebSocket connection to `wss://relay.zeenie.xyz/ws` - can be active without a device
- **Device Pairing**: An Android device scans the QR code and pairs - required for service execution
- **Android service nodes require pairing**, not just relay connection, to execute

**Status Broadcasting Architecture:**
```
server/services/android/
├── client.py        # RelayWebSocketClient - manages WebSocket connection
├── broadcaster.py   # Status broadcast functions
├── manager.py       # Global client instance management
└── protocol.py      # JSON-RPC 2.0 message handling
```

**Broadcast Functions** (`server/services/android/broadcaster.py`):
```python
# Device connected and paired
await broadcast_connected(device_id, device_name)

# Device disconnected but relay still connected (for re-pairing)
await broadcast_device_disconnected(
    relay_connected=True,
    qr_data=qr_data,
    session_token=session_token
)

# Relay connection fully closed
await broadcast_relay_disconnected()

# QR code available for pairing
await broadcast_qr_code(qr_data, session_token)
```

**Frontend Status Indicator** (`client/src/components/SquareNode.tsx`):
```typescript
// Android nodes use 'paired' status, not 'connected'
const isAndroidConnected = isAndroidNode && androidStatus.paired;
```

**Status Flow:**
1. User clicks "Connect" → Relay WebSocket connects → `connected=true, paired=false`
2. QR code displayed → User scans with Android app → `connected=true, paired=true`
3. Android app disconnects → `connected=true, paired=false` (can re-pair)
4. Relay WebSocket closes → `connected=false, paired=false`

**WebSocket Context Interface** (`client/src/contexts/WebSocketContext.tsx`):
```typescript
export interface AndroidStatus {
  connected: boolean;      // Relay WebSocket connected
  paired: boolean;         // Android device paired
  device_id: string | null;
  device_name: string | null;
  connected_devices: string[];
  connection_type: string | null;
  qr_data: string | null;
  session_token: string | null;
}
```

## WhatsApp Integration

### Overview
WhatsApp nodes use square design with integrated QR code viewing and proper error handling. The integration proxies all requests through the Python backend to the WhatsApp RPC service (default port 9400, configurable via `WHATSAPP_RPC_PORT` env var or `--port` CLI flag). Supports individual chats, groups, and newsletter channels (sending, querying, follow/unfollow, create, mute, mark viewed, react, live updates, media download, profile pics). All 14 WhatsApp events handled.

### Architecture
```
Frontend (WhatsAppNode.tsx) → Python Backend (/api/whatsapp/*) → WhatsApp RPC Service (localhost:${WHATSAPP_RPC_PORT:-9400})
```

### Key Features
- **Square Node Design**: 80x80px square nodes with status indicators
- **QR Code Viewer**: Embedded QR code display via Python backend proxy
- **Error Handling**: Robust error handling with proper HTTP status codes (503, 504, 410)
- **No Mock Data**: All endpoints return proper errors instead of mock responses
- **Connection Status**: Real-time status display with device ID, session, and service info

### Backend Helpers (`server/services/whatsapp_service.py`)

Wave 11: renamed from `routers/whatsapp.py` (was misnamed — never an APIRouter). Provides RPC proxy helpers consumed by `nodes/whatsapp/*` plugins and the WhatsApp WebSocket handlers.

#### `/api/whatsapp/status` - Get Connection Status
- Returns WhatsApp connection status from Flask service
- Handles ConnectError, TimeoutException with 503/504 status codes
- Safe JSON parsing with error handling

#### `/api/whatsapp/qr` - Get QR Code
- Checks connection status first
- Returns QR code data if not connected
- Returns "Already connected" message if connected
- Handles errors gracefully without crashing

#### `/api/whatsapp/start` - Start Connection
- Proxies start request to Flask service
- Safe JSON parsing and error handling
- Returns proper HTTP errors on failure

#### `/api/whatsapp/send` - Send Message
- Enhanced messaging endpoint
- Comprehensive error handling with specific exception catches
- Never crashes on service unavailability

### Frontend Component (`client/src/components/WhatsAppNode.tsx`)
- **Node Type**: Square (80x80px, borderRadius: 8px)
- **Status Indicators**: Top-right corner indicator (green/yellow/red)
- **Connect Button**: Bottom-left corner for opening modal
- **QR Code Display**: Fetches QR via `fetchQRCode()` from Python backend
- **Connection Details**: Shows device ID, status, session, service, pairing, timestamp
- **Action Buttons**: Start, Restart, Refresh Status, Close (always visible)

### Critical Bug Fixes

#### 1. Missing Dependency Injection Wiring
**Problem**: `main.py` was missing `"routers.whatsapp"` in `container.wire()` modules list
**Impact**: Uvicorn reloader child process crashed with exit code 1, triggering SIGTERM
**Fix**: Added `"routers.whatsapp"` to wiring list in `server/main.py:38`
```python
container.wire(modules=[
    "routers.auth",
    "routers.ai",
    "routers.workflow",
    "routers.database",
    "routers.maps",
    "routers.nodejs_compat",
    "routers.whatsapp",  # CRITICAL: This was missing
    "routers.android"
])
```

#### 2. Unhandled JSON Parse Errors
**Problem**: `.json()` calls without error handling raised `JSONDecodeError` when Flask returned HTML errors
**Impact**: Server crashes when WhatsApp service unavailable
**Fix**: Wrapped all `.json()` calls in try-except blocks with proper error responses

#### 3. Unhandled HTTP Status Errors
**Problem**: `response.raise_for_status()` raised `httpx.HTTPStatusError` not caught by specific handlers
**Impact**: Unhandled exceptions crashed the server
**Fix**: Removed `.raise_for_status()`, manually check `response.status_code != 200`

#### 4. Missing HTTPException Re-raise
**Problem**: Generic `Exception` handlers didn't re-raise `HTTPException`
**Impact**: Double exception wrapping and unclear errors
**Fix**: Added `except HTTPException: raise` before generic handler

### Error Handling Pattern
All WhatsApp endpoints follow this pattern:
```python
try:
    response = await client.get(url, timeout=10.0)

    # Check status manually
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="...")

    # Safe JSON parsing
    try:
        data = response.json()
        return data
    except Exception as json_err:
        logger.error(f"Failed to parse JSON: {json_err}")
        raise HTTPException(status_code=503, detail="Invalid response")

except httpx.ConnectError as e:
    raise HTTPException(status_code=503, detail="Service not running")
except httpx.TimeoutException as e:
    raise HTTPException(status_code=504, detail="Service timeout")
except HTTPException:
    raise  # Re-raise HTTPException
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    raise HTTPException(status_code=503, detail="Service unavailable")
```

### Result
- Python backend never crashes when WhatsApp service is down
- Proper HTTP error codes (503, 504, 410) returned
- No SIGTERM crashes
- Frontend receives proper error messages
- QR code viewer works seamlessly
- All mock data removed from production code

### WhatsApp Group/Sender Name Persistence
The WhatsApp Receive node stores human-readable names alongside JIDs/phone numbers:

#### Problem
When reopening the parameter panel, group/sender selectors showed the raw JID (e.g., `120363123456789@g.us`) instead of the group name because the name was only fetched when the dropdown was opened.

#### Solution
Store the name as a separate parameter alongside the ID:
- `group_id` + `group_name` - Group JID and display name
- `phone_number` + `sender_name` - Phone number and contact name

#### Implementation
```typescript
// In ParameterRenderer.tsx - GroupIdSelector
<GroupIdSelector
  value={currentValue || ''}
  onChange={onChange}
  onNameChange={(name) => onParameterChange?.('group_name', name)}
  storedName={allParameters?.group_name || ''}
  ...
/>

// GroupIdSelector stores name when selection changes
const handleChange = (value: string, option: any) => {
  onChange(value);
  if (option?.label && onNameChange) {
    onNameChange(option.label);
  }
};

// Display uses storedName when available
const displayLabel = storedName || (value && !loading ? value : '');
```

#### Key Files
- `client/src/components/ParameterRenderer.tsx` - GroupIdSelector and SenderNumberSelector with `onNameChange` and `storedName` props
- `client/src/components/parameterPanel/MiddleSection.tsx` - Passes `onParameterChange` to ParameterRenderer

## Event-Driven Trigger Node System

### Overview
Trigger nodes wait for external events (WhatsApp messages, webhooks, etc.) using Python's asyncio.Future. The backend handles all event waiting logic with the frontend displaying waiting state and providing cancel functionality.

### Architecture
```
User clicks "Run" on Trigger Node
       ↓
Frontend sends execute_node via WebSocket
       ↓
Python backend detects trigger node type (event_waiter.is_trigger_node)
       ↓
Backend registers asyncio.Future waiter with filter
       ↓
Backend broadcasts "waiting" status to frontend
       ↓
External service sends event (e.g., whatsapp_message_received)
       ↓
event_waiter.dispatch() resolves matching waiters
       ↓
Backend returns execution result with event data as output
       ↓
Frontend displays result in output panel
```

### Backend Implementation

#### Event Waiter Service (`server/services/event_waiter.py`)
Generic event waiting using standard asyncio primitives:

```python
@dataclass
class TriggerConfig:
    node_type: str
    event_type: str  # e.g., 'whatsapp_message_received'
    display_name: str

TRIGGER_REGISTRY: Dict[str, TriggerConfig] = {
    'whatsappReceive': TriggerConfig('whatsappReceive', 'whatsapp_message_received', 'WhatsApp Message'),
    'webhookTrigger': TriggerConfig('webhookTrigger', 'webhook_received', 'Webhook Request'),
    'chatTrigger': TriggerConfig('chatTrigger', 'chat_message_received', 'Chat Message'),
    'taskTrigger': TriggerConfig('taskTrigger', 'task_completed', 'Task Completed'),
    'telegramReceive': TriggerConfig('telegramReceive', 'telegram_message_received', 'Telegram Message'),
    # Future: 'emailTrigger', 'mqttTrigger', etc.
}

@dataclass
class Waiter:
    id: str
    node_id: str
    node_type: str
    event_type: str
    filter_fn: Callable[[Dict], bool]
    future: asyncio.Future

# Key functions:
def register(node_type: str, node_id: str, params: Dict) -> Waiter
def dispatch(event_type: str, data: Dict) -> int  # Returns count resolved
def cancel(waiter_id: str) -> bool
def cancel_for_node(node_id: str) -> int
def get_active_waiters() -> List[Dict]
```

#### Trigger Node Execution (`server/services/workflow.py`)
```python
async def _execute_trigger_node(self, node_id: str, node_type: str, parameters: Dict) -> Dict:
    config = event_waiter.get_trigger_config(node_type)
    waiter = event_waiter.register(node_type, node_id, parameters)

    # Broadcast waiting status
    await broadcaster.update_node_status(node_id, "waiting", {
        "message": f"Waiting for {config.display_name}...",
        "waiter_id": waiter.id
    })

    # Wait indefinitely (user cancels via cancel_event_wait)
    event_data = await waiter.future
    return {"success": True, "result": event_data, ...}
```

#### Filter Builders
Each trigger type has a filter builder that creates a function to match events:

```python
def build_whatsapp_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter for WhatsApp messages based on node parameters."""
    msg_type = params.get('messageTypeFilter', 'all')
    sender_filter = params.get('filter', 'all')  # all, any_contact, contact, group, keywords
    forwarded_filter = params.get('forwardedFilter', 'all')  # all, only_forwarded, ignore_forwarded
    # ... builds closure that checks message fields
```

**Sender Filter Options:**
- `all` - Accept all messages (groups and contacts)
- `any_contact` - Accept only non-group messages (individual chats)
- `contact` - Accept from specific phone number
- `group` - Accept from specific group (optionally filter by sender)
- `keywords` - Accept messages containing specific keywords

### WebSocket Handlers

#### Cancel Event Wait (`server/routers/websocket.py`)
```python
@ws_handler()
async def handle_cancel_event_wait(data: Dict[str, Any], websocket: WebSocket):
    """Cancel by waiter_id or node_id."""
    if waiter_id := data.get("waiter_id"):
        success = event_waiter.cancel(waiter_id)
    elif node_id := data.get("node_id"):
        count = event_waiter.cancel_for_node(node_id)
    return {"success": success, ...}

@ws_handler()
async def handle_get_active_waiters(data: Dict[str, Any], websocket: WebSocket):
    """Get list of active waiters for debugging/UI."""
    return {"waiters": event_waiter.get_active_waiters()}
```

### WhatsApp Receive Node

#### Node Definition (plugin: `server/nodes/whatsapp/whatsapp_receive.py`; pre-Wave-11 frontend shape shown below for historical reference)
```typescript
whatsappReceive: {
  displayName: 'WhatsApp Receive',
  name: 'whatsappReceive',
  icon: WHATSAPP_RECEIVE_ICON,  // Bell with notification dot
  group: ['whatsapp', 'trigger'],
  outputs: [{
    name: 'main',
    displayName: 'Message',
    type: 'main',
    description: 'message_id, sender, chat_id, message_type, text, timestamp, is_group, is_from_me, push_name, group_info'
  }],
  properties: [
    // Message Type Filter: all, text, image, video, audio, document, location, contact
    // Sender Filter: all, contact (specific phone), group (specific group), keywords
    // Ignore Own Messages: boolean (default true)
    // Include Media Data: boolean (default false)
  ]
}
```

#### Output Schema (backend, `server/services/node_output_schemas.py`)
Runtime output shapes live on the backend and are fetched lazy by InputSection per the Wave 3 source-of-truth decision. The WhatsApp Receive schema:
```python
class WhatsAppGroupInfo(BaseModel):
    group_jid: Optional[str] = None
    sender_jid: Optional[str] = None
    sender_phone: Optional[str] = None   # Resolved phone number (Go RPC resolves LIDs before sending event)
    sender_name: Optional[str] = None

class WhatsAppReceiveOutput(_OutputBase):
    message_id: Optional[str] = None
    sender: Optional[str] = None
    sender_phone: Optional[str] = None
    chat_id: Optional[str] = None
    message_type: Optional[str] = None
    text: Optional[str] = None
    timestamp: Optional[str] = None
    is_group: Optional[bool] = None
    is_from_me: Optional[bool] = None
    push_name: Optional[str] = None
    media: Optional[dict] = None
    group_info: Optional[WhatsAppGroupInfo] = None
    newsletter_meta: Optional[dict] = None

NODE_OUTPUT_SCHEMAS["whatsappReceive"] = WhatsAppReceiveOutput
```
Served via `GET /api/schemas/nodes/whatsappReceive.json` + `get_node_output_schema` WS handler. See [docs-internal/schema_source_of_truth_rfc.md](./docs-internal/schema_source_of_truth_rfc.md).

### Task Trigger Node

The Task Trigger node fires when a delegated child agent completes its task (success or error). This enables parent agents to react to child completion via workflow nodes.

#### Node Definition (plugin: `server/nodes/trigger/task_trigger.py`; pre-Wave-11 frontend shape shown below for historical reference)
```typescript
taskTrigger: {
  displayName: 'Task Completed',
  name: 'taskTrigger',
  icon: '📨',
  group: ['trigger', 'workflow'],
  outputs: [{
    name: 'main',
    displayName: 'Output',
    type: 'main',
    description: 'task_id, status, agent_name, result/error, parent_node_id'
  }],
  properties: [
    // Task ID Filter: Optional specific task ID to watch
    // Agent Name Filter: Optional partial match on agent name
    // Status Filter: all, completed, error
    // Parent Node ID: Optional filter by parent agent node
  ]
}
```

#### Output Schema (`client/src/components/parameterPanel/InputSection.tsx`)
```typescript
taskTrigger: {
  task_id: 'string',
  status: 'string',      // 'completed' or 'error'
  agent_name: 'string',
  agent_node_id: 'string',
  parent_node_id: 'string',
  result: 'string',      // Present when status='completed'
  error: 'string',       // Present when status='error'
  workflow_id: 'string',
}
```

#### Event Dispatch (`server/services/handlers/tools.py`)
The `task_completed` event is dispatched when a delegated child agent finishes:
```python
# On success:
await broadcaster.send_custom_event('task_completed', {
    'task_id': task_id,
    'status': 'completed',
    'agent_name': agent_label,
    'agent_node_id': node_id,
    'parent_node_id': config.get('parent_node_id', ''),
    'result': result.get('result', {}).get('response', ...),
    'workflow_id': workflow_id,
})

# On error:
await broadcaster.send_custom_event('task_completed', {
    'task_id': task_id,
    'status': 'error',
    'agent_name': agent_label,
    'agent_node_id': node_id,
    'parent_node_id': config.get('parent_node_id', ''),
    'error': str(e),
    'workflow_id': workflow_id,
})
```

### Adding New Trigger Types

1. **Add to Registry** in `server/services/event_waiter.py`:
   ```python
   TRIGGER_REGISTRY['emailTrigger'] = TriggerConfig('emailTrigger', 'email_received', 'Email')
   ```

2. **Add Filter Builder**:
   ```python
   def build_email_filter(params: Dict) -> Callable[[Dict], bool]:
       # Build filter based on node parameters
   FILTER_BUILDERS['emailTrigger'] = build_email_filter
   ```

3. **Add the plugin** at `server/nodes/<category>/<trigger_name>.py`:
   - Define the `NodeSpec` (inputs / outputs / uiHints) as a subclass
     or dataclass per the plugin system
   - Implement the trigger-handler `execute` method

4. **Add Output Schema** in `InputSection.tsx`:
   ```typescript
   email: { from: 'string', subject: 'string', body: 'string', ... }
   ```

5. **Dispatch Events** from external service:
   ```python
   from services import event_waiter
   event_waiter.dispatch('email_received', email_data)
   ```

### Polling Triggers (Gmail, Twitter)

Some triggers require active API polling instead of waiting for externally dispatched events. These use `setup_polling_trigger` in `TriggerManager` instead of `setup_event_trigger`.

**Architecture:**
```
setup_polling_trigger() → broadcasts "waiting" status
       ↓
   poller task: runs poll_coroutine(queue, is_running_fn)
       ↓                    ↓
   polls API at interval → enqueues new items to asyncio.Queue
       ↓
   processor task: reads queue → calls on_event → spawns execution run
```

**Key differences from event triggers:**
- Event triggers: `event_waiter.register()` + `wait_for_event()` (push-based)
- Polling triggers: Custom poll coroutine + `asyncio.Queue` (pull-based)

**Routing** (`server/services/deployment/manager.py`):
```python
if node_type in POLLING_TRIGGER_TYPES:  # gmailReceive, twitterReceive
    poll_coroutine = self._create_poll_coroutine(node_type, node_id, params)
    await trigger_manager.setup_polling_trigger(...)
```

**Constants** (`server/constants.py`):
- `POLLING_TRIGGER_TYPES`: `frozenset(['gmailReceive', 'twitterReceive'])`
- These are also in `WORKFLOW_TRIGGER_TYPES` for trigger node detection

### Key Design Decisions

- **No Timeout**: Trigger nodes wait indefinitely; users cancel via Cancel button
- **Backend-First**: All event waiting logic in Python backend, minimal frontend changes
- **Generic Architecture**: Same execution flow for all trigger types via registry
- **Filter Functions**: Each trigger type builds its own filter from node parameters
- **asyncio.Future**: Simpler than asyncio.Event for single-value resolution
- **Polling triggers**: Use asyncio.Queue + dedicated poll coroutine for APIs without push support

## Real-time Status WebSocket System

### Overview
The frontend and Python backend communicate via WebSocket for real-time status updates. This replaces API polling with push-based updates for Android connection status, node execution status, and variable changes.

### Architecture
```
React Frontend (WebSocketContext.tsx) <--WebSocket--> Python Backend (status_broadcaster.py)
         |                                                    |
         v                                                    v
   SquareNode.tsx                                   websocket_client.py
   (uses androidStatus)                             (broadcasts Android status)
```

### Backend Implementation

#### Status Broadcaster (`server/services/status_broadcaster.py`)
Central service for managing WebSocket connections and broadcasting status updates:

```python
class StatusBroadcaster:
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._status: Dict[str, Any] = {
            "android": {"connected": False, "device_id": None, "connected_devices": [], "connection_type": None},
            "nodes": {},
            "variables": {},
            "workflow": {"executing": False, "current_node": None}
        }

    async def connect(self, websocket: WebSocket): ...
    async def disconnect(self, websocket: WebSocket): ...
    async def update_android_status(self, connected, device_id, connected_devices, connection_type): ...
    async def update_node_status(self, node_id, status, data): ...
    async def update_variable(self, name, value): ...
    async def update_workflow_status(self, executing, current_node, progress): ...
```

Key methods:
- `connect()` - Accepts WebSocket, adds to connection set, sends initial status
- `update_android_status()` - Updates Android status and broadcasts to all clients
- `update_node_status()` - Updates individual node status with data/output
- `update_variable()` - Updates single variable value
- `_broadcast()` - Sends message to all connected clients

#### WebSocket Router (`server/routers/websocket.py`)
FastAPI WebSocket endpoint:

```python
@router.websocket("/ws/status")
async def websocket_status_endpoint(websocket: WebSocket):
    broadcaster = get_status_broadcaster()
    await broadcaster.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "get_status":
                await websocket.send_json({"type": "full_status", "data": broadcaster.get_status()})
    except WebSocketDisconnect:
        await broadcaster.disconnect(websocket)
```

### Frontend Implementation

#### WebSocket Context (`client/src/contexts/WebSocketContext.tsx`)
React context providing WebSocket connection and status state:

```typescript
export interface AndroidStatus {
  connected: boolean;
  device_id: string | null;
  connected_devices: string[];
  connection_type: string | null;
}

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [androidStatus, setAndroidStatus] = useState<AndroidStatus>(defaultAndroidStatus);
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, NodeStatus>>({});
  const [variables, setVariables] = useState<Record<string, any>>({});
  // WebSocket connection with auto-reconnect
};

// Hooks for consuming status
export const useWebSocket = (): WebSocketContextValue => { ... }
export const useAndroidStatus = (): AndroidStatus => { ... }
export const useNodeStatus = (nodeId: string): NodeStatus => { ... }
```

Features:
- Auto-connect on mount
- Auto-reconnect after 3 seconds on disconnect
- Ping every 30 seconds to keep connection alive
- Message type handlers for all status update types

#### Usage in Components (`client/src/components/SquareNode.tsx`)
```typescript
const { androidStatus } = useWebSocket();
// Android service nodes use 'paired' status (device must be paired to execute)
const isAndroidConnected = isAndroidNode && androidStatus.paired;
```

### Android Status Broadcasting
The Android relay client (`server/services/android/client.py`) broadcasts status changes via dedicated functions in `broadcaster.py`:

```python
# When device pairs successfully
await broadcast_connected(device_id, device_name)

# When device unpairs (relay may still be connected)
await broadcast_device_disconnected(
    relay_connected=self.is_connected(),
    qr_data=self.qr_data,
    session_token=self.session_token
)

# When relay WebSocket closes unexpectedly
await broadcast_relay_disconnected()
```

**Key distinction:**
- `broadcast_device_disconnected()` - Device unpaired, relay still connected (can re-pair via QR)
- `broadcast_relay_disconnected()` - Full disconnection, need to reconnect

### Real Device Detection
Fixed issue where Android status remained green after device disconnect:

**Problem**: Base name `android_system_services` remained in connected devices set after real device `android_system_services_1764708352672` left.

**Solution**: Added methods to distinguish real devices (with timestamp suffix) from base names:

```python
def _get_discovered_devices(self) -> list:
    """Get list of actual discovered devices (with timestamp suffix)."""
    discovered = []
    for device_id in self._connected_android_devices:
        parts = device_id.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            discovered.append(device_id)
    return discovered

def has_real_android_devices(self) -> bool:
    """Check if there are any real (discovered) Android devices connected."""
    return len(self._get_discovered_devices()) > 0
```

Updated `client_left` and presence handlers use `has_real_android_devices()` instead of checking total device count.

### WebSocket Message Types (127 Handlers)

#### Request/Response Messages (Client -> Server -> Client)
| Category | Message Types |
|----------|--------------|
| **Status/Ping** | `ping`, `get_status`, `get_android_status`, `get_node_status`, `get_variable` |
| **Node Parameters** | `get_node_parameters`, `get_all_node_parameters`, `save_node_parameters`, `delete_node_parameters` |
| **Tool Schemas** | `get_tool_schema`, `save_tool_schema`, `delete_tool_schema`, `get_all_tool_schemas` |
| **Node Execution** | `execute_node`, `execute_workflow`, `cancel_execution`, `get_node_output`, `clear_node_output` |
| **Triggers/Events** | `cancel_event_wait`, `get_active_waiters` |
| **Dead Letter Queue** | `get_dlq_entries`, `get_dlq_entry`, `get_dlq_stats`, `replay_dlq_entry`, `remove_dlq_entry`, `purge_dlq` |
| **Deployment** | `deploy_workflow`, `cancel_deployment`, `get_deployment_status`, `get_workflow_lock`, `update_deployment_settings` |
| **AI Operations** | `execute_ai_node`, `get_ai_models` |
| **API Keys** | `validate_api_key`, `get_stored_api_key`, `save_api_key`, `delete_api_key` |
| **Claude OAuth** | `claude_oauth_login`, `claude_oauth_status` |
| **Twitter OAuth** | `twitter_oauth_login`, `twitter_oauth_status`, `twitter_logout` |
| **Google OAuth** | `google_oauth_login`, `google_oauth_status`, `google_logout` |
| **AI Proxy** | `test_ai_proxy` |
| **Android** | `get_android_devices`, `execute_android_action`, `android_relay_connect`, `android_relay_disconnect`, `android_relay_reconnect` |
| **Maps** | `validate_maps_key` |
| **Apify** | `validate_apify_key` |
| **WhatsApp** | `whatsapp_status`, `whatsapp_connected_phone`, `whatsapp_qr`, `whatsapp_send`, `whatsapp_start`, `whatsapp_restart`, `whatsapp_groups`, `whatsapp_group_info`, `whatsapp_chat_history`, `whatsapp_newsletters`, `whatsapp_rate_limit_get`, `whatsapp_rate_limit_set`, `whatsapp_rate_limit_stats`, `whatsapp_rate_limit_unpause`, `whatsapp_mark_read`, `whatsapp_typing`, `whatsapp_presence`, `whatsapp_stop`, `whatsapp_diagnostics` |
| **Telegram** | `telegram_connect`, `telegram_disconnect`, `telegram_status`, `telegram_send`, `telegram_reconnect`, `telegram_get_me`, `telegram_get_chat` |
| **Workflow Storage** | `save_workflow`, `get_workflow`, `get_all_workflows`, `delete_workflow` |
| **Chat Messages** | `send_chat_message`, `get_chat_messages`, `clear_chat_messages`, `save_chat_message`, `get_chat_sessions` |
| **Console/Terminal** | `get_console_logs`, `clear_console_logs`, `get_terminal_logs`, `clear_terminal_logs` |
| **User Skills** | `get_user_skills`, `get_user_skill`, `create_user_skill`, `update_user_skill`, `delete_user_skill` |
| **Built-in Skills** | `get_skill_content`, `save_skill_content`, `scan_skill_folder`, `list_skill_folders` |
| **Memory/Skill Reset** | `clear_memory`, `reset_skill` |
| **User Settings** | `get_user_settings`, `save_user_settings` |
| **Provider Defaults** | `get_provider_defaults`, `save_provider_defaults` |
| **Pricing** | `get_pricing_config`, `save_pricing_config` |
| **Usage/Compaction** | `get_api_usage_summary`, `get_compaction_stats`, `configure_compaction`, `get_provider_usage_summary` |
| **Agent Teams** | `create_team`, `get_team`, `get_team_status`, `dissolve_team`, `add_team_task`, `claim_team_task`, `complete_team_task`, `get_team_tasks`, `send_team_message`, `get_team_messages` |
| **Model Registry** | `get_model_constraints`, `refresh_model_registry` |

#### Broadcast Messages (Server -> All Clients)
| Message Type | Description |
|--------------|-------------|
| `android_status` | Android device connection update |
| `node_status` | Node execution status change |
| `node_output` | Node execution output data |
| `agent_progress` | CloudEvents v1.0 envelope (type=`agent.progress`) — per-step agent-loop iteration count, drives the live "N / max" badge on AI Agent canvas nodes |
| `variable_update` | Single variable value change |
| `workflow_status` | Workflow execution progress |
| `api_key_status` | API key validation status |
| `node_parameters_updated` | Node parameters changed by another client |

#### Status Messages
| Message Type | Direction | Description |
|--------------|-----------|-------------|
| `initial_status` | Server -> Client | Full status on connect |
| `full_status` | Server -> Client | Full status response |
| `pong` | Server -> Client | Keep-alive response |
| `error` | Server -> Client | Error response with code and message |

## WebSocket Hooks

### useWhatsApp (`client/src/hooks/useWhatsApp.ts`)
Hook for WhatsApp operations via WebSocket:
```typescript
const { getStatus, getQRCode, sendMessage, startConnection, isLoading, connectionStatus } = useWhatsApp();
```

### useExecution (`client/src/hooks/useExecution.ts`)
Hook for node execution via WebSocket:
```typescript
const { executeNode, cancelExecution, isExecuting, lastResult } = useExecution();
```

### useApiKeys (`client/src/hooks/useApiKeys.ts`)
Hook for API key management via WebSocket:
```typescript
const { validateApiKey, getStoredKey, saveApiKey, deleteApiKey } = useApiKeys();
```

### useAndroidOperations (`client/src/hooks/useAndroidOperations.ts`)
Hook for Android device operations via WebSocket:
```typescript
const { getDevices, executeAction, setupDevice, isConnected, deviceStatus } = useAndroidOperations();
```

### useParameterPanel (`client/src/hooks/useParameterPanel.ts`)
Hook for parameter management via WebSocket:
```typescript
const { parameters, saveParameters, loadParameters, isDirty } = useParameterPanel(nodeId);
```

### Conditional Parameter Display Implementation
Located in `client/src/components/parameterPanel/MiddleSection.tsx`:

```typescript
const shouldShowParameter = (param: INodeProperties, allParameters: Record<string, any>): boolean => {
  if (!param.displayOptions?.show) {
    return true;
  }

  const showConditions = param.displayOptions.show;

  for (const [paramName, allowedValues] of Object.entries(showConditions)) {
    const currentValue = allParameters[paramName];

    if (Array.isArray(allowedValues)) {
      if (!allowedValues.includes(currentValue)) {
        return false;
      }
    } else {
      if (currentValue !== allowedValues) {
        return false;
      }
    }
  }

  return true;
};
```

This function:
- Checks if parameter has displayOptions.show configuration
- Evaluates all show conditions against current parameter values
- Returns false if any condition fails (parameter hidden)
- Returns true if all conditions pass (parameter visible)
- Applied before rendering: `.filter(param => shouldShowParameter(param, parameters))`

## Planned Features

### Workflow-Level Execution (n8n-style Parallel Workflows)

**Current Limitations:**
- Single workflow execution at a time (global `_deployment_running` flag)
- Nodes fetch status on component mount, not when workflow is selected
- Status broadcasts to all clients without workflow filtering
- No isolation between workflow executions

**Planned Architecture:**

1. **Defer Node Status Checks Until Workflow Selected**
   - Remove eager `getStatus()` calls from WhatsAppNode mount (lines 44-48)
   - Remove eager `checkConfiguration()` from SquareNode mount (lines 46-92)
   - Status should only fetch when workflow containing those nodes is selected
   - Use cached status from WebSocket context instead of per-node fetching

2. **Workflow-Isolated Execution Context**
   ```python
   # server/services/workflow.py
   class ExecutionContext:
       def __init__(self, workflow_id: str, session_id: str):
           self.workflow_id = workflow_id
           self.session_id = session_id
           self.outputs: Dict[str, Any] = {}
           self.iteration = 0
           self.running = False
           self.task: Optional[asyncio.Task] = None

   # Replace single deployment state with:
   self._execution_contexts: Dict[str, ExecutionContext] = {}
   ```

3. **Parallel Workflow Deployment**
   - Each workflow gets unique `workflow_id` in execution requests
   - Backend tracks `_execution_contexts[workflow_id]` instead of single `_deployment_running`
   - Cancel by `workflow_id` instead of globally
   - Status broadcasts include `workflow_id` for client filtering

4. **Frontend Changes**
   - `WebSocketContext`: Add `activeWorkflowId`, filter status by workflow
   - `useAppStore`: Add `runningWorkflows: Set<string>` to track parallel executions
   - `WorkflowSidebar`: Show running indicator next to deployed workflows
   - `Dashboard`: Pass `workflow_id` to all execution calls

**Files to Modify:**
- `client/src/components/WhatsAppNode.tsx` - Remove mount status fetch
- `client/src/components/SquareNode.tsx` - Remove mount config check
- `client/src/contexts/WebSocketContext.tsx` - Add workflow filtering
- `client/src/store/useAppStore.ts` - Track running workflows
- `server/services/workflow.py` - ExecutionContext class, parallel support
- `server/routers/websocket.py` - workflow_id in messages
- `server/services/status_broadcaster.py` - workflow_id filtering

## Notes
- **No Legacy Support**: Pure modern methods only, backward compatibility removed
- **Interface Alignment**: ParameterRenderer supports both interface types seamlessly
- **Execution Ready**: Components can be executed with real-time result display
- **Clean Codebase**: Significant file and code reduction while maintaining full functionality
- **Modular Backend**: workflow.py reduced from 2068 to 460 lines via facade pattern
  - NodeExecutor: Registry-based dispatch with `functools.partial` for dependency injection
  - ParameterResolver: Compiled regex for `{{node.field}}` template resolution
  - DeploymentManager: Handles deploy/cancel lifecycle with TriggerManager for cron/events
  - No global state: `_active_cron_jobs` moved to TriggerManager instance variable
- **Performance**: Fast HMR updates and clean TypeScript compilation
- **AI Architecture**: 5-layer system with factory pattern and secure credential management
- **Android Architecture**: Factory-based node creation with ADB integration for device automation
- **WebSocket-First Architecture**: most frontend-backend RPC (parameters, execution, API keys, Android, WhatsApp, skill operations) goes through WebSocket. Live handler set lives in the `MESSAGE_HANDLERS` dict in `server/routers/websocket.py` plus plugin-registered handlers via `services.ws_handler_registry`.
- **WebSocket Hooks**: Dedicated React hooks (useWhatsApp, useExecution, useApiKeys, useAndroidOperations, useParameterPanel) for clean component integration
- **WebSocket Support**: Persistent remote Android device connections via WebSocket proxy with background tasks
  - Connection stays alive across multiple API requests until switched to local ADB
  - Background message receiver and keepalive loop (25s interval)
  - Message queue for async message handling with filtering logic
  - Connection reuse reduces execution time from 0.18s to 0.0003s
- **Real-time Status WebSocket**: Frontend-backend WebSocket at `/ws/status` for live updates
  - Android connection status broadcasts when devices connect/disconnect
  - Node execution status and output updates
  - Variable value changes
  - Workflow execution progress
  - Replaces API polling with push-based updates
  - Auto-reconnect with 3-second delay on disconnect
  - Real device detection distinguishes actual devices (with timestamp suffix) from base names
  - **Android two-state model**: `connected` (relay WebSocket) vs `paired` (device paired)
    - Android service nodes use `paired` status for indicator (green = paired, red = not paired)
    - Relay can be connected without a device (shows QR for pairing)
    - Device disconnect broadcasts `paired=false` while keeping `connected=true` for re-pairing
- **Conditional Display**: Full implementation of displayOptions.show pattern for dynamic UI rendering
- **Process Management**: Robust stop scripts handle duplicate processes with verification and retry
- **Process Independence**: Removed `--kill-others` from concurrently to prevent cascading crashes when uvicorn reloads
- **WhatsApp Integration**: Square node design with QR code viewer, proper error handling, no crashes
  - Critical fix: Added "routers.whatsapp" to dependency injection wiring
  - All endpoints use safe JSON parsing with comprehensive error handling
  - Backend proxies all requests to WhatsApp RPC service (default port 9400, configurable)
  - Returns proper HTTP status codes (503, 504, 410) instead of mock data
  - Python server never crashes when WhatsApp service is unavailable
  - WebSocket handlers for status, QR code, send message, and start connection
  - useWhatsApp hook provides clean React component integration
  - Uses external npm package `whatsapp-rpc` with pre-built Go binaries
  - Newsletter channels: send to channels (text/image/video/audio/document), query channel DB (list, info, messages, stats), follow/unfollow, create, mute, mark viewed, react, live updates. Channel JID format: `<numeric_id>@newsletter`. All 14 WhatsApp events handled (including `event.history_sync_complete`).
  - Media download: `include_media_data` param on chat_history and channel_messages triggers `media` RPC for base64 media retrieval
  - Channel message filters: date range (since/until), media type, text search, pagination offset
  - WebSocket RPC passthroughs: mark_read, typing, presence, stop, diagnostics
- **Event-Driven Triggers**: Generic trigger node architecture with asyncio.Future
  - `server/services/event_waiter.py` - Waiter registration, dispatch, cancellation
  - TRIGGER_REGISTRY for extensible trigger types (WhatsApp, Webhook, Telegram, future: Email, MQTT)
  - Filter builders create closures from node parameters (whatsapp_filter, webhook_filter)
  - No timeout - wait indefinitely until event or user cancel
  - WebSocket handlers: `cancel_event_wait`, `get_active_waiters`
  - **Trigger State Machine** (n8n pattern):
    - `idle` → `waiting` (on deploy, cyan indicator)
    - `waiting` → `idle` (on event received, graph starts executing, green indicator)
    - `idle` → `waiting` (after graph completes, listening again)
    - Triggers NEVER show `executing` status - only downstream nodes do
  - **Sequential Queue Processing**: Events are queued and processed one at a time via `wait_for_completion=True`
- **HTTP/Webhook Integration**: 3 utility nodes for HTTP communication
  - `httpRequest` - Make outgoing HTTP requests with httpx async client
  - `webhookTrigger` - Receive incoming HTTP requests at `/webhook/{path}`
  - `webhookResponse` - Send custom responses back to webhook callers
  - `server/routers/webhook.py` - Dynamic webhook router using broadcaster.send_custom_event()
  - Output panel shows clean summaries (method, path, body for webhooks; status code for HTTP)
- **n8n-Pattern Cache System**: Automatic fallback hierarchy for different environments
  - Production (Docker): Redis → SQLite → Memory
  - Local Development: SQLite → Memory (Redis disabled via `REDIS_ENABLED=false`)
  - `server/core/cache.py` - CacheService with fallback logic
  - `server/models/cache.py` - CacheEntry SQLModel for SQLite persistence
  - `server/core/database.py` - Cache CRUD methods (get/set/delete/cleanup)
  - Supports TTL expiration and automatic cleanup of expired entries
- **Conditional Redis with Docker Profiles**: Redis container only starts when explicitly enabled
  - Uses Docker Compose profiles: Redis service has `profiles: [redis]`
  - `scripts/docker.js` wrapper auto-adds `--profile redis` when `REDIS_ENABLED=true` in `.env`
  - npm scripts (`docker:up`, `docker:down`, etc.) use the wrapper for seamless handling
  - Backend no longer depends on Redis - depends only on WhatsApp service
- **WebSocket Reconnect via PartySocket** (`partysocket/ws` from Cloudflare): native-WS-compatible class handles jittered exponential backoff, message replay (`maxEnqueuedMessages: 200`), and intentional-close (RFC 6455 §7.4.1 code 1000) automatically. Replaces the previous flat 3 s `setTimeout(connect, 3000)` loop. Reconnect envelope (`MIN_DELAY_MS: 250`, `MAX_DELAY_MS: 8000`, `GROW_FACTOR: 1.3`) lives in `client/src/lib/connectionConfig.ts`. The +12 s WS-drop-and-reconnect cycle observed under React Strict Mode is gone — verified across two consecutive cold launches with zero `Client disconnected` events through 130+ s of activity. See `docs-internal/performance.md` for measurements.
- **Docker Backend Fix**: Backend container uses Python uvicorn directly
  - Changed from `npm run start` (failed - npm not in Python image) to `python -m uvicorn`
  - CMD: `["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3010"]`
- **Configurable Authentication**: `VITE_AUTH_ENABLED` environment variable
  - Set to `false` to bypass login entirely (useful for local development)
  - Frontend creates anonymous user with owner privileges when disabled
  - AuthContext bootstraps via TanStack Query `useQuery({queryKey: ['auth','status'], retry, retryDelay, signal})` — replaced the previous recursive `setTimeout` chain. Full-jitter exponential backoff per the AWS Architecture Blog formula `random(0, min(CAP_MS, BASE_MS * 2^attempt))` with `BASE_MS=50`, `CAP_MS=4000`, `MAX_ATTEMPTS=7` (constants in `client/src/lib/connectionConfig.ts`). Cumulative budget ~10 s vs. the old 31 s; sub-second granularity early covers the typical 4 s backend cold-start window in 4-5 attempts. 401/403 short-circuit the retry chain (no budget burn for "auth disabled / not logged in" responses). `signal` (AbortController) plumbed through `queryFn` so unmount + Strict Mode cleanup cancel in-flight requests automatically. login / register / logout invalidate the cache via `queryClient.invalidateQueries(AUTH_STATUS_QUERY_KEY)`.
  - Pydantic Settings accepts `vite_auth_enabled` field (required due to `extra="forbid"`)
- **CloudEvents v1.0 envelope on the wire (preserved end-to-end)**: backend `WorkflowEvent` ([server/services/events/envelope.py](./server/services/events/envelope.py)) and frontend `WorkflowEvent<T>` interface ([client/src/types/cloudEvents.ts](./client/src/types/cloudEvents.ts)) mirror each other. `matchesType(event, 'credential.api_key.*')` parity-tested vs `WorkflowEvent.matches_type` Pydantic method. Today wraps inside the legacy `{type, data}` WS frame (e.g. `credential_catalogue_updated` → `data: <envelope>`); future Wave 12 sources (`stripe.*`, `telegram.*`, `task.*`) drop into the same envelope so a single FE switch case routes them via glob dispatch instead of inventing per-source wire keys.
- **whatsapp-rpc Package**: External dependency for WhatsApp integration
  - Published to npm as `whatsapp-rpc` (unscoped) and GitHub Packages as `@trohitg/whatsapp-rpc`
  - Published to PyPI as `whatsapp-rpc` (async Python client)
  - Cross-platform binaries built via GitHub Actions (linux/amd64, linux/arm64, darwin/amd64, darwin/arm64, windows/amd64)
  - Binary downloaded from GitHub releases during npm postinstall
  - Configurable port via `--port` CLI flag, `PORT` or `WHATSAPP_RPC_PORT` env vars (default: 9400)
  - QR codes generated as base64 PNG in memory (no file I/O, no `data/qr` directory)
  - Source: https://github.com/trohitg/whatsapp-rpc
- **Node Data Architecture**: `node.data` only stores `label` (display name). All parameters are stored in the database via `save_node_parameters` WebSocket handler. This prevents parameter bloat in workflow JSON exports and keeps React Flow state lightweight. `useDragAndDrop.ts` saves default parameters to DB on drop, not to `node.data`.
- **Workflow Export/Import with Parameters**: Exported workflow JSON includes a `nodeParameters` field containing all node configuration (provider, model, prompt, skillsConfig, etc.) fetched from the database at export time. On import, embedded `nodeParameters` are saved back to the database. `sanitizeNodes()` in `workflowExport.ts` still strips `node.data` to UI-only fields (`label`, `disabled`, `condition`). A `parameterSanitizer.ts` utility exists for credential stripping but is currently disabled (pass-through). Old exports without `nodeParameters` import cleanly (backward compatible). Key files: `client/src/utils/workflowExport.ts`, `client/src/utils/parameterSanitizer.ts`, `client/src/Dashboard.tsx` (export/import handlers), `server/services/example_loader.py`.
- **Skill System Architecture**: Skills organized in `server/skills/<folder>/` subfolders. Each folder appears in Master Skill dropdown. DB is source of truth for skill instructions (seeded from SKILL.md on first load). Icon resolution: node definition (SVG) > SKILL.md metadata (emoji) > fallback. Native DOM keydown handler prevents React Flow from intercepting Ctrl shortcuts in skill editor.
- **Example Workflows**: Auto-load example workflows from `workflows/` folder on first use. Uses `UserSettings.examples_loaded` flag to track import status. Supports anonymous users (`user_id="default"`). Reuses existing `database.save_workflow()` for import. Embedded `nodeParameters` in example JSON files are saved to the database on import. See "Example Workflows" section for details.
- **Onboarding Service**: 5-step welcome wizard (Welcome, Concepts, API Keys, Canvas Tour, Get Started) using Ant Design Steps/Card/Button/Typography. Database-backed via `UserSettings.onboarding_completed` + `onboarding_step`. Existing users auto-skip (migration marks `examples_loaded=1` as completed). Replayable from Settings "Help" section. No new WebSocket handlers needed. See [Onboarding Service](./docs-internal/onboarding.md) for details.
- **Node.js Code Executor**: Persistent Node.js server (Express + tsx) at port 3020 for JavaScript/TypeScript execution, replacing subprocess spawning per execution. Handlers in `server/services/handlers/code.py` call `NodeJSClient` which makes HTTP requests to the Node.js server. All config via environment variables (`NODEJS_EXECUTOR_URL`, `NODEJS_EXECUTOR_PORT`, etc.).
- **writeTodos Tool Node**: Dedicated AI tool for task planning connecting to any agent's `input-tools` handle. `TodoService` singleton (`server/services/todo_service.py`) stores JSON-based per-session todo state keyed by workflow_id. Handler broadcasts `phase: "todo_update"` via WebSocket on each update for real-time UI; `formatTodoOutput()` in `OutputDisplayPanel.tsx` renders the result as a checklist with `[ ]` / `[~]` / `[x]` icons. Schema uses `TodoItem`/`TodoStatus` Pydantic enum. Skill at `server/skills/assistant/write-todos-skill/SKILL.md` teaches the plan-work-update loop.
- **Temporal Activity Heartbeats**: Activities send `activity.heartbeat()` on every non-matching WebSocket broadcast inside the read loop in `services/temporal/activities.py`. This keeps long-running browser and claude_code_agent operations alive past the 2-minute `heartbeat_timeout`. Start/end heartbeats alone were causing `TIMEOUT_TYPE_HEARTBEAT` failures on ops taking 5-10 minutes. Connection config: `heartbeat=30`, `receive_timeout=540` (fits within 10-min `start_to_close_timeout`).
- **WebSocket `_safe_send` Guard**: `server/routers/websocket.py` checks `websocket.client_state.name != "CONNECTED"` before sending and logs at `debug` level (not `error`) on failure. Prevents "ASGI message after websocket.close" errors when broadcasts race with disconnects.
- **Claude Code CLI Flag**: `server/services/claude_code_service.py` uses `--max-budget-usd <amount>` (previously incorrectly `--max-cost`, which caused "unknown option" errors on every run).
- **Persisted-prefix cache contract**: `PersistQueryClientProvider` hydrates entries with the QueryClient's *default* options, so any prefix in `PERSISTED_KEY_PREFIXES` (`client/src/lib/queryPersist.ts`) MUST also have a matching `queryClient.setQueryDefaults(['<prefix>'], { staleTime: FOREVER, gcTime: FOREVER })` declaration in `client/src/lib/queryClient.ts`. Per-call options don't apply on hydration. Canonical set: `nodeSpec`, `nodeGroups`, `skillContent`. `credentialValues` was previously persisted here but was removed per OWASP HTML5 Security Cheat Sheet / ASVS V9.9 — decrypted API keys must never live in `localStorage`. The in-memory TanStack Query cache (`gcTime: ∞`) keeps the credentials form populated for the session lifetime; on reload the panel refetches via WS. `credentialCatalogue` has its own `idb-keyval` warm-start path so it is intentionally not in either list.
- **WebSocket `sendRequest` replay queue**: when the socket is not open, requests enqueue with `AbortController`-backed per-request timeouts (default 30s) and replay on reconnect inside `ws.onopen` before `setIsReady(true)`. Queue capped at 200 with FIFO eviction. Intentional close (`event.code === 1000`) drops the queue; transient closes preserve it. Eliminates indefinite spinners during the 3s reconnect window. Implementation in `client/src/contexts/WebSocketContext.tsx` (`pendingSendQueueRef` + `drainPendingSends`).
- **`currentWorkflowId` single source**: lives in `useAppStore.currentWorkflow.id` only. Non-React listeners (WS handlers) read via `useAppStore.getState().currentWorkflow?.id` -- the documented Zustand escape hatch. The push to `nodeStatusStore.setCurrentWorkflowId` is driven from one `useEffect` in `Dashboard.tsx`. Removed the prior `currentWorkflowIdRef` mirror inside WebSocketContext that misrouted broadcasts during workflow switches.
- **Execution correlation IDs**: `handle_execute_node` issues a `uuid4().hex` token at request entry, propagates it through every `node_status` / `node_output` broadcast for the run, and returns it in the response payload as `execution_id`. Frontend `ExecutionResult.executionId` carries it; `OutputSection` dedups runs by it instead of `JSON.stringify(outputs)` (which collapsed distinct executions whose payloads matched). Same trace-id pattern as OpenTelemetry / HTTP request ids.
- **`clear_node_status` idle reset**: `StatusBroadcaster.clear_node_status(node_id)` resets the slot to `{status: "idle", data: {}, cleared: true}` instead of `del`'ing it. Deleting created a race window where the in-flight execution's `success` broadcast re-created the entry and stuck the UI on "completed" for a cancelled node. Idle reset preserves entry identity so subsequent broadcasts update normally; the `cleared: true` flag distinguishes "never ran" from "explicitly cleared."
- **`get_node_output` race**: in-memory `_outputs` cache re-population after a DB-fallback `await` previously overwrote a fresh in-memory write with a stale DB value. Fix uses nested `dict.setdefault` (atomic at the CPython GIL level -- no lock needed). `store_node_output` and `clear_all_outputs` had no real race because asyncio coroutines do not preempt at synchronous statements. Reference for the Python concurrency model: https://docs.python.org/3/library/asyncio-task.html#asyncio-await
- **`NodeUserError` (services.plugin)**: typed exception for user/LLM-correctable failures (string not found, command not found, bad cwd, missing required field, operator-only Twitter query, Python sandbox `import`, Node.js sidecar down). `BaseNode.execute()` catches it specifically: single WARN line in the operator log (no traceback) + structured `{success: False, error_type: "NodeUserError", error: ...}` envelope. Genuine bugs still flow through the generic `except Exception` branch and keep their full stacktrace via `logger.exception`. Adopted across `fileRead` / `fileModify` / `fsSearch` / `process_manager` / `pythonExecutor` / `javascriptExecutor` / `typescriptExecutor`. Reach for it whenever the LLM (or user) can fix the input and retry — never for actual server bugs.
- **Process manager Windows shim resolution**: `process_service.start()` resolves `argv[0]` via `shutil.which()` before `asyncio.create_subprocess_exec`. On Windows, `shutil.which` honours `PATHEXT` and returns the absolute `.cmd` path (e.g. `C:\...\npm.cmd`); `CreateProcessW` then launches it directly. No `cmd /c` wrap — same canonical idiom used by `browser_service`, `claude_code_service`, `claude_oauth`, `himalaya_service`. Without this, bare `argv[0]="npm"` raises `WinError 2` because `CreateProcessW` does NOT apply `PATHEXT` to bare names. Missing binary → early `Command not found: '<bin>'. Check spelling or ensure the binary is on PATH.` envelope (no traceback).
- **Telegram message auto-split**: Telegram Bot API caps `sendMessage.text` at 4096 chars (per https://core.telegram.org/bots/api). `TelegramService.send_message` splits longer text at paragraph → line → sentence → space → hard-cut boundaries via `_split_text` helper, sends each chunk threaded under the previous (`reply_to_message_id` cascade), returns first message's metadata + `parts` + `message_ids[]`. Captions on `send_photo` / `send_document` still take the legacy path and will fail on >1024 chars — separate fix once needed (constants `_TG_TEXT_LIMIT=3500`, `_TG_CAPTION_LIMIT=900` in `_service.py` already account for ~20% HTML expansion from markdown→HTML conversion in `_resolve_body`).
- **Google OAuth scope expansion**: Google's authorisation server legitimately returns a wider scope set than requested when the OAuth Client's "Data Access" page lists extra scopes (commonly `cloud-platform`) or when `include_granted_scopes` replays a previously-granted scope. `oauthlib` does strict set-equality and aborts with `Warning: Scope has changed`. As of 2026 (`google-auth-oauthlib` 1.2.4, `oauthlib` upstream issue #562 still open), no constructor flag, context manager, or `expected_scopes` argument exists — the documented relief is the env var. `services/google_oauth.py` sets `OAUTHLIB_RELAX_TOKEN_SCOPE=1` via `os.environ.setdefault` BEFORE the `google_auth_oauthlib.flow` import (oauthlib reads it once at parameters-module import; request-time setting races under uvicorn workers), paired with `warnings.filterwarnings(message=r"Scope has changed.*")` to keep the operator log clean. Long-term root cause: audit the Cloud Console Data Access page and remove `cloud-platform` if no handler uses it.
- **Code-executor error mapping** (`pythonExecutor` / `javascriptExecutor` / `typescriptExecutor`): wrap user-code execution and sidecar calls in `try/except`. Python: detect `ImportError("__import__ not found")` (the LLM tried `import X` against the sandboxed builtins) and surface the pre-injected names list (`math, json, datetime, timedelta, re, random, Counter, defaultdict`) plus the suggestion to use `process_manager` for unsupported modules; other exceptions get formatted as `<ErrorName> at line N: <message>` (line N walked from `<string>` frame in the traceback) plus any captured stdout. JS/TS: detect `aiohttp.ClientConnectorError` and surface "JavaScript executor not running on localhost:3020. Start the dev runner or fall back to python_executor." All raise `NodeUserError` so the framework logs one WARN line — no aiohttp/CreateProcessW noise in the operator log.
- **Workflow-scoped chat + console history**: chat messages persist with `chat_messages.session_id == <workflow_id>` (or `"default"` when no workflow is open); console logs persist with `console_logs.workflow_id`. Backend `database.get_console_logs(limit, workflow_id=None)` and `clear_console_logs(workflow_id=None)` filter by workflow when given. `handle_clear_console_logs` broadcasts `console_logs_cleared` carrying the `workflow_id` so the existing frontend filter in `WebSocketContext` keeps other workflows' panels intact. Frontend reads `currentWorkflow.id` via the documented Zustand escape hatch (`useAppStore.getState().currentWorkflow?.id`) at call time for `clearChatMessages` / `clearConsoleLogs` / `sendChatMessage` so the callbacks don't re-create on every workflow switch. A dedicated `useEffect([currentWorkflowId, isReady])` resets local `chatMessages` / `consoleLogs` and refetches both when the user opens / switches workflow. Incoming `console_log` broadcasts are filtered by `currentWorkflow.id` so a parallel run on another workflow never bleeds into the active panel. Legacy logs without `workflow_id` still surface (transition guard).
- **Auto-derived `isConfigNode` uiHint**: `_derive_auto_ui_hints(group)` in `services/plugin/base.py` automatically sets `uiHints.isConfigNode: True` on any plugin whose `group` tuple contains `memory` or `tool` (centralized as `_CONFIG_NODE_GROUPS = frozenset({"memory", "tool"})`). Explicit `cls.ui_hints` always wins (merge order: auto first, then `dict.update`). Frontend `InputSection.tsx` and `OutputPanel.tsx` consume the flag via `definition?.uiHints?.isConfigNode === true` — the old `groups.includes('memory') || groups.includes('tool')` heuristic is gone. Pytest invariant `test_ui_hints_only_carry_known_flags` locks the flag name. Adding a new auxiliary node type costs zero per-plugin code; opting out costs one line (`ui_hints = {"isConfigNode": False}`).
- **`isMasterSkillEditor` uiHint replaces `node.type === 'masterSkill'` checks**: 6 frontend callsites (`Dashboard.tsx:98` component dispatch, `useAutoSkillEdges.ts` constant + edge filter, `MiddleSection.tsx` × 3) now read `getCachedNodeSpec(type)?.uiHints?.isMasterSkillEditor === true` instead of comparing the type string. The `MasterSkillNode` plugin already declared the hint — no backend change. Renaming the plugin's `type` is now a single backend edit followed by a NodeSpec deploy; the frontend never needs to know the string.
- **`--action-X-hover` triplet + ActionButton zero-arithmetic**: each of the 6 action roles (`run`/`stop`/`save`/`config`/`secret`/`tools`) now exposes a `-hover` variant (0.25 alpha) alongside the existing `-soft` (0.15) and `-border` (0.6). ActionButton's CVA reads `hover:bg-action-X-hover` directly; disabled state is the shadcn-idiomatic `disabled:opacity-50` on the base class. No per-token `/25`, `/40`, `/10` opacity arithmetic at any call site. Credential-modal panels (`OAuthConnect`, `EmailPanel`, `QrPairingPanel`) and the skill / tool-schema editors (`SkillEditorModal`, `ToolSchemaEditor`) consume `<ActionButton intent="...">` directly. `ActionDef` carries an `intent` key; the catalogue adapter maps server-sent `theme_color` palette strings to intents via `SERVER_COLOR_TO_INTENT`.
- **Credentials: DB as single source of truth + symmetric broadcasts + cache dedup**: `CredentialsDatabase` (encrypted SQLite) is canonical; every other layer is a derived cache with explicit invalidation. Backend `AuthService._memory_cache + _models_cache` collapsed into one `_api_key_cache: Dict[str, ApiKeyCacheEntry]` dataclass — single write/evict site. Per RFC 9700 (OAuth 2.0 BCP, 2024) the `_oauth_cache` no longer carries `refresh_token`; new `get_oauth_refresh_token(provider, customer)` reads from the encrypted DB on every call. `validate_api_key`, `save_api_key`, `delete_api_key`, `twitter_logout`, `google_logout` now broadcast symmetrically: `update_api_key_status` (in-memory map) + `broadcast_credential_event(...)` (refetch signal) wrapping `WorkflowEvent` (CloudEvents v1.0 from `services/events/envelope.py`, the same envelope the Wave 12 EventSource framework uses). The dead-letter `credential_catalogue_updated` event is finally emitted by the backend. Frontend retired the 200-LOC `client/src/components/credentials/providers.tsx` static fallback — `useCatalogueQuery` is the only source; cold-boot renders `<Skeleton>`, server-unreachable shows an explicit error state. `ApiKeyStatus.hasKey` mirror dropped (duplicated catalogue's `provider.stored`); two new selector hooks (`useProviderStored`, `useStoredProviderCount`) read the catalogue. Pytest invariant `test_credential_broadcasts.py` (14 tests) locks the broadcast contract via `inspect.getsource` introspection + the CloudEvents v1.0 envelope shape + AuthService DB-write-then-cache-update ordering + the no-refresh-token-in-cache rule.
- **Local-LLM provider routing + per-model context**: Ollama and LM Studio are first-class providers (9 in total for agents now: cloud 7 + 2 local). Provider detection is driven by `detect_ai_provider` in `server/constants.py` plus the `provider` Literal in `nodes/agent/{ai_agent,chat_agent,_specialized}.py` — both MUST list `ollama` / `lmstudio` or chat-model nodes / agent dropdowns silently fall through to `'openai'` and `execute_chat` calls api.openai.com with the placeholder key. The validator at `nodes/model/_local_validator.py` probes via the official SDKs (`ollama.AsyncClient.ps()` for typed `ProcessResponse.Model`, `lmstudio.AsyncClient.llm.list_loaded()` for typed `LlmInstanceInfo`) — reads only typed fields (`context_length`, `max_context_length`, `vision`, `trained_for_tool_use`, `architecture`, `params_string`, `format`, plus Ollama's `details.{family,parameter_size,quantization_level}`). No regex, no Modelfile-parameters parsing, no `/api/show` modelinfo dict-key hunting. Per-model params persist in `EncryptedAPIKey.models["model_params"]` (via `save_api_key(model_params=...)`) AND in `model_registry.json` via `register_local_model()` — sync `get_context_length()` / `get_max_output_tokens()` find real values without async DB lookups, and entries survive process restart. `is_model_valid_for_provider` returns `True` for open-world providers (`openrouter` / `ollama` / `lmstudio`) so local model names like `qwen/qwen3.6-27b` aren't rejected by the cloud-style pattern check. Runtime path uses `OpenAIProvider(base_url={user_proxy_url}, api_key="ollama")` — traffic stays on `localhost`.
- **Typed openai exception → NodeUserError**: `execute_chat` / `execute_agent` / `execute_chat_agent` in `services/ai.py` each have **one** `except openai.OpenAIError as e` block that re-raises `NodeUserError(str(e)) from e`. The openai SDK's typed exception hierarchy — `BadRequestError` (400, e.g. `n_keep > n_ctx` overflow), `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404), `RateLimitError` (429), `APIConnectionError` (server unreachable), `APITimeoutError` — IS the user-correctable contract. `BaseNode.execute()` catches `NodeUserError` and logs at WARN with one line, no stacktrace. The plain `except Exception` block keeps the full-traceback path for real bugs. Zero string matching, zero flag plumbing, zero classification helpers. The `frontend toast` reads `response.message ?? response.error` so both clean envelopes (validator-style) and uncaught-error envelopes (handler-wrapper-style) surface a useful message.
- **Plugin extraction (Wave 11.I)**: every plugin's WS handlers, OAuth client, FastAPI router, and lifecycle service live entirely under `nodes/<plugin>/`. Eight plugin domains migrated this round (whatsapp / twitter / google-workspace / android / browser / email / code / credential-validation-scaffold for maps+apify+ollama+lmstudio) follow the telegram pattern. `routers/websocket.py` shrunk from ~3,785 to ~2,977 LOC (-808). Three plugin routers (`routers/twitter.py`, `routers/google.py`, `routers/android.py`) moved into `nodes/<plugin>/_router.py` and mount via the plugin-router loop in `main.py`; the explicit `app.include_router(<plugin>.router)` calls and `from routers import <plugin>` imports in `main.py` are gone. **`register_router(router, name=...)` is the new sibling helper to `register_ws_handlers` (same file: `services/ws_handler_registry.py`)** — six generic registries total now (ws_handlers, router, filter_builder, trigger_precheck, service_refresh, output_schema). **`Credential.validate(data) -> dict` + `Credential._probe(api_key) -> ProbeResult` is the new shared validator scaffold** in `services/plugin/credential.py` — replaces the per-router `_SPECIAL_PROVIDER_VALIDATORS` dict; one `_LLMApiKey._probe` method serves all 9 cloud LLM providers, dedicated `_probe` overrides handle Maps + Apify, `_LocalLLM.validate` overrides for Ollama / LM Studio's 2-storage edge case. **`tests/test_plugin_self_containment.py` locks the contract** with 7 invariant classes (forbidden-imports / no-router-outside-nodes / per-plugin self-registration / registry-API sanity / stale-paths-absent / main.py-does-not-mount / WS_HANDLERS-non-empty); same `inspect.getsource` introspection style as `test_credential_broadcasts.py`. Wire format unchanged — frontend WS message-type strings (`whatsapp_status`, `twitter_oauth_login`, `google_oauth_status`, `android_relay_connect`, etc.) and HTTP route paths (`/api/twitter/callback`, `/api/google/callback`, `/api/android/*`) are byte-identical post-migration.
- **Typed CloudEvents factory pattern for new broadcasts** (b6aecd3): every new server→FE broadcast follows the recipe locked by `broadcast_credential_event` and `broadcast_agent_progress`. (1) Add a typed classmethod on `WorkflowEvent` (`services/events/envelope.py`) — convention: `source = "machinaos://services/<area>"`, `type = "<area>.<event>"`, `subject = <primary entity id>`, `workflow_id` extension if scoped. (2) Add a `broadcaster.broadcast_<area>_<event>(...)` method on `StatusBroadcaster` that builds the envelope and emits via `broadcast({type: "<wire_key>", data: event.model_dump(mode="json")})`. Wire key is typically the event topic with dots → underscores (`agent.progress` → `agent_progress`). (3) FE handler in `WebSocketContext.tsx` switch routes the wire key, parses `data` as `WorkflowEvent<TInner>`, writes the inner payload to whichever store consumes it (`nodeStatusStore` for per-node state, etc.). (4) Add a parity test in `tests/test_status_broadcasts.py` via `inspect.getsource` introspection. **Forbidden**: emitting raw-dict payloads through `update_node_status` for any new event type — that channel is allowlisted via `_LEGACY_RAW_DICT_CALLSITES` and the allowlist is closed.
- **Operator-metadata fields in credential panels** (f746ddf): when a credential panel needs a non-credential follow-up field (e.g. Telegram bot token + owner chat id), declare it as a second entry in the provider's `fields` array in `server/config/credential_providers.json`. The first field stays the validate/connect target; subsequent fields render below as plain text inputs via `SecondaryFieldRow` in `ApiKeyPanel.tsx` (shadcn `Input` + `Label` + `<ActionButton intent="save">`). Each field carries an optional `help` slot (`FieldDef.help` / `ServerFieldDef.help`) for always-visible explanatory text under the input. Save writes via `panel.actions.save(field.key, value)` → `auth_service.store_api_key` (which is permissive about provider string). The credential class must declare the new key in `extra_fields` (`Credential` subclass attribute) so the storage layer accepts it. Reference: `telegram_owner_chat_id` field.
- **Telegram owner detection — three layers** (f746ddf): the bot owner is captured by THREE independent paths so the realistic setup flows all converge to a working state. (1) **Explicit field**: secondary `FieldDef` in the credentials modal lets the user paste their Telegram user ID directly (recommended; works without DM'ing the bot). (2) **Pre-poll peek** in `connect()` via `_capture_owner_from_pending_updates()` — calls `bot.get_updates(timeout=0)` BEFORE `start_polling(drop_pending_updates=True)` discards the queue, scans for any historical private DM, captures + persists atomically. (3) **Atomic write-through in `_on_message_received`** — persists FIRST, sets in-memory ONLY on success. Invariant: "in-memory has owner ⇒ DB has owner" so a process restart can re-capture cleanly. Failure logged at `ERROR` with `exc_info=True` (was `WARNING` previously, masking persist failures). The lazy fallback in `telegram_send.py` (read DB → `service.set_owner`) sits on top of all three.
- **Supervisor Job Object loud-failure (machina/tree.py + supervisor.py)** (8a64eb9): the Windows process-tree mechanism depends entirely on a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (no fallback — `start_new_session` is POSIX-only). `pywin32>=308` is a hard supervisor dependency on Windows; the declaration in `machina/pyproject.toml` carries an inline comment so future maintainers don't drop it. `_JobObject.__init__` splits failures into explicit `ImportError` (with reinstall command) and `Exception` (with `repr`) branches — both write to `stderr` instead of swallowing. `_JobObject.add()` verifies enrollment via `win32job.IsProcessInJob(handle, self._handle)` after assignment; on mismatch it queries `IsProcessInJob(handle, None)` so the warning explicitly names the wrapper-job hypothesis (npm / pnpm / conhost wrappers occasionally place us in their own non-nesting Job). `supervisor._spawn_once` checks `add_to_job` return value and emits a yellow `WARN: pid=N not enrolled in Job Object` per child if False. Without these guards, a stale pywin32 install or a wrapping Job Object silently leaks orphan Python processes on every Ctrl-C and they accumulate to the point of holding SQLite locks that block subsequent backend startup.
- **Credentials envelope-shape invariant** (dc94cde): the `useCredentialPanel` query stores `{values: CredentialFormValues, hadStored: boolean}` (an envelope, NOT a flat dict). The earlier `writeValues` called `setQueryData<CredentialFormValues>` with a spread `(prev) => ({...prev, [key]: value})`, which at runtime merged the typed character at the envelope level next to `values` and `hadStored` instead of inside `.values` — the input selector `panel.values[field.key]` re-rendered with the original (server) value on every keystroke and the input felt frozen. Fix: `writeValues` now preserves the envelope shape and updates only the inner `values` dict; `hadStored` is preserved verbatim (it reflects real server state, not local edits). When introducing other queries shaped as `{data, meta}` envelopes, follow the same pattern — never spread `prev` as if it were the inner payload.
- never use emojis in prints