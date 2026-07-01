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
| **[Theme System](./docs-internal/theme_system.md)** | 10-way visual theme system — 5 utopian (light, dark, renaissance, greek, edo, steampunk, atomic) + 5 dystopian (cyber, wasteland, rot, plague, surveillance) — driven by `<html data-theme>` + per-theme CSS files in `client/src/themes/`. Token taxonomy (surface / fg / border / accent / typography / motion) in **hex + `color-mix()`**; `@theme inline` bridge maps `--color-X: var(--X)` (no `hsl()` wrapper); per-theme files own the colour hex, base.css owns the shared `--tint-*` alpha scale; action role tokens (incl. `-ink` readable text), **per-theme `--code-*` syntax tokens** (tier 6 — the code editor, console/output JSON viewers, and chat code blocks paint in each theme's own palette: keyword→trigger, string→success, number→agent, function→model, on an adaptive dark/light code surface; replaced the global dracula `--prism-*` block + dead `getPrismTokenCSS`; the `OutputPanel` JSON viewer reads the same vars), per-theme `--pulse-keyframe` animation system + `.machina-*` helpers in `animations.css`, decorative-layer wrappers (`.app-frame` / `.canvas-host` / `.modal-frame`), per-component decorative ornaments (panel textures + canvas decorations + node pseudo-element overlays + theme-specific keyframes), **canvas-node visual contract via `--node-color` CSS custom property** (no inline `background` / `border` on node components — base.css + per-theme CSS owns visuals; `NodeStyle` helper type at [types/NodeTypes.ts](./client/src/types/NodeTypes.ts) makes the inline custom-prop typecheck-clean), **`--node-pulse-color` separate from `--node-color`** so executing-node glow uses each theme's highest-contrast accent regardless of plugin accent (Cyber neon cyan, Surveillance REC red, Renaissance ultramarine, etc.), **`data-page-hidden` animation pause** (toggled by Dashboard's `visibilitychange` listener; base.css declares `html[data-page-hidden] *, *::before, *::after { animation-play-state: paused !important }` to prevent compositor stall on tab return), **per-theme icon glyph system** (290 SVGs across 29 keys × 10 themes via [themedGlyphs.ts](./client/src/assets/icons/themedGlyphs.ts) + theme-aware [NodeIcon.tsx](./client/src/assets/icons/NodeIcon.tsx)), **per-theme canvas-grid + custom cursors** via `--canvas-grid` / `--cursor-default` slots, **decorative HTML primitives** (`<SvgFilterDefs>` mounting `#ink-blot` / `#noise` / `#crt` filter IDs at app root, `<DropCap>` wrapper for Renaissance ornament rule), **parameter panel migrated to Tailwind tokens** (no `useAppTheme()` reads; section headers carry the display-typography triplet; raw `<Button>` swapped to `<ActionButton intent>`), **per-theme scrollbar webkit rules** in all 10 themes, 9-event WebAudio sound system (10 packs via `--sound-pack` token + `useSound()` hook + global hover delegate + sonner toast monkey-patch + `withSound()` HOC + `Sounds.unlock()` gesture-unlock for AudioContext autoplay-policy compliance), `@media (prefers-reduced-motion: reduce)` accessibility, 30 ms throttle on `type` / `hover`, migration recipe, anti-patterns. Read this before adding a new theme, migrating a component to the new contract, or adding a canvas-node component. |
| **[Design System Bundle](./docs-internal/design-system/IMPLEMENTATION.md)** | Canonical, vendored design-system reference (extracted from this repo; see its `IMPLEMENTATION.md` for the contract). Tokens are **hex + `color-mix()`** (`tokens/{colors,typography,spacing,motion,fonts,base}.css`) — the format the live codebase standardizes on. Includes 34 reference components (`components/`, inline-style reference only — recreate in Tailwind/shadcn idioms), full-app UI kit (`ui_kits/machinaos/index.html`), and 12-theme docs (`guidelines/THEMES.md`, `reference/themes/`). Copy token values verbatim; do not re-derive by eye. Pairs with [Theme System](./docs-internal/theme_system.md). |
| **[Schema Source of Truth RFC](./docs-internal/schema_source_of_truth_rfc.md)** | Backend is SSOT for node schemas, visual metadata, handlers, palette metadata, icons. Plugin pattern: one `BaseNode` subclass in `server/nodes/<group>/<plugin>/__init__.py`. Wire format: `asset:<key>` / `<lib>:<brand>` / URL / emoji. Endpoint: `/api/schemas/nodes/{type}/spec.json`. Live invariant total via `pytest --collect-only`. |
| **[Plugin System (Wave 11)](./docs-internal/plugin_system.md)** | Class-based plugin-first architecture. `BaseNode` / `ActionNode` / `TriggerNode` / `ToolNode` + `@Operation` decorator. Pydantic `Params`/`Output`. Declarative `Routing` DSL + `Connection` facade (Nango pattern). 18 `Credential` subclasses live in each node folder's `_credentials.py` (or inline for single-use). `TaskQueue` constants route to Temporal worker pools. Plugins live across 9 queues (live count via `glob server/nodes/**/__init__.py`); handler bodies fully inlined (`services/handlers/` shrank 12.8K → 1.1K LOC across 16 → 4 files; only cross-cutting orchestration remains: `tools.py` AI-tool dispatch + agent delegation, `google_auth.py`, `triggers.py`). **Wave 11.H added "self-contained plugin folders"** — up to six generic registries (`ws_handler_registry`, `register_router`, `event_waiter.{register_filter_builder,register_trigger_precheck}`, `status_broadcaster.register_service_refresh`, `node_output_schemas.register_output_schema`) so rich plugins like telegram own their entire surface area without core-services edits. |
| **[Nodes Cookbook](./server/nodes/README.md)** | 5-minute recipe + folder map + shared helpers (`_base.py` / `_inline.py` per domain) + shared credentials + **canonical folder-per-plugin shape (telegram is the reference implementation)** + contract invariants + common pitfalls. Lives next to the plugin files. |
| **[Node Creation Guide](./docs-internal/node_creation.md)** | Canonical plugin recipe — one self-contained folder per plugin under `server/nodes/<group>/<plugin>/`, rooted at `__init__.py`. Multi-file split (`_service.py` / `_handlers.py` / etc.) when the plugin owns long-lived state. Zero frontend edits, zero core-services edits. Auto-registers via `BaseNode.__init_subclass__` + the six `register_*` hooks. Covers tool nodes, dual-purpose nodes (workflow + AI tool), and specialized agents as variations of the same recipe. |
| **[Agent Architecture](./docs-internal/agent_architecture.md)** | How AI Agent and Chat Agent discover skills/tools, inject them into LLM prompts, and execute via the plain-async `_run_agent_loop` |
| **[Agent Delegation](./docs-internal/agent_delegation.md)** | How memory, parameters, and execution context flow when one AI agent delegates work to another agent connected as a tool |
| **[Agent Teams](./docs-internal/agent_teams.md)** | Claude SDK Agent Teams pattern - AI Employee and Orchestrator nodes with input-teammates handle for multi-agent coordination |
| **[Memory Compaction](./docs-internal/memory_compaction.md)** | Token tracking and model-aware memory compaction using native provider APIs (Anthropic, OpenAI). Threshold = `Settings.compaction_ratio` (env `COMPACTION_RATIO`, default 0.8) × model context_length; per-user UserSettings override exposed in Settings tab. |
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
| **[Claude Code Agent](./docs-internal/claude_code_agent.md)** | Hub for the Claude Code agent — routes to architecture, interactive mode, CLI framework, canonical-patterns RFC, and the 5 CLI snapshot references. |
| **[CLI Agent Framework](./docs-internal/cli_agent_framework.md)** | Multi-provider CLI runtime (Claude Code / Codex / Gemini): `AICliService.run_batch`, per-task worktree isolation, FastMCP bridge, **memory bridge** (`--continue` for first run + intra-process stream-json multi-turn + `--resume <UUID>` for crash recovery, all on a stable `cwd=repo_root`; `node_parameters_updated` broadcast on every successful turn). **Plugin-folder layout** (post-cutover): all claude-specific code lives in [`server/nodes/agent/claude_code_agent/`](./server/nodes/agent/claude_code_agent/) — `_provider.py`, `_pool.py`, `_skills.py`, `_oauth.py`, `_handlers.py` — and self-registers via three `factory.py` registries (`register_provider` / `register_session_pool` / `register_skill_materialiser`) plus `register_ws_handlers`. The generic framework at `services/cli_agent/` imports nothing from `nodes/`. |
| **[Claude Code Interactive Mode](./docs-internal/claude_code_interactive_mode.md)** | The interactive-mode cutover: MachinaOs no longer uses `claude -p` headless. `ClaudeSessionPool` (keyed by `simpleMemory.node_id`) spawns `claude` as a plain subprocess with stdio pipes — **no PTY** — and drives it over the VSCode-extension protocol: `--output-format stream-json --input-format stream-json --verbose --ide`. User prompts written as JSON to `proc.stdin` (`{"type":"user","message":{"role":"user","content":"..."}}\n`); `system/init` / `assistant` / `result` / `system/compact_boundary` events stream back on `proc.stdout` (parsed by a background `stdout_reader_task` — the on-disk JSONL is persistence-only, not the runtime contract, because `result` events are stdout-only in stream-json mode). Cross-platform via plain pipes (the earlier PTY pattern was broken on Windows because pywinpty/ConPTY's emulated stdin never reached claude's Ink TUI keystroke handler). Stays in interactive billing — entrypoint `claude-vscode`, NOT `sdk-cli`. Multi-turn within one warm subprocess preserves the session UUID (verified end-to-end); cross-batch continuity via `--continue` (first run) or `--resume <UUID>` (crash recovery). Four typed CloudEvents (`claude.session.{spawned,cleared,terminated,usage}`) fire from the pool. `/compact` events forward to `CompactionService`. The non-pooled `AICliSession` path (one-shot prompt-in-argv runs) still uses PTY (out of scope for this refactor; works on POSIX). |
| **[CLI Agent Canonical Patterns RFC](./docs-internal/cli_agent_canonical_patterns_rfc.md)** | Audit of MachinaOs's `services/cli_agent` against the official Claude Code spec — six invariants (skills-as-files, MCP-only transport, `list_changed`, deferral, visible-tool filtering, native-session-continuity-via-stable-cwd) with current implementation status. |
| **[Claude Code CLI Reference (snapshot)](./docs-internal/claude_code_cli_reference.md)** | Verbatim snapshot of [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference) — every CLI subcommand + flag + system-prompt-flag matrix + the subset we emit from `nodes/agent/claude_code_agent/_provider.py`. Fetched 2026-05-11. |
| **[Claude Code Env Vars (snapshot)](./docs-internal/claude_code_env_vars_reference.md)** | Categorised [code.claude.com/docs/en/env-vars](https://code.claude.com/docs/en/env-vars) snapshot — auth, Bedrock/Vertex, model, bash, MCP, telemetry, session/debug, paths. Documents `CLAUDE_CONFIG_DIR`, `MAX_MCP_OUTPUT_TOKENS`, `ENABLE_TOOL_SEARCH` which we touch directly. |
| **[Claude Code Permission Modes (snapshot)](./docs-internal/claude_code_permission_modes_reference.md)** | Verbatim [code.claude.com/docs/en/permission-modes](https://code.claude.com/docs/en/permission-modes) — `default` / `acceptEdits` / `plan` / `auto` / `dontAsk` / `bypassPermissions` semantics, Shift+Tab cycle, protected paths. MachinaOs default is `acceptEdits`. |
| **[Claude Code Headless / Print Mode (snapshot)](./docs-internal/claude_code_headless_reference.md)** | Verbatim [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless) — `claude -p`, `--output-format` (`text` / `json` / `stream-json`), `--input-format`, `--bare`, stream-json event schema (`system/init`, `system/api_retry`, `system/plugin_install`). MachinaOs no longer uses `-p`; the pool path emits `--output-format stream-json --input-format stream-json --verbose --ide` over stdio pipes (interactive billing). The event schema is the contract `nodes/agent/claude_code_agent/_pool.py` parses off `proc.stdout`. |
| **[Claude Code Skills (snapshot)](./docs-internal/claude_code_skills_reference.md)** | Verbatim [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills) — `SKILL.md` frontmatter spec, discovery paths, content lifecycle, `context: fork`, dynamic context injection via `` !`<command>` ``. The spec MachinaOs materialises connected skills against in `_pre_spawn`. |
| **[Autonomous Agent Creation](./docs-internal/autonomous_agent_creation.md)** | Creating autonomous agents with Code Mode patterns and agentic loops |
| **[Polyglot Server](../polyglot-server/ARCHITECTURE.md)** | Plugin registry microservice with MCP gateway (optional integration) |
| **[Authentication](./docs-internal/authentication.md)** | JWT/cookie auth: toggle, single/multi mode, backend + frontend, middleware, startup retry. |
| **[Credentials Panel](./docs-internal/credentials_panel.md)** | CredentialsModal.tsx logic-flow reference (state taxonomy, per-provider panels, invariants). |
| **[Memory Lifecycle](./docs-internal/memory_lifecycle.md)** | Canonical conversation-memory lifecycle: load/append/trim/archive/clear/resume across all agents. |
| **[Node Parameter Panel](./docs-internal/node_panels.md)** | Three-section node config UI (Input / Parameters / Output) logic-flow reference. |
| **[Deployment (legacy reference)](./docs-internal/deployment_legacy.md)** | machina deploy CLI summary + the historical Docker Compose topology. |
| **[GCP VM Deploy Runbook](./docs-internal/gcp_vm_deploy_runbook.md)** | Manual gcloud + Cloudflare runbook for deploying the released npm package (non-Terraform path). |

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
│                            # (Claude Code CLI wrapper + isolated OAuth moved to
│                            #  nodes/agent/claude_code_agent/_oauth.py — CLAUDE_CONFIG_DIR=<DATA_DIR>/claude/)
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
├── paths.py                 # SSOT for on-disk locations (generic helpers only): machina_root()/data_path()/workspaces_dir()/workspace_dir()/daemons_dir() + packages_dir()/package_dir(name) all under ~/.machina/ (= DATA_DIR); packages/ holds the single shared npm tree + stripe/ + temporal/ binary subdirs; example_workflows_dir() = <repo>/.machina/workflows/ (shipped seeds, NOT under DATA_DIR). Plugin-specific subpaths composed inline at the call site, never added here.
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
MachinaOs can optionally integrate with the sibling **polyglot-server** repo (a plugin-registry microservice exposing REST + MCP + WebSocket). NOTE: the MachinaOs-side client/handler (`polyglot_client.py`, `handlers/polyglot.py`) are not currently present in the tree — this is a possible future integration, not wired. See [Polyglot Server](../polyglot-server/ARCHITECTURE.md).

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
(`cli/colors.py`) prepends `[HH:MM:SS.fff]` to every aggregated
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

**Output contract enforcement** (`BaseNode._serialize_result` in
`services/plugin/base.py`): the declared `Output` Pydantic model is
enforced at the serialization boundary, FastAPI-`response_model` style.
Dict results validate via `Output.model_validate(...).model_dump(mode="json",
exclude_unset=True)`; `BaseModel` results dump `mode="json"`; violations
produce an `error_type="OutputValidationError"` envelope at the producer.
Rules: prefer returning the `Output` instance; never put raw third-party
objects (SDK results, dataclasses) or pre-stringified JSON into result
dicts — return plain lists/dicts; Params fields that may receive
LLM-stringified JSON args coerce with `field_validator(mode="before")`
(canonical: `AndroidServiceParams._coerce_parameters`,
`WriteTodosParams._coerce_todos`). Below the plugin layer, the SQLAlchemy
engine sets `json_serializer` backed by `pydantic_core.to_jsonable_python`
(`core/database.py`) so every JSON column tolerates dataclasses /
datetimes / enums / sets. Full spec:
[docs-internal/plugin_system.md → Output contract enforcement](./docs-internal/plugin_system.md);
locked by `server/tests/test_output_contract.py`.

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
- `src/components/ui/CodeEditor.tsx` - Syntax-highlighted code editor (react-simple-code-editor + prismjs). Token colours come from the per-theme `--code-*` tokens (see [Theme System](./docs-internal/theme_system.md) tier 6) — the code editor, console/output JSON viewers, and chat code blocks all paint in the active theme's syntax palette, not a global dracula scheme. (Retired the old `--prism-*` block + dead `getPrismTokenCSS()`.)

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

10-way visual theme system (5 utopian: light, dark, renaissance, greek, edo, steampunk, atomic + 5 dystopian: cyber, wasteland, rot, plague, surveillance) driven by `<html data-theme>` (set by [ThemeContext.tsx](./client/src/contexts/ThemeContext.tsx), which also toggles `.dark` for DARK_FAMILY themes) + per-theme CSS in `client/src/themes/`. Token VALUES are hex + `color-mix()` (never HSL): per-theme files own shadcn/dracula/node/action hex, `base.css` owns the shared `--tint-*` scale, `index.css` is plumbing (`@theme inline` maps `--color-X: var(--X)`). Six token tiers, the per-theme `--pulse-keyframe` animation system, decorative-layer wrappers, the 10-pack WebAudio sound system, and the canvas-wide edge/node status rules (`canvasAnimations.ts`) are all documented in **[Theme System](./docs-internal/theme_system.md)** — read it before adding a theme or a canvas-node component. The strict frontend theme RULES remain normative under "Frontend Design + Theme System (strict)" above.

### WebSocket-First Architecture
The project uses WebSocket as the primary communication method between frontend and backend, replacing most REST API calls:
- `src/contexts/WebSocketContext.tsx` - Central WebSocket context with request/response pattern
- `server/routers/websocket.py` - WebSocket endpoint; the live handler set is the `MESSAGE_HANDLERS` dict plus plugin-registered handlers via `services.ws_handler_registry`. Don't hand-maintain a count here.
- `server/services/status_broadcaster.py` - Connection management and broadcasting

**Canvas mutations from the backend** -- any handler that needs to add / move / delete nodes or edges (auto-add-skill on tool connect, Agent Builder runtime tools called by the LLM mid-execution, future workflow-template features) returns a workflow-ops batch (`{operations: [...]}`) and the frontend applies it through `applyOperations` in [client/src/lib/workflowOps.ts](./client/src/lib/workflowOps.ts). Backend builders live in [server/services/workflow_ops.py](./server/services/workflow_ops.py). Two delivery modes: request/response (frontend-driven, e.g. auto-skill) and push broadcast (`send_custom_event('workflow_ops_apply', ...)`, picked up by `useWorkflowOpsListener`). Full spec: [docs-internal/workflow_ops_protocol.md](./docs-internal/workflow_ops_protocol.md).

## Implemented Node Types

> **Authoritative source: backend plugin registry.** Glob [`server/nodes/**/*.py`](./server/nodes/) (excluding `_*.py` helpers and `__init__.py`) for the live count. The per-node descriptions below are reference material — they drift on every plugin add and should be cross-checked against [`server/nodes/README.md`](./server/nodes/README.md), the per-domain docs in `docs-internal/`, and the actual plugin classes before relying on any specific detail.

### Node Catalogue (collapsed)

> The authoritative, per-node reference is the backend plugin registry plus the per-node "logic-flow" cards under [docs-internal/node-logic-flows/](./docs-internal/node-logic-flows/) (one card per node, grouped by category, with handles / params / outputs / side-effects / edge-cases). Live node list = glob `server/nodes/**/__init__.py` (~138 today); live total via `pytest --collect-only`. Do NOT maintain a per-node catalogue here — it drifts on every plugin add.

Node groups (palette categories): agent, model, skill, tool, trigger, workflow, search, google, android, whatsapp, telegram, twitter, social, email, proxy, chat, scheduler, text, code, document, location, utility, browser, scraper, filesystem, stripe. See [docs-internal/node-logic-flows/](./docs-internal/node-logic-flows/) for the card index.

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
- Node execution dispatches every registered plugin via the `BaseNode` registry (authoritative count = glob `server/nodes/**/__init__.py`, ~138 node files today; live total via `pytest --collect-only`)
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

Workflows execute via Temporal for durability and horizontal scaling, gated by `TEMPORAL_ENABLED`. Three dispatch paths exist — legacy single `execute_node_activity` (default), per-type `node.{type}.v{version}` activities (F4.A, `TEMPORAL_PER_TYPE_DISPATCH=true`), and Agent-as-child-workflow (F4.B, `TEMPORAL_AGENT_WORKFLOW_ENABLED=true`) — with `rlm_agent`/`claude_code_agent` always bypassing AgentWorkflow. Execution routing falls back Temporal → Redis-parallel → sequential; the Temporal dev server + embedded worker are managed in-process under `server/services/temporal/` (pooch-downloaded CLI, single `temporal server start-dev` process, gRPC 7233 + Web UI 8080, SQLite at `~/.machina/temporal.db`). Full architecture, dispatch matrix, per-node + agent-loop lifecycle, the 7 F4.B `agent.*.v1` activities, heartbeat semantics, delegation input contract, resumption toggle (`TEMPORAL_TERMINATE_RUNNING_ON_STARTUP`), and all `.env` tunables live in [docs-internal/TEMPORAL_ARCHITECTURE.md](./docs-internal/TEMPORAL_ARCHITECTURE.md).

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
✅ **Theme System**: Neutral-slate (dark) + grey-blue paper (light) surfaces with Dracula accent palette, dark mode support, vibrant action buttons, and themed React Flow edges
✅ **Modular Backend Architecture**: workflow.py refactored from 2068 to 460 lines using facade pattern with NodeExecutor, ParameterResolver, and DeploymentManager modules
✅ **Node Rename System**: n8n-style node renaming via F2 keyboard shortcut, double-click on label, or right-click context menu with inline editing
✅ **UI State Persistence**: localStorage persistence for sidebar visibility, component palette visibility, dev mode, and collapsed sections
✅ **Normal/Dev Mode**: Toggle in toolbar to filter Component Palette - Normal mode shows only AI Agents, Models, and Skills; Dev mode shows all categories
✅ **Production Deployment**: Docker Compose deployment (4 containers: Redis, Backend, Frontend, WhatsApp), nginx reverse proxy, and Let's Encrypt SSL
✅ **Authentication System**: n8n-style JWT authentication with HttpOnly cookies, single-owner and multi-user modes
✅ **Cache System**: n8n-pattern cache with Redis (production) / SQLite (local dev) / Memory fallback hierarchy
✅ **AI Thinking/Reasoning**: Extended thinking for Claude, Gemini 2.5/3, OpenAI GPT-5/o-series, Groq Qwen3 with output available in Input Data & Variables for downstream nodes
✅ **Onboarding Service**: 5-step welcome wizard with shadcn UI, database persistence, skip/resume/replay support
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

**Directory**: `~/.machina/workspaces/<workflow_slug>/` (Wave 14 — keyed by the human-readable slug, not the UUID; see "Workflow naming" below).

**Configuration** (`server/core/config.py`):
```python
workspace_base_dir: str = Field(default="workspaces", env="WORKSPACE_BASE_DIR")  # resolved under DATA_DIR -> ~/.machina/workspaces/
```

**How it works:**
- `workflow.py` creates the workspace dir and injects `workspace_dir` into the execution context. The dir name is the `workflow_slug` resolved from the DB (falls back to `"default"` for one-off Runs without a saved row).
- `fileDownloader` saves to `{workspace_dir}/downloads/` by default
- Code executors (Python/JS/TS) receive `workspace_dir` in their execution namespace
- Deep Agent uses `FilesystemBackend(root_dir=workspace_dir, virtual_mode=True)` -- its filesystem tools (`read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`) operate within the workspace
- `virtual_mode=True` sandboxes paths to prevent traversal outside workspace
- Rename follows the workflow: when the user renames a workflow, `save_workflow` recomputes the slug and `os.rename`s the workspace dir to match (existing files preserved).

**Key Files:**
| File | Description |
|------|-------------|
| `server/core/config.py` | `workspace_base_dir` setting |
| `server/services/workflow.py` | `_get_workspace_dir()`, injects into context |
| `server/nodes/document/file_downloader/` | `fileDownloader` saves to the workspace |
| `server/nodes/code/` | `workspace_dir` available in Python/JS/TS executors |
| `server/nodes/filesystem/_backend.py` | `NushellBackend(root_dir=workspace_dir, virtual_mode=True)` for the file/shell plugins |

### Workflow Naming (Wave 14)
The workflow record carries three identity fields with strict separation:

| Field | Carrier | Stable? | Surfaces |
|---|---|---|---|
| `Workflow.id` | opaque 32-hex UUID (`uuid.uuid4().hex`) | yes — never changes on rename | FK target (`Execution.workflow_id`), `EventWorkflowId` Search Attribute in Temporal Visibility, `WorkflowEvent.workflow_id` CloudEvents extension, `log_context(workflow_id=...)`, Redis cache keys, `DeploymentManager._deployments` dict key, frontend `useAppStore.currentWorkflow.id` |
| `Workflow.name` | free-form display ("AI Assistant") | mutable | sidebar, parameter panel, exported JSON |
| `Workflow.slug` | `<Sanitized_Name>_<N>` (`AI_Assistant_1`) | mutable, recomputed on rename | `~/.machina/workspaces/<slug>/`, Temporal workflow IDs (visible in Temporal Web UI), cron Schedule IDs, export filenames |

Single source of truth: [`server/services/workflow_naming.py`](./server/services/workflow_naming.py) — `slugify_name` (via `python-slugify` for Unicode transliteration, emoji strip, case preservation, length cap), `next_available_slug(name, database, *, exclude_id=None)` (fill-gap counter; pass `exclude_id=workflow_id` on rename so the row doesn't bump itself), `new_workflow_id()` (bare hex UUID), `node_label_slug(node)` (sandbox-safe stdlib slug from `node.data.label` or `node.type`, used inside Temporal `@workflow.defn` modules where `python-slugify` can't import safely).

**Temporal workflow ID convention** — uniform `<workflow_slug>-<node_label>` shape across every workflow type. The Temporal Web UI's "Workflow Type" column already distinguishes the kind (TriggerListenerWorkflow / PollingTriggerWorkflow / CronTriggerWorkflow / AgentWorkflow / MachinaWorkflow), so no middle `-trigger-` / `-agent-` tag in the id.

| Surface | Format | Example |
|---|---|---|
| Trigger listener (push/poll) | `<slug>-<trigger_label>` | `AI_Assistant_1-chatTrigger` (or `AI_Assistant_1-Customer_Inbox` after F2 rename) |
| Per-firing run (child of listener) | `<slug>-<trigger_label>-<event_id>` | `AI_Assistant_1-chatTrigger-evt-abc` |
| Cron Schedule | `<slug>-<trigger_label>` | `AI_Assistant_1-cronScheduler` |
| Cron firing (per-tick child) | `<slug>-<trigger_label>-<ScheduledStartTime>` | `AI_Assistant_1-cronScheduler-2026-05-27T12:00:00Z` |
| Agent child workflow | `<slug>-<agent_label>` | `AI_Assistant_1-aiAgent` (or `AI_Assistant_1-Bot` after F2 rename) |
| Direct MachinaWorkflow exec | `<slug>-<uuid8>` | `AI_Assistant_1-a1b2c3d4` |
| Per-node activity (inside MachinaWorkflow) | `activity_id = <node_id>` | `chatTrigger-1779...-47c2f5` |

`node_label` is `node.data.label` (the F2-renamed canvas label) when set, falling back to `node.type` (`chatTrigger` / `telegramReceive` / `aiAgent` / etc.). Computed once at deploy time via `node_label_slug(node)` and passed into Temporal workflows as `trigger_label` (in `listener_data` for the trigger path) or read directly from the node dict (in `MachinaWorkflow.run` for the agent path). Stable `TriggerNodeId` / `EventWorkflowId` Search Attributes still use the immutable node_id / UUID so admin queries don't break across rename.

**Rename path** — there is NO dedicated rename endpoint. The frontend's auto-save chain (`TopToolbar` inline edit → `updateWorkflow({name})` → debounced save → REST `POST /api/database/workflows` → `services.workflow_storage.handlers.handle_save_workflow`) IS the rename path. When `name` changes between saves, the handler (1) allocates a fresh slug via `next_available_slug`, (2) `database.rename_workflow` updates name + slug atomically (id UUID stays put), (3) renames the on-disk workspace dir via `Path.rename()`, (4) broadcasts a CloudEvents `workflow.renamed` envelope (`broadcaster.broadcast_workflow_lifecycle("renamed", workflow_id=..., name=..., slug=..., old_slug=...)`) so other tabs invalidate their workflows query.

**Invariants** (locked by `tests/services/test_workflow_naming.py` + `test_workflow_rename.py` — 42 tests):
- First creation always gets `_1` suffix (no bare-base slugs).
- Fill-gap: deleted `AI_Assistant_2` slot is reused on next "AI Assistant" creation.
- Renaming `AI Assistant` → `AI Assistant!` (same slug base) keeps `_1` via `exclude_id` (no self-bump).
- UNIQUE constraint on `slug` is the final collision guard; `IntegrityError` indicates a race the caller should retry.
- Non-ASCII names transliterate via `text-unidecode` ("日本語" → "Ri_Ben_Yu"); fall back to `Workflow_N` only when slug is empty after sanitize.
- No backfill migration — the slug column is required on every save. Existing DBs must be rebuilt.

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

Chat-model nodes (`openaiChatModel`, `anthropicChatModel`, ...) render through `SquareNode` from the backend NodeSpec. Dual-path execution: native SDK (`ChatUnifier` in `services/llm/`) for chat, LangChain (`_run_agent_loop`) for agents. 11 providers are wired for agents (see the provider list above); model params (max output, context length, thinking type, temperature range) come from `ModelRegistryService`. The 5-layer architecture, the native-vs-LangChain split, proxy/local-LLM (Ollama, LM Studio) routing, the per-provider model + thinking/reasoning matrix (budget / effort / format), and `extract_thinking_from_response` all live in **[Native LLM SDK](./docs-internal/native_llm_sdk.md)**. The runtime output schema (`thinking` field for downstream nodes) is backend-served per [Schema Source of Truth RFC](./docs-internal/schema_source_of_truth_rfc.md).

## AI Agent Node Architecture

AI Agent (`aiAgent`) and Chat Agent / Zeenie (`chatAgent`) run the same plain-async `_run_agent_loop` (`server/services/ai.py`) and support memory / skills / tools / task input; differences are the backend method (`execute_agent()` vs `execute_chat_agent()`) and error-handling softness. The loop routes purely on `response.tool_calls`, hot-rebinds the tool surface mid-run after canvas mutations, and treats compaction (token-based, post-turn) as the real termination signal. Connection collection is `collect_agent_connections` in `server/services/plugin/edge_walker.py` (5-tuple: memory, skill, tool, input, task); the pre-Wave-11 `handle_ai_agent`/`handle_chat_agent` handlers are gone (dispatch is per-plugin `execute_op` under `server/nodes/agent/<plugin>/__init__.py`).

**`max_iterations` precedence (highest->lowest): per-node `parameters.max_iterations` > `UserSettings.agent_recursion_limit` > env `AGENT_RECURSION_LIMIT` (default 200) > `llm_defaults.json:agent.recursion_limit`.**

Full reference — agent loop, skill injection, tool building, input/auto-prompt fallback (message>text>content>str), handle topology, spec-driven `AIAgentNode`, async delegation (F4.B child-workflow + legacy fire-and-forget), specialized-agent routing — in [docs-internal/agent_architecture.md](./docs-internal/agent_architecture.md); delegation deep-dive in [agent_delegation.md](./docs-internal/agent_delegation.md), team leads in [agent_teams.md](./docs-internal/agent_teams.md).

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

### Self-Deploy CLI (`machina deploy`) — current path
One command provisions a login-gated MachinaOs VM on a cloud provider. Two stages: the
operator's **cloud CLI** (gcloud; aws planned) handles auth + project/region/zone resolution +
ADC verification + API enablement, then **Terraform** (`cli/terraform/gcp/`) owns all resources —
VM (always named `machinaos`), firewall, artifact bucket (local `npm pack` source), service
account, and a cloud-init startup script that installs Node 22 + uv + the package and runs
`machina serve` under systemd. Login gate = built-in auth (`VITE_AUTH_ENABLED=true`,
`AUTH_MODE=single`) with the owner credential generated at deploy time and seeded on first boot.

```bash
machina deploy up --provider gcp --owner-email you@example.com   # provision + install + print URL/creds
machina deploy status                                            # URL + /health
machina deploy destroy                                           # terraform destroy + clear state
```

Key files: `cli/commands/serve.py` (single-port runtime: uvicorn fronts API + WS + built SPA, plus
the node sidecar), `cli/commands/deploy/` (verbs, secrets, Terraform driver, provider CLI adapters),
`cli/terraform/gcp/` (HCL module + `startup.sh.tftpl`). Deployment state lives at
`<user-data>/deploy/machinaos/` (preserved by `machina clean` — see `_MACHINA_KEEP`); only
`machina deploy destroy` removes it. The deploy code is fully delinked from `machina build` /
`machina clean` (lazy verb stubs in `cli/cli.py`; nothing in the build pipeline imports it).

The legacy `deploy.sh` (docker-compose images over SCP to a GCE box) was removed. The Docker
Compose notes below are retained for reference for the historical container topology.

### Docker Deployment (legacy reference)

The historical Docker Compose topology (4-container stack: redis / backend / frontend / whatsapp, nginx reverse proxy, dev + prod compose files, env config, resource usage, useful commands) and the `npm run build` / `npm run preview` local-build commands are preserved in **[Deployment (legacy reference)](./docs-internal/deployment_legacy.md)**.

## Authentication System

n8n-style JWT auth in HttpOnly cookies. `VITE_AUTH_ENABLED=false` bypasses login (anonymous owner, dev); when enabled, `AUTH_MODE=single` (first user = owner, registration then closed) or `multi` (open registration). Backend: `User` (bcrypt) + `UserAuthService` + `/api/auth/*` router + `AuthMiddleware` (public-path allowlist + `/webhook/` prefix). Frontend: `AuthContext` (TanStack-Query bootstrap with full-jitter backoff) + `ProtectedRoute` + `LoginPage`; all API calls send `credentials: 'include'`, and the WebSocket refuses to connect without the cookie.

**Load-bearing rule:** JWT handling uses **PyJWT** (`import jwt`, HS256 with `Settings.jwt_secret_key`). Do NOT reintroduce `python-jose` — it drags in pure-Python `ecdsa` with the unpatchable Minerva timing-attack advisory (GHSA-wj6h-64fc-37mp). Full reference (models, router, middleware, config, startup retry, key files, deps) in **[Authentication](./docs-internal/authentication.md)**.

## Encrypted Credentials System

API keys and OAuth tokens live in a separate encrypted database (`credentials.db`): Fernet (AES-128-CBC + HMAC-SHA256) with the key derived via PBKDF2HMAC-SHA256 (600K iterations, OWASP-2024) from the server-scoped `API_KEY_ENCRYPTION_KEY` (`.env`) + a salt stored in `credentials.db`, initialized at startup and held for the process lifetime. Two distinct systems that never cross: API keys (`store_api_key`/`get_api_key` → `EncryptedAPIKey`) and OAuth tokens (`store_oauth_tokens`/`get_oauth_tokens` → `EncryptedOAuthToken`). Multi-backend (Fernet default / Keyring / AWS Secrets Manager) via `CREDENTIAL_BACKEND`.

**Load-bearing rule:** every credential operation MUST go through `AuthService` (`from core.container import container; container.auth_service()`); routers must NEVER touch `CredentialsDatabase` directly. Full pipeline, cache contract, backends, config, key files, and design decisions are in **[Credentials Encryption](./docs-internal/credentials_encryption.md)**.

## Example Workflows

### Overview
Example workflows are pre-built workflow templates that auto-load on first use. They provide users with starting points to explore the platform's capabilities. Seeds live as JSON files at **`<repo>/.machina/workflows/`** — the only git-tracked content under `<repo>/.machina/` (everything else there is `.gitignore`d as runtime state). The path is also preserved by `machina clean` (see `_MACHINA_KEEP` in `cli/commands/clean.py`).

### Architecture
```
<repo>/.machina/workflows/        # Shipped seed JSONs (git-tracked)
├── AI Assistant_example_workflow-*.json
├── AI Employee_example_workflow-*.json
└── Claude Assistant_example_workflow-*.json

server/services/
└── example_loader.py             # Loads and imports examples via core.paths.example_workflows_dir()

server/models/database.py         # UserSettings.examples_loaded flag
server/core/database.py           # Migration for examples_loaded column
server/routers/database.py        # Auto-load logic in get_all_workflows
```

### How It Works
1. **First Fetch Detection**: When `get_all_workflows` API is called, it checks `UserSettings.examples_loaded`
2. **Auto-Import**: If `examples_loaded=false`, calls `example_loader.get_example_workflows()` which reads from `core.paths.example_workflows_dir()` = `<repo>/.machina/workflows/`
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
| `.machina/workflows/*.json` | Shipped seed workflow JSONs (git-tracked) |
| `server/core/paths.py` | `example_workflows_dir()` → `<repo>/.machina/workflows/` (fixed path, NOT under `DATA_DIR`) |
| `server/services/example_loader.py` | `get_example_workflows()`, `import_examples_for_user()` |
| `server/models/database.py` | `UserSettings.examples_loaded` field |
| `server/core/database.py` | Migration adds `examples_loaded` column |
| `server/routers/database.py` | Auto-load check in `get_all_workflows` |

### Example Loader Service
```python
# server/services/example_loader.py
from core.paths import example_workflows_dir

def get_example_workflows() -> List[Dict[str, Any]]:
    """Load all example workflow JSON files from disk."""
    examples_dir = example_workflows_dir()
    ...

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
2. Copy the JSON file to `<repo>/.machina/workflows/` (git-tracked seed location)
3. Edit the `id` and `name` fields as needed
4. Delete `~/.machina/workflow.db` (or set `examples_loaded=false` in DB)
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

Multi-step welcome wizard shown on first launch — database-backed (`UserSettings.onboarding_completed` + `onboarding_step`), skippable, resumable, replayable from Settings, and auto-skipped for existing users (migration sets `onboarding_completed=1` where `examples_loaded=1`). Built with shadcn primitives (no antd). Full step list, key files, replay flow, and how to add a step are in **[Onboarding Service](./docs-internal/onboarding.md)**.

## AI Chat Model Development Guide

A new chat-model provider is one self-contained folder under `server/nodes/model/<provider>_chat_model/` with `__init__.py` declaring a `ChatModelBase` subclass (auto-registers via `BaseNode.__init_subclass__`; the frontend renders it through `SquareNode` from the NodeSpec with zero TS changes). Credentials live in `server/nodes/model/_credentials.py`. The full recipe — native chat path (`services/llm/providers/`), LangChain agent path, `llm_defaults.json` config, the dual-path routing, and the per-provider implementation-file map — is in **[Native LLM SDK](./docs-internal/native_llm_sdk.md)**.

## Simple Memory System

The Simple Memory node (`simpleMemory`, plugin `server/nodes/skill/simple_memory.py`) provides markdown-based conversation history on the agent's `input-memory` handle: window-based trimming, an editable markdown view, and optional long-term vector retrieval. Memory is loaded/parsed/appended/trimmed via the helpers in `server/services/memory/`. The full lifecycle (two storage formats, the markdown helper API, vector store, the `claude_code_agent` native-session bridge, clearing/resume) is the canonical **[Memory Lifecycle](./docs-internal/memory_lifecycle.md)**; token tracking + compaction thresholds are in **[Memory Compaction](./docs-internal/memory_compaction.md)**.

## Memory Compaction, Token Tracking, and Cost Calculation

Automatic memory compaction, token tracking, and cost calculation for all LLM providers. After each agent turn, `CompactionService.track()` records token metrics + cost (via `PricingService`) and, when cumulative tokens cross the threshold, summarises the transcript (native Anthropic/OpenAI context-management APIs where available, client-side summarisation otherwise). Effective threshold = `compaction_ratio` x the model's context window.

**Compaction-ratio precedence (highest->lowest): per-session `SessionTokenState.custom_threshold` > per-user `UserSettings.compaction_ratio` > env `COMPACTION_RATIO` (default 0.8) > `llm_defaults.json:agent.compaction.ratio`.**

Full reference — the `CompactionService` API, native vs client-side compaction, the 5-section summary format, DB models (`TokenUsageMetric` / `SessionTokenState` / `CompactionEvent`), WS handlers, and the broadcast events — in **[Memory Compaction](./docs-internal/memory_compaction.md)**; per-service API cost tracking is in **[Pricing Service](./docs-internal/pricing_service.md)**.

## API Cost Tracking

Centralized cost tracking for third-party API services (Twitter/X, Google Maps). See [Pricing Service](./docs-internal/pricing_service.md) for full documentation.

### Two Tracking Methods

**1. Manual Tracking** - For services using native SDKs:
```python
# usage tracked inside server/nodes/twitter/
await _track_twitter_usage(node_id, 'tweet', 1, workflow_id, session_id)

# usage tracked inside server/nodes/location/_service.py
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
| `server/services/plugin/edge_walker.py` | `collect_agent_connections` — tool/skill/memory discovery from edges |

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
Dedicated plugins under `server/nodes/search/` (`brave_search` / `serper_search` / `perplexity_search`) using `httpx.AsyncClient`:
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

### WebSocket Message Types

> Live count = `len(MESSAGE_HANDLERS) + len(get_ws_handlers())` (core dict in `server/routers/websocket.py` + plugin-registered handlers). The catalogue below is illustrative, not exhaustive or hand-maintained.

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
- **Skill System Architecture**: Skills organized in `server/skills/<folder>/` subfolders. Each folder appears in Master Skill dropdown. DB is source of truth for skill instructions (seeded from SKILL.md on first load). Icon resolution mirrors `BaseNode._metadata_dict`: per-plugin `<plugin>/icon.svg` (served as `/api/schemas/nodes/<type>/icon`) → `visuals.json` (emoji / `lobehub:<brand>`); color: per-plugin `<plugin>/meta.json` → `visuals.json`. Lives in [`skill_loader.py::_parse_skill_metadata`](./server/services/skill_loader.py). Native DOM keydown handler prevents React Flow from intercepting Ctrl shortcuts in skill editor. **Mutation broadcasts use the CloudEvents-typed `skill_lifecycle` wire key** with stages `created` / `updated` / `deleted` / `content_saved` — see "Plugin-folder location for plugin-specific CloudEvents factories" below.
- **Example Workflows**: Auto-load example workflow seeds from `<repo>/.machina/workflows/` on first use (git-tracked; the only non-ignored content under `<repo>/.machina/`). Path resolved by `core.paths.example_workflows_dir()` — fixed, NOT under `DATA_DIR`, preserved by `machina clean` via `_MACHINA_KEEP`. Uses `UserSettings.examples_loaded` flag; supports anonymous users (`user_id="default"`); embedded `nodeParameters` saved to DB on import.
- **Onboarding Service**: 5-step welcome wizard (Welcome, Concepts, API Keys, Canvas Tour, Get Started) using shadcn primitives + lucide icons. Database-backed via `UserSettings.onboarding_completed` + `onboarding_step`. Existing users auto-skip (migration marks `examples_loaded=1` as completed). Replayable from Settings "Help" section. No new WebSocket handlers needed. See [Onboarding Service](./docs-internal/onboarding.md) for details.
- **Node.js Code Executor**: Persistent Node.js server (Express + tsx) at port 3020 for JavaScript/TypeScript execution, replacing subprocess spawning per execution. The code executor plugins under `server/nodes/code/` call `NodeJSClient` which makes HTTP requests to the Node.js server. All config via environment variables (`NODEJS_EXECUTOR_URL`, `NODEJS_EXECUTOR_PORT`, etc.).
- **writeTodos Tool Node**: Dedicated AI tool for task planning connecting to any agent's `input-tools` handle. `TodoService` singleton (`server/services/todo_service.py`) stores JSON-based per-session todo state keyed by workflow_id. Handler broadcasts `phase: "todo_update"` via WebSocket on each update for real-time UI; `formatTodoOutput()` in `OutputDisplayPanel.tsx` renders the result as a checklist with `[ ]` / `[~]` / `[x]` icons. Schema uses `TodoItem`/`TodoStatus` Pydantic enum. Skill at `server/skills/assistant/write-todos-skill/SKILL.md` teaches the plan-work-update loop.
- **Temporal Activity Heartbeats**: Activities send `activity.heartbeat()` on every non-matching WebSocket broadcast inside the read loop in `services/temporal/activities.py`. This keeps long-running browser and claude_code_agent operations alive past the 2-minute `heartbeat_timeout`. Start/end heartbeats alone were causing `TIMEOUT_TYPE_HEARTBEAT` failures on ops taking 5-10 minutes. Connection config: `heartbeat=30`, `receive_timeout=540` (fits within 10-min `start_to_close_timeout`).
- **WebSocket `_safe_send` Guard**: `server/routers/websocket.py` checks `websocket.client_state.name != "CONNECTED"` before sending and logs at `debug` level (not `error`) on failure. Prevents "ASGI message after websocket.close" errors when broadcasts race with disconnects.
- **Claude Code CLI Flag**: the Claude Code agent (`nodes/agent/claude_code_agent/`) uses `--max-budget-usd <amount>` (previously incorrectly `--max-cost`, which caused "unknown option" errors on every run).
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
- **Plugin-folder location for plugin-specific CloudEvents factories** (RFC §6.4): when a typed event is owned by one plugin (telegram, whatsapp, android, email, google, master_skill, agent_builder, webhook_trigger, chat_trigger, agent), the factory + `broadcast_<event>` wrapper live in [`server/nodes/<plugin>/_events.py`](./server/nodes), NOT on `WorkflowEvent` / `StatusBroadcaster` directly. Telegram's [`_events.py`](./server/nodes/telegram/_events.py) is the canonical template — `broadcast_telegram_status` constructs the envelope via a plain factory function and calls `get_status_broadcaster().broadcast(...)` itself. Latest example: [`server/nodes/skill/master_skill/_events.py`](./server/nodes/skill/master_skill/_events.py) (`broadcast_skill_lifecycle("created" \| "updated" \| "deleted" \| "content_saved", name=..., **data_extra)`) — replaces four raw-dict broadcasts in `handle_create_user_skill` / `handle_update_user_skill` / `handle_delete_user_skill` / `handle_save_skill_content` (the last one was previously silent). FE routes wire key `skill_lifecycle` to invalidate `userSkills` + `folderSkills` queries and drop the `skillContent` cache on delete + content_saved so the Master Skill panel refreshes live across every connected client. Cross-cutting events (credential, workflow_lifecycle, agent_progress, node_parameters_updated) keep their `WorkflowEvent` classmethods + `StatusBroadcaster` wrappers — only plugin-scoped events move to the plugin folder.
- **Operator-metadata fields in credential panels** (f746ddf): when a credential panel needs a non-credential follow-up field (e.g. Telegram bot token + owner chat id), declare it as a second entry in the provider's `fields` array in `server/config/credential_providers.json`. The first field stays the validate/connect target; subsequent fields render below as plain text inputs via `SecondaryFieldRow` in `ApiKeyPanel.tsx` (shadcn `Input` + `Label` + `<ActionButton intent="save">`). Each field carries an optional `help` slot (`FieldDef.help` / `ServerFieldDef.help`) for always-visible explanatory text under the input. Save writes via `panel.actions.save(field.key, value)` → `auth_service.store_api_key` (which is permissive about provider string). The credential class must declare the new key in `extra_fields` (`Credential` subclass attribute) so the storage layer accepts it. Reference: `telegram_owner_chat_id` field.
- **Telegram owner detection — three layers** (f746ddf): the bot owner is captured by THREE independent paths so the realistic setup flows all converge to a working state. (1) **Explicit field**: secondary `FieldDef` in the credentials modal lets the user paste their Telegram user ID directly (recommended; works without DM'ing the bot). (2) **Pre-poll peek** in `connect()` via `_capture_owner_from_pending_updates()` — calls `bot.get_updates(timeout=0)` BEFORE `start_polling(drop_pending_updates=True)` discards the queue, scans for any historical private DM, captures + persists atomically. (3) **Atomic write-through in `_on_message_received`** — persists FIRST, sets in-memory ONLY on success. Invariant: "in-memory has owner ⇒ DB has owner" so a process restart can re-capture cleanly. Failure logged at `ERROR` with `exc_info=True` (was `WARNING` previously, masking persist failures). The lazy fallback in `telegram_send.py` (read DB → `service.set_owner`) sits on top of all three.
- **Supervisor Job Object loud-failure (cli/tree.py + cli/supervisor.py)** (8a64eb9): the Windows process-tree mechanism depends entirely on a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (no fallback — `start_new_session` is POSIX-only). `pywin32>=308` is a hard supervisor dependency on Windows; the declaration in the root `pyproject.toml` (where `machinaos-cli` lives — `cli/pyproject.toml` was removed as a stale duplicate) carries an inline comment so future maintainers don't drop it. `_JobObject.__init__` splits failures into explicit `ImportError` (with reinstall command) and `Exception` (with `repr`) branches — both write to `stderr` instead of swallowing. `_JobObject.add()` verifies enrollment via `win32job.IsProcessInJob(handle, self._handle)` after assignment; on mismatch it queries `IsProcessInJob(handle, None)` so the warning explicitly names the wrapper-job hypothesis (npm / pnpm / conhost wrappers occasionally place us in their own non-nesting Job). `supervisor._spawn_once` checks `add_to_job` return value and emits a yellow `WARN: pid=N not enrolled in Job Object` per child if False. Without these guards, a stale pywin32 install or a wrapping Job Object silently leaks orphan Python processes on every Ctrl-C and they accumulate to the point of holding SQLite locks that block subsequent backend startup.
- **Credentials envelope-shape invariant** (dc94cde): the `useCredentialPanel` query stores `{values: CredentialFormValues, hadStored: boolean}` (an envelope, NOT a flat dict). The earlier `writeValues` called `setQueryData<CredentialFormValues>` with a spread `(prev) => ({...prev, [key]: value})`, which at runtime merged the typed character at the envelope level next to `values` and `hadStored` instead of inside `.values` — the input selector `panel.values[field.key]` re-rendered with the original (server) value on every keystroke and the input felt frozen. Fix: `writeValues` now preserves the envelope shape and updates only the inner `values` dict; `hadStored` is preserved verbatim (it reflects real server state, not local edits). When introducing other queries shaped as `{data, meta}` envelopes, follow the same pattern — never spread `prev` as if it were the inner payload.
- **`core.paths` is the SSOT for on-disk locations**: only generic helpers (`data_path` / `packages_dir` / `package_dir(name)` / `daemons_dir` / `workspaces_dir` / `workspace_dir(workflow_id)` / `example_workflows_dir`). Plugin-specific subpaths are composed inline at the plugin's call site — adding a new package never requires touching `core.paths`. Reference subtree under `machina_root()` (= `~/.machina/` per default `DATA_DIR`): `workspaces/<slug>/` (per-workflow scratch), `daemons/` (supervised event-source daemon cwds — `stripe listen` etc.; shared root by default, no per-namespace subdir), `claude/` (Claude Code's `CLAUDE_CONFIG_DIR` state — composed as `data_path("claude")` in `nodes/agent/claude_code_agent/_oauth.py`), `packages/` (MachinaOs-managed install root). `packages/` holds a **single shared npm tree** — one `package.json` + `package-lock.json` + `node_modules/` covering every MachinaOs-managed npm package (`@anthropic-ai/claude-code`, `edgymeow`, `agent-browser`). Each plugin's `_install.py` runs `npm install <pkg> --prefix <packages_dir>` which extends the shared tree idempotently; npm manages everything else. Non-npm binaries (Stripe CLI, Temporal CLI) sit under sibling subdirs `packages/stripe/`, `packages/temporal/` via `package_dir(name)`. Also under `<DATA_DIR>/`: `whatsapp/` (WhatsApp session DB), `credentials.db` / `workflow.db` / `temporal.db`. Pre-fix `packages_dir()` routed through `platformdirs.user_cache_path("MachinaOs")` (`~/.cache/MachinaOs/` etc.) and the Temporal CLI sat in its own `pooch.os_cache("machinaos-temporal")` namespace — operators reported both as "not local". The WhatsApp Go bridge (`edgymeow`) used to be a top-level pnpm dep at `<repo>/node_modules/edgymeow/`; now MachinaOs-managed via `nodes/whatsapp/_install.py` like the other CLIs. Daemon cwds used to live under `workspaces/_<namespace>/` and polluted per-workflow scratch with framework state. Out of scope: globally-installed binaries (Himalaya — system package manager). Shipped seed workflows are the lone exception — `example_workflows_dir()` is hardcoded to `<repo>/.machina/workflows/` (git-tracked seeds, NOT under `DATA_DIR`) so they survive `machina clean` and stay at the same path across `DATA_DIR=~/.machina` vs `DATA_DIR=.machina` configs.
- **`machina clean` preserves `.machina/{workflows,deploy,packages}/`**: `cli/commands/clean.py` iterates `<repo>/.machina/` children and skips anything in `_MACHINA_KEEP = frozenset({"workflows", "deploy", "packages"})`. Wipes `claude/`, `workspaces/`, `*.db` as before. `workflows/` holds the shipped seed JSONs (git-tracked); `deploy/` holds Terraform state for LIVE cloud resources (only `machina deploy destroy` removes it); `packages/` holds the MachinaOs-managed binaries (Temporal CLI ~114 MB, Stripe CLI, shared npm tree) — re-fetchable but expensive, so clean+build cycles stay offline-safe cache hits. Test `cli/tests/test_clean.py::test_machina_keep_preserves_workflows_deploy_and_packages` locks the keep-list.
- **No raw `print()` outside three sanctioned helpers**: `main._startup_log` (pre-logger boot markers), `core.container._clog` (DI-bootstrap markers), and `nodes.code.python_executor.captured_print` (the sandbox builtin handed to user code). Everything else goes through `logger = get_logger(__name__)`. The supervisor prefixes every aggregated line with `[HH:MM:SS.fff]` so no inner `TimeStamper` is needed; structlog console mode is deliberately timestamp-less. Test `server/tests/test_no_raw_prints.py` AST-walks the tree and flags any unsanctioned `print(...)` call.
- **`configure_logging(settings)` must run BEFORE plugin self-registration imports**: in `main.py`, `Settings()` + `configure_logging(settings)` + `init_tracing()` + `get_logger(__name__)` happen ahead of `from core.container import container` and `from routers import …`. Otherwise plugin folders that register on import call `logger.debug(...)` while structlog is still on its default processor chain (which includes `TimeStamper` + no `filter_by_level`) — symptom is double timestamps + debug records leaking despite `LOG_LEVEL=INFO`.
- **Plugin-identifier shape validator (`services/plugin/identifiers.py`)** (`7700f87`): `NODE_TYPE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"` + `is_valid_node_type(value)`. Single source of truth used by FastAPI URL routes (`Path(pattern=NODE_TYPE_PATTERN)` in `routers/schemas.py`) AND internal helpers (`nodes/_visuals.py::get_plugin_meta` / `get_plugin_icon_path`). Closes CodeQL `py/path-injection` alerts on the icon-resolution endpoints: the registry lookup `get_node_class(node_type)` already gates the taint in practice, but CodeQL can't follow registry-mediated sanitization — adding `fullmatch` at the function boundary is the canonical pattern its rule docs prescribe. Contract test (`tests/services/test_identifiers.py`, 47 cases) locks the regex against `../etc/passwd`, `..\windows\system32`, `foo\x00bar`, `%2e%2e%2fetc`, `foo;bar`, `${HOME}`, `` foo`bar ``, CRLF, and non-string input.
- **CLI recovery-resilience invariants** (May 2026): `python -m cli clean` must run end-to-end on a system Python with no third-party deps. Achieved by: (a) every verb in [`cli/cli.py`](./cli/cli.py) is a lazy stub that imports its impl inside the function body — `import cli.cli` does NOT pull in `anyio` / `psutil` / `cli.supervisor` / sibling verb modules (300 → 131 modules at boot). (b) [`cli/_common.py`](./cli/_common.py) defers `cli.run` + `cli.supervisor` imports inside `build_backend_spec` so importing `cli._common` (and therefore `clean.py`) doesn't drag in rich. (c) [`cli/platform_.py`](./cli/platform_.py) lazy-imports `platformdirs` inside the four `user_*_dir` helpers — module loads without the wheel. (d) [`cli/commands/clean.py`](./cli/commands/clean.py) uses stdlib `print()` (no rich) + lazy `cli.ports` import inside `_kill_running_processes` wrapped in `try/except ImportError` (skip-with-warning if psutil is missing). (e) [`cli/commands/_temporal_specs.py`](./cli/commands/_temporal_specs.py) reads `os.environ["TEMPORAL_*"]` inside `temporal_specs()`, not at module load — so importing the module is safe before `load_config()` has run. (f) The supervised-runtime shim moved from `cli/commands/_supervised_runtime.py` to [`server/services/temporal/_supervised_runtime.py`](./server/services/temporal/_supervised_runtime.py) (colocated with the factories it imports). (g) `daemon` is now a verb-per-file package ([`cli/commands/daemon/`](./cli/commands/daemon/) — `_state.py` + `start.py` + `stop.py` + `status.py` + `restart.py`) following pdm's `commands/venv/` shape; PID-file resolution is a function (`pid_dir()`) not a module-level attribute so platformdirs only loads at call time.
- **Wave 13 canary fixes** — six load-bearing corrections to the Wave 12 event framework. See [docs-internal/event_framework.md → Wave 13 fixes](./docs-internal/event_framework.md#wave-13-fixes) for the full breakdown. Key invariants to remember when working on canary triggers: (1) `register_canary_trigger_type(node_type, cloudevent_type)` requires the CloudEvents reverse-DNS string as second arg — must match the producer's `WorkflowEvent.type` exactly or the deployment manager's `EventType` SA won't match `dispatch.emit`'s Visibility query (silent firing failure). Diverging re-registration raises `ValueError` so plugin upgrades surface loudly. (2) Plugin `_events.py` for canary-registered triggers is canary-only — no `event_waiter.dispatch` / `send_custom_event` calls (those have zero consumers in canary-on mode). `dispatch.emit(envelope, wire_routing_key=...)` handles BOTH Temporal Signal fan-out AND in-process WS broadcast. (3) `TriggerListenerWorkflow` + `PollingTriggerWorkflow` call `broadcast_trigger_status_activity` before/after each child spawn for firing-pulse UX (matches legacy `triggers.py` collector/processor). (4) `PollingTriggerNode.as_poll_activity` returns `seen_ids: list(current)` — NOT `list(prior_seen | current)` (the latter grows unboundedly; Gmail at ~100/day hit ~36K entries in a year). The legacy `_build_poll_coroutine` does `seen = set(current)` at end of cycle for the same reason. Visibility-filtered providers (Gmail-unread) re-emit on re-surface, which is correct. (5) Canary trigger output persistence: `MachinaWorkflow.run`'s pre-executed loop schedules `store_node_output_activity` for every firing trigger so `ParameterResolver` can resolve `{{triggerNode.field}}` in downstream nodes (the legacy `_execute_from_trigger` did this via `_store_output(trigger_node_id, "output_0", ...)`; canary skipped it pre-fix). Skips non-firing siblings (`_trigger_output={"not_triggered": True}`). (6) `DeploymentManager.cancel` sweeps stuck node statuses via `_clear_stuck_node_statuses(workflow_id, include_waiting=True)` + emits terminal `update_workflow_status(executing=False, workflow_id=...)`. Without these the FE leaves downstream nodes glowing forever after deployment cancel and the toolbar Start/Stop indicator stays at `executing=True`. The delegation guard inside `_clear_stuck_node_statuses` still protects in-flight fire-and-forget child agents.
- never use emojis in prints