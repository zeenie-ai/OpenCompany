# OpenCompany - Claude Documentation

## Project Overview
This is a React Flow-based workflow automation platform implementing n8n-inspired architectural patterns. The project has undergone a comprehensive refactoring to implement modern INodeProperties interface system with full TypeScript compliance and code cleanup.

## Documentation Reference

**Always refer to these documentation files for detailed guides:**

| Document | Description |
|----------|-------------|
| **[Frontend Architecture](./docs-internal/frontend_architecture.md)** | Current frontend stack (React 19 + Vite + Tailwind v4 + shadcn/ui + Radix + RHF/zod + TanStack Query + Zustand). Tokens, primitives, forms, credentials exemplar, ownership boundary, `uiHints` catalogue. |
| **[UI Migration Plan](./docs-internal/ARCHIVE/ui_migration_plan.md)** | *Archived.* antd → shadcn/ui migration completion log (Waves 1–10). Current state lives in [Frontend Architecture](./docs-internal/frontend_architecture.md) and [Theme System](./docs-internal/theme_system.md). |
| **[Temporal Cleanup & Resilience Plan (Waves 15-18)](./docs-internal/ARCHIVE/TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md)** | *Archived — all four waves shipped.* 15 dead-code retirement (Redis-Streams branch in event_waiter, APScheduler cron stack, orphaned `connection_status` factory), 16 per-queue task routing (`TemporalWorkerPool`), 17 resilience hardening (cron `catchup_window`, SDK interceptors, `DEPLOYMENT_MODE`, periodic heartbeats; the one-shot LLM-step retry it records was later reverted to unlimited-with-backoff), 18 worker performance tuning. Current settings live in [Temporal Architecture](./docs-internal/TEMPORAL_ARCHITECTURE.md). |
| **[Node Allowlist](./docs-internal/node_allowlist.md)** | Single-config UI visibility — `server/config/node_allowlist.json` controls which nodes / credential categories / skill folders show in the UI. Five lists with two enforcement tiers (mode-gated allowlist + absolute blocklist). `useNodeAllowlist` hook exposes `isVisible` / `isBlocked` / `isAllowed` / `isCredentialCategoryDisabled` / `isSkillFolderDisabled`. Adding a new disabled domain = single JSON edit, no code change. |
| **[Theme System](./docs-internal/theme_system.md)** | 12-way visual theme system — 2 base (light, dark) + 5 utopian (renaissance, greek, edo, steampunk, atomic) + 5 dystopian (cyber, wasteland, rot, plague, surveillance) — driven by `<html data-theme>` + per-theme CSS files in `client/src/themes/`. Token taxonomy (surface / fg / border / accent / typography / motion) in **hex + `color-mix()`**; `@theme inline` bridge maps `--color-X: var(--X)` (no `hsl()` wrapper); per-theme files own the colour hex, base.css owns the shared `--tint-*` alpha scale; action role tokens (incl. `-ink` readable text), **per-theme `--code-*` syntax tokens** (tier 6 — the code editor, console/output JSON viewers, and chat code blocks paint in each theme's own palette: keyword→trigger, string→success, number→agent, function→model, on an adaptive dark/light code surface; replaced the global dracula `--prism-*` block + dead `getPrismTokenCSS`; the `OutputPanel` JSON viewer reads the same vars), per-theme `--pulse-keyframe` animation system + `.opencompany-*` helpers in `animations.css`, decorative-layer wrappers (`.app-frame` / `.canvas-host` / `.modal-frame`), per-component decorative ornaments (panel textures + canvas decorations + node pseudo-element overlays + theme-specific keyframes), **canvas-node visual contract via `--node-color` CSS custom property** (no inline `background` / `border` on node components — base.css + per-theme CSS owns visuals; `NodeStyle` helper type at [types/NodeTypes.ts](./client/src/types/NodeTypes.ts) makes the inline custom-prop typecheck-clean), **`--node-pulse-color` separate from `--node-color`** so executing-node glow uses each theme's highest-contrast accent regardless of plugin accent (Cyber neon cyan, Surveillance REC red, Renaissance ultramarine, etc.), **`data-page-hidden` animation pause** (toggled by Dashboard's `visibilitychange` listener; base.css declares `html[data-page-hidden] *, *::before, *::after { animation-play-state: paused !important }` to prevent compositor stall on tab return), **per-theme icon glyph system** (290 SVGs across 29 keys × 10 themes via [themedGlyphs.ts](./client/src/assets/icons/themedGlyphs.ts) + theme-aware [NodeIcon.tsx](./client/src/assets/icons/NodeIcon.tsx)), **per-theme canvas-grid + custom cursors** via `--canvas-grid` / `--cursor-default` slots, **decorative HTML primitives** (`<SvgFilterDefs>` mounting `#ink-blot` / `#noise` / `#crt` filter IDs at app root, `<DropCap>` wrapper for Renaissance ornament rule), **parameter panel migrated to Tailwind tokens** (no `useAppTheme()` reads; section headers carry the display-typography triplet; raw `<Button>` swapped to `<ActionButton intent>`), **per-theme scrollbar webkit rules** in all 12 themes, 9-event WebAudio sound system (10 packs via `--sound-pack` token + `useSound()` hook + global hover delegate + sonner toast monkey-patch + `withSound()` HOC + `Sounds.unlock()` gesture-unlock for AudioContext autoplay-policy compliance), `@media (prefers-reduced-motion: reduce)` accessibility, 30 ms throttle on `type` / `hover`, migration recipe, anti-patterns. Read this before adding a new theme, migrating a component to the new contract, or adding a canvas-node component. |
| **[Design System Bundle](./docs-internal/design-system/IMPLEMENTATION.md)** | Canonical, vendored design-system reference (extracted from this repo; see its `IMPLEMENTATION.md` for the contract). Tokens are **hex + `color-mix()`** (`tokens/{colors,typography,spacing,motion,fonts,base}.css`) — the format the live codebase standardizes on. Includes 34 reference components (`components/`, inline-style reference only — recreate in Tailwind/shadcn idioms), full-app UI kit (`ui_kits/opencompany/index.html`), 12-theme docs (`guidelines/THEMES.md`), **the merged handoff brief (`HANDOFF.md` — glow-ladder traps, step-edge contract, toolbar layout traps, canvas-layer asymmetries, plus the recorded product amendments)**, and **the panel×theme fidelity target (`reference-mockup/Panel Theme Matrix.dc.html` — 17 panels × 12 themes, open in a browser)**. `reference/themes/` no longer vendors CSS snapshots — it points at the authoritative [client/src/themes/](./client/src/themes/). Copy token values verbatim; do not re-derive by eye. Pairs with [Theme System](./docs-internal/theme_system.md). |
| **[Schema Source of Truth RFC](./docs-internal/ARCHIVE/schema_source_of_truth_rfc.md)** | *Archived — shipped.* Backend is SSOT for node schemas, visual metadata, handlers, palette metadata, icons; `VITE_NODESPEC_BACKEND` defaults on and the frontend node definitions are gone. Plugin pattern: one `BaseNode` subclass in `server/nodes/<group>/<plugin>/__init__.py`. Wire format: `asset:<key>` / `<lib>:<brand>` / URL / emoji. Endpoint: `/api/schemas/nodes/{type}/spec.json`. Current reference: [Plugin System](./docs-internal/plugin_system.md). |
| **[Plugin System (Wave 11)](./docs-internal/plugin_system.md)** | Class-based plugin-first architecture. `BaseNode` / `ActionNode` / `TriggerNode` / `ToolNode` + `@Operation` decorator. Pydantic `Params`/`Output`. Declarative `Routing` DSL + `Connection` facade (Nango pattern; `request()` supports `json` / `data` / `files` — the last for multipart uploads, threaded through the auth-retry rebuild). `Credential` subclasses live in each node folder's `_credentials.py` (or inline for single-use); live count via `len(services.plugin.credential.CREDENTIAL_REGISTRY)` — 30 at time of writing, do not hand-maintain. `TaskQueue` constants route to Temporal worker pools. Plugins live across 9 queues (live count via `glob server/nodes/**/__init__.py`); handler bodies fully inlined (`services/handlers/` shrank 12.8K → 1.1K LOC across 16 → 4 files; only cross-cutting orchestration remains: `tools.py` AI-tool dispatch + agent delegation, `triggers.py` generic trigger-node handler, `todo.py` writeTodos shim — `google_auth.py` was retired into `nodes/google/_auth_helper.py`). **Wave 11.H added "self-contained plugin folders"** — up to seven generic registries (`ws_handler_registry`, `register_router`, `event_waiter.{register_filter_builder,register_trigger_precheck}`, `status_broadcaster.register_service_refresh`, `node_output_schemas.register_output_schema`, `edge_walker.register_agent_context_builder`) so rich plugins like telegram own their entire surface area without core-services edits. |
| **[Nodes Cookbook](./server/nodes/README.md)** | 5-minute recipe + folder map + shared helpers (`_base.py` / `_inline.py` per domain) + shared credentials + **canonical folder-per-plugin shape (telegram is the reference implementation)** + contract invariants + common pitfalls. Lives next to the plugin files. |
| **[Node Creation Guide](./docs-internal/node_creation.md)** | Canonical plugin recipe — one self-contained folder per plugin under `server/nodes/<group>/<plugin>/`, rooted at `__init__.py`. Multi-file split (`_service.py` / `_handlers.py` / etc.) when the plugin owns long-lived state. Zero frontend edits, zero core-services edits. Auto-registers via `BaseNode.__init_subclass__` + the six `register_*` hooks. Covers tool nodes, dual-purpose nodes (workflow + AI tool), and specialized agents as variations of the same recipe. |
| **[Agent Architecture](./docs-internal/agent_architecture.md)** | How AI Agent and Chat Agent discover skills/tools, inject them into LLM prompts, and execute through the provider-neutral `run_native_agent_loop` |
| **[Agent Delegation](./docs-internal/agent_delegation.md)** | How memory, parameters, and execution context flow when one AI agent delegates work to another agent connected as a tool |
| **[Agent Teams](./docs-internal/agent_teams.md)** | Claude SDK Agent Teams pattern - AI Employee and Orchestrator nodes with input-teammates handle for multi-agent coordination |
| **[Agent Context Flow](./docs-internal/agent_context_flow.md)** | Normative continuity reference — how a conversation survives firings, delegations, and rollovers on both paths. Plain conversation store: one `agent_conversations` row per `(workflow_id, generation, agent_node_id)` → messages JSON, loaded at run start and saved per turn (LangGraph thread_id / OpenAI conversation_id pattern; the journal/thread/epoch machinery is gone). Seeding precedence (carried rollover transcript > stored conversation > bare build, 1 MB guards), LOUD load failures (`ConversationLoadFailed` / `ConversationTooLarge`) vs best-effort saves, the specialized-provider bridge, and the August 2026 "lead forgot its plan" regression. Read before touching `services/agent_context/`, `nodes/context/`, `services/cli_agent/context_bridge.py`, `agent.prepare_payload`, or the team-completion event chain. |
| **[Memory Compaction](./docs-internal/memory_compaction.md)** | Native usage aggregation plus shared client-side summarization through `ChatUnifier` for memory-connected agents. Threshold = `Settings.compaction_ratio` (env `COMPACTION_RATIO`, default 0.8) × model context length; see the document for the per-session override limitation on Temporal runs. |
| **[Pricing Service](./docs-internal/pricing_service.md)** | Centralized cost tracking for LLM tokens and API services (Twitter, Google Maps) with HTTPX event hooks |
| **[Proxy Service](./docs-internal/proxy_service.md)** | Residential proxy provider management with template-based URL formatting, health scoring, and transparent HTTP node injection |
| **[Email Service](./docs-internal/email_service.md)** | IMAP/SMTP integration via Himalaya CLI with EmailService orchestrator, provider presets, custom credential fallback, and polling triggers |
| **[Browser Harness](./docs-internal/browser_harness.md)** | Wave 19 — `browserHarness` node integrating browser-use/browser-harness (alpha, raw CDP against the user's REAL Chrome; no Playwright). Primary op `run_python` pipes agent-authored Python to the CLI stdin against ~25 pre-imported helpers (see→act→verify loop: `capture_screenshot` → `click_at_xy` → `wait_for_load` → `js`). uv-tool install under `packages/browser-harness/`; daemon state pinned to `daemons/browser-harness/` via `BH_RUNTIME_DIR`/`BH_TMP_DIR`; Windows OK (token-auth TCP loopback IPC). Sibling of the stable `browser` node (agent-browser `@eN` refs) — NOT a replacement. Chrome must be CDP-reachable (`BU_CDP_URL` / chrome://inspect / `--remote-debugging-port`); `doctor` op diagnoses. Skill: `skills/web_agent/browser-harness-skill/`. |
| **[WhatsApp Business Service](./docs-internal/whatsapp_business_service.md)** | Official Meta Cloud API plugin in [`server/nodes/whatsapp_business/`](./server/nodes/whatsapp_business/) — **distinct from `nodes/whatsapp/`**, which is a personal account over an unofficial Go bridge; they share no node type, credential or palette group. Four nodes: `whatsappBusinessSend` (10 ops — Meta models message type as a *field* on one `/messages` endpoint, so text / media / template / interactive are operations, not nodes), `whatsappBusinessMedia` (the one genuinely separate endpoint family), and the `Receive` / `Status` triggers (kept separate because a canary trigger registers exactly one CloudEvents type). `phone_number_id` is deliberately **absent from Params** — on a dual-purpose ActionNode `execute_as_tool` merges `{**node_params, **tool_args}`, so a declared field is model-settable and a prompt injection could choose the sending identity; it comes from the credential only. Webhook via `register_webhook_source` on the shared catch-all, `X-Hub-Signature-256` HMAC failing closed, composite `wamid:status` dedup ids so `sent→delivered→read` don't collapse. |
| **[Stripe Service](./docs-internal/stripe_service.md)** | Stripe CLI integration — `stripeAction` (CLI pass-through) + `stripeReceive` (signed-webhook trigger). Reference plugin for the Wave 12 event framework AND the **CLI-managed-auth pattern**: browser OAuth via `stripe login --non-interactive` + `--complete <url>` (URL extracted from `next_step` shell command via `shlex.split`) + auto-installed binary (`ensure_stripe_cli` from GitHub releases) + marker-token reuse of `auth_service.store_oauth_tokens` + CloudEvents-shaped `broadcast_credential_event("credential.oauth.connected" \| ".disconnected", provider=...)` — zero per-provider hardcoding in the frontend. Daemon stdout/stderr ingested via `ProcessService`'s `line_handler` callback (no log-file tailing); credential gate routed through `await self.has_credential()` so non-api-key auth (`is_logged_in()`) plugs in via subclass override. |
| **[Vercel Service](./docs-internal/vercel_service.md)** | Vercel CLI integration — `vercelAction` (deploy / inspect / list + `custom` CLI passthrough; AI tool `vercel`). Second reference for the CLI-managed-auth pattern: the **device-flow variant** (`vercel login` is one blocking process — no two-step `--complete`; direct `create_subprocess_exec` + chunk-based banner read for the code-embedding URL, pumps drain pipes for the process lifetime, success gate = auth.json mtime advance + sniff). Dual auth: optional `vercel_token` field (injected as `VERCEL_TOKEN` env, never argv) OR CLI login with marker tokens. All invocations pin `--global-config <DATA_DIR>/vercel/` (the `CLAUDE_CONFIG_DIR` isolation idiom). npm install into the shared packages tree (pinned version). First-deploy project guard raises `NodeUserError` instead of letting Vercel derive an invalid name from the workspace dir. Shipped with the generic OAuthConnect change: Login gates on **required** fields only, so optional fields never block CLI login. |
| **[GitHub Service](./docs-internal/github_service.md)** | GitHub CLI integration — `githubAction` (repo_clone / pr_create / pr_list / pr_merge / issue_create / issue_list + `custom` passthrough; AI tool `github`). **Strict Stripe pattern: the gh CLI owns its own auth** — no token stored/injected by OpenCompany, no auth pre-flight (gh's own error surfaces), marker OAuth row for the modal badge, fieldless catalogue entry. Login = spawned `gh auth login --hostname github.com --git-protocol https --web` — source-verified: headless takes the `isInteractive=false` branch (no Press-Enter), prints one-time code + `github.com/login/device` URL on stderr; the modal shows the code via the generic `verificationCode` plumbing (`useCredentialPanel` → `OAuthConnect`). `login_env()` strips `GH_TOKEN`/`GITHUB_TOKEN` (login aborts otherwise). Success gate = `gh auth status` exit 0, then best-effort **`gh auth setup-git`** (official bridge — the future git node needs zero auth code). gh binary: **project-local, pooch-driven** (temporal `_install.py` idiom) — pinned release under `package_dir("gh")`, system gh never consulted (auth state still shared: gh config paths are user-level). Palette group `vcs`. |
| **[Cloudflare Service](./docs-internal/cloudflare_service.md)** | Cloudflare CLI integration — `cloudflareAction` (whoami / zones_list / dns_records CRUD / `graphql_query` / `custom` passthrough; AI tool `cloudflare`) wrapping the official `cf` CLI (npm `cf@0.2.0`, technical preview — "the next version of Wrangler"). **Two cf-specific variants of the CLI-managed-auth pattern**: (1) the CLI opens the browser ITSELF (loopback PKCE callback on fixed port 8877) so the login handler proxies nothing — single-flight guard (concurrent logins collide on the fixed port) + never-kill-the-shim invariant (killing the npm `.cmd` wrapper orphans the node child holding 8877); success gate = `cf auth whoami` JSON (`authenticated: true` — cf exits 0 in BOTH auth states). (2) **Fixed 86-scope OAuth grant with no `--scopes` flag** — only `dns_analytics:read` for analytics; no RUM/zone-analytics OAuth scope exists anywhere, so the optional `cloudflare_api_token` field (injected as `CLOUDFLARE_API_TOKEN`, cf's documented first-priority credential) is the only path to Web Analytics/RUM + the GraphQL Analytics API. `graphql_query` op POSTs the official `client/v4/graphql` endpoint directly (outside the OpenAPI schema cf/SDKs are generated from) — the replacement for the sunsetted Zone Analytics REST API; needs `Account > Account Analytics > Read`. Pinned npm install into the shared packages tree, system cf never consulted (preview argv surface drifts across versions). Palette group `deployment`. |
| **[Google Cloud Service](./docs-internal/gcloud_service.md)** | Google Cloud CLI integration — `gcloudAction` (auth/config/projects + set_project / Compute Engine instances list-start-stop-describe / Cloud Run deploy-list-describe / Cloud Storage ls-cp-rm / `custom` passthrough; AI tool `gcloud`) wrapping the official gcloud CLI (pinned versioned archive via pooch into `package_dir("gcloud")` — NOT npm; the Windows bundled-python zip keeps the legacy `google-cloud-sdk-` filename prefix, the `-cli-` spelling 404s, locked by test). Cloudflare login variant (CLI opens the browser itself, random loopback port — no fixed-port hazard) + the gh marker-token pattern; success gate = `gcloud auth list --filter=status:ACTIVE --format=json` non-empty (exit codes never trusted). **Config isolation divergence from gh**: every invocation pins `CLOUDSDK_CONFIG=<DATA_DIR>/gcloud/`, so terminal logins against the operator's global gcloud are deliberately NOT visible to the node. ADC note: node login mints gcloud USER creds only (CLI commands never read ADC); client-library code gets ADC via the `custom` op `auth application-default login` (lands isolated in the pinned dir). Cold install ~100 MB → login answers `pending: True` within the 22 s WS budget. Provider id `gcloud` is orthogonal to the Workspace `google` OAuth2 provider. Palette group `deployment`. |
| **[TikHub Service](./docs-internal/tikhub_service.md)** | TikHub social-scraping integration — `tikhubAction` (`call` / `list_endpoints` / `fetch_url` / `account`; AI tool `tikhub_action`; palette group `scraper`, visible in normal mode) over the **official `tikhub` SDK** (`tikhub>=2.1.2,<3`, auto-generated from TikHub's `openapi.json`; ~1,100 methods across TikTok / Douyin / Instagram / YouTube / Twitter / Xiaohongshu / Bilibili / Kuaishou / Weibo / Reddit / Threads / LinkedIn / Zhihu). **The node is the "flattened CLI"**: one `call` op addressed by the SDK's own `resource.method` id (`douyin_web.fetch_one_video`, also `resource/method` or the `/api/v1/...` path) with **reflective `getattr` dispatch** and `inspect.signature(...).bind` pre-flight so unknown kwargs fail before any paid request; **nothing is vendored** — `endpoint_index()` introspects the installed SDK at runtime (resources = `AsyncResource` instance attrs, params = keyword-only signature, route from the docstring's ``GET /api/v1/...`` literal), cached per process, and feeds both `list_endpoints` and the `platform`-dependent dynamic `endpoint` dropdown (`tikhubEndpoints` loader; `all` returns no options, the LLM path stays free text). `tikhub` is imported lazily inside `_sdk.py` only (plugin imports with `sys.modules["tikhub"] = None`). `TikHubCredential` = bearer `ApiKeyCredential` with a declarative httpx probe against `/api/v1/tikhub/user/get_user_info` (never the unauthenticated health route); runtime key via `TikHubCredential.resolve` so a missing key yields the structured `PermissionDeniedError` envelope. SDK `TikHubError` subclasses map to `NodeUserError` (401 key rejected -> Credentials; 403 scope-or-balance -> `account`; 429 + `retry_after`; 400/422 with TikHub `detail`; 404; 5xx upstream after the SDK's own retries) and HTTP-200-with-in-body-`code >= 400` is raised too. **Flat `$0.001`/request tracking** (`pricing.json: api.tikhub` + `operation_map.tikhub`, `track_tikhub_usage` -> `api_usage_metrics`, only after a 2xx, never for `list_endpoints`). SDK drift is guarded by two contract tests (every `PLATFORMS` value matches a live resource; every backticked id in the skill resolves). |
| **[Discord Service](./docs-internal/discord_service.md)** | Discord bot integration — `discordSend` / `discordAction` (AI tools) + `discordReceive` / `discordInteraction` triggers. **discord.py owns the gateway, REST is plugin-owned** (nodes must work with no gateway, `discordAction` needs arbitrary routes, and the invalid-request guard is process-wide across accounts). **Multi-account rides the existing `session_id` credential column** — `_accounts.py` is the only file that knows `account_id -> "discord:<app_id>"`, `"default"` is the row the modal already writes, and `credential_customer_id` is deliberately NOT used (it is per-execution-context tenancy, so two nodes in one workflow could never target different bots). **`AccountScopedNode` exists because `server_controlled_fields` is enforced only in `execute_as_tool`'s ToolNode branch** — a dual-purpose ActionNode takes an earlier `{**node_params, **tool_args}` merge with model args winning, so locked fields are stripped before it. Attachments are metadata on the trigger and downloaded by `discordAction` (deployed triggers never run their node body). Interaction tokens never leave the server — the trigger emits an opaque ref. The interactions endpoint is a plugin router, not a `WebhookSource`: Discord's validation probe requires **401** (the generic intake raises 400) and the 3-second ACK deadline precludes awaiting `emit`. Rate limiting is reactive; the invalid-request guard is **process-global** because Cloudflare bans the source IP, not the token. |
| **[Sarvam AI Service](./docs-internal/sarvam_service.md)** | Sarvam AI (Indic-first). **`nodes/sarvam/` no longer exists** — every capability it served is now a *provider* inside an abstracted node: speech in `nodes/speech/`, and translate / transliterate / detect-language in `nodes/translate/`. What remains Sarvam-specific is `sarvamChatModel` (an **OpenAI-compat provider**: one `_COMPAT_PROVIDERS` entry + a JSON block, no provider subclass) under `nodes/model/`. **One stored key spans two auth styles** — the chat route takes `Authorization: Bearer` (what the openai SDK sends) while every other endpoint takes only `api-subscription-key`, so `SarvamCredential` declares the native header and every surface shares it. Introduces the generic **`supports_model_listing: false`** flag: Sarvam ships no `/v1/models` route, and unhandled that 404 becomes a `NodeUserError` that `AIService.fetch_models` re-raises *before* its curated fallback, breaking key validation and the model dropdown — so `OpenAIProvider.fetch_models` serves the curated list and probes with a one-token completion instead (defaults `true`; the other 12 providers are untouched, locked by test). Also added generic **`Connection.request(files=)`** multipart support. |
| **[Speech Provider RFC](./docs-internal/speech_provider_rfc.md)** | Provider-abstracted speech — two nodes (`textToSpeech`, `speechToText`) with a `provider` dropdown, backed by **two registries, one per direction** (registry membership IS the capability: a synthesis-only vendor simply never appears in the transcription enum). Everything vendor-specific lives in the plugin folder [`server/nodes/speech/`](./server/nodes/speech/) — protocol, registries, dispatch, per-vendor modules; only the generic `services/provider_registry.py` and the vendor-neutral `services/media/` are shared. v1 providers: OpenAI (both directions), ElevenLabs (TTS), Deepgram + Groq (STT), Sarvam (both). Capabilities are JSON (`server/config/speech_defaults.json`) with per-model overrides, so no shared code branches on a provider name. **Audio never travels as bytes** — nodes return an `AudioRef` (a ~400 B reference) because a 12 MB base64 result blows Temporal's 2 MiB blob limit and retries 3× re-billing the provider each time. Backing routes: `GET/POST /api/workspace/{workflow_id}/…` in [`routers/workspace.py`](./server/routers/workspace.py). |
| **[Media Transport](./docs-internal/media_transport.md)** | How files move through the engine, and why they move as **references rather than bytes**. `services/media/` — `FileRef` (the base; `FileKind = file\|audio\|image\|video\|document`) with `AudioRef(FileRef)` as the probed-container narrowing (~400 B, `extra="forbid"` so adding a bytes field is a `ValidationError` rather than a silent regression), `write_audio` / `resolve_media` / `coerce_file_param` (the one nodes should call — accepts a `FileRef`/`AudioRef`, a bare path, or the legacy base64 envelope), `inspect_audio` (tinytag → stdlib `wave` → PCM arithmetic, **never raises**), `limits.py`, and **`preview.py` — the single owner of the inline-vs-attachment rule**, with two consumers: `routers/workspace.py` picks its `Content-Disposition` from `serves_inline`, and the gallery listing sets each row's `preview` from `preview_kind`, so the panel can never offer a preview the route refuses to serve. Containment runs through `resolve_within`, which closed a live vulnerability: a node joined a user path onto the workspace root unchecked, so `audio_file="../../credentials.db"` read the encrypted credential store and uploaded it. **The id/slug asymmetry is the trap** — workspace dirs are named by the mutable `Workflow.slug` while a ref stores the immutable `workflow_id` so refs survive rename; the lookup lives in [`services/workspace_locator.py`](./server/services/workspace_locator.py) (`resolve_workspace_root(..., allow_default=)`), and **every mutating caller must pass `allow_default=False`** or an unresolvable id silently lands the write in the shared anonymous workspace. HTTP surface in [`routers/workspace.py`](./server/routers/workspace.py): Range support comes free from Starlette's `FileResponse`; `NEVER_INLINE` is `{image/svg+xml, text/html, text/xml, application/xhtml+xml}` because `shell` / `fileDownloader` / `fileModify` can write arbitrary files into a workspace. |
| **[Data Node & Vision](./docs-internal/data_node.md)** | The `dataSource` agent tool (raw local data over two namespaces: workspace paths + operator-approved `mnt/<name>/...` external mounts with per-mount writable flags; typed bounded read tiers text/csv/json/pdf/html/xlsx/image-metadata/binary; write/append but **no delete**; `copy_to_workspace` imports mount files as real FileRefs), the machine-wide `data_mounts` allowlist (`services/data/mount_store.py` — refuses `DATA_DIR` overlap, drive roots, home dir), the `visionAnalyze` delegate tool (vision for every host model via openai/anthropic/gemini official SDK shapes + the `services/media/image_fit.py` visual-token budget), and the first native-image-blocks increment: `ContentBlock.source` (durable `file_ref` vs transient `bytes` — the wire codec raises on bytes), the `llm_media` tool-result opt-in, capability-gated `hydrate_image_blocks` in `run_native_llm_step`, Anthropic `tool_result` image encoding (openai/gemini encoders pending; their `vision.enabled` stays false until then). |
| **[Canvas Node](./docs-internal/canvas_node.md)** | The `canvas` node — pushed-content display board (the Claude/ChatGPT-Canvas analog) in [`server/nodes/tool/canvas/`](./server/nodes/tool/canvas/). Dual control: agents call the locked `canvas(...)` tool (paths / url / 64KB-capped markdown notes, append/replace), workflow edges auto-display upstream FileRefs via a structural scan of `connected_outputs` (`tts → canvas` is a zero-config edge; the node is the one addition to `NodeExecutor._NEEDS_CONNECTED_OUTPUTS`). Board = plugin-owned `canvas_boards`/`canvas_items` tables (mount_store mechanism, 200-item FIFO), identity-only `canvas_updated` broadcast (direct, no canary consumer), `canvas_list/remove/clear` WS handlers with the simple_memory security preamble. **Two frontend hosts share one renderer**: the `isCanvasPanel` parameter panel and the new docked resizable right sidebar (`CanvasDock` + `canvasDockStore`; auto-open via `notifyPushed`, ephemeral click-to-preview from the gallery dialog, `usePanelResize` extracted from ConsolePanel). Renders media carousel with follow-latest image poll, markdown/code/JSON via the repo's first capped client-side text fetch (`useWorkspaceText`, 512KB), external URLs + workspace HTML in strictly sandboxed iframes (never `allow-same-origin`; `NEVER_INLINE` untouched), and inline PDF via the `INLINE_EXACT` exact-match addition to `preview.py`. Browser screenshots became displayable in the same change: both browser plugins persist shots as workspace FileRefs via the tolerant `nodes/browser/_screenshots.py`. |
| **[Translate / Transliterate / Detect](./docs-internal/speech_provider_rfc.md#8-what-this-pattern-should-absorb-next)** | The second application of the multi-vendor pattern, and the one that retired `nodes/sarvam/`. Three nodes (`translateText`, `transliterateText`, `detectLanguage`) in [`server/nodes/translate/`](./server/nodes/translate/), backed by **three registries — one per capability**, because the asymmetry is sharper than speech's: DeepL translates and does nothing else, while Sarvam and LLM-backed providers do all three. Membership makes an unsupported selection unrepresentable rather than a runtime failure inside a paid call. Providers: **deepl** (`Authorization: DeepL-Auth-Key`, `text` is an array, free keys ending `:fx` route to a different host, and it returns `billed_characters` so cost is the provider's own figure), **sarvam** (all three over dedicated REST), **openai** (all three via `ChatUnifier` — proves the Protocol does not care what a provider does internally; billing deliberately NOT recorded because that path bills tokens the LLM layer already costs). Capabilities in `server/config/translate_defaults.json`. |
| **[CI/CD Pipeline](./docs-internal/ci_cd.md)** | GitHub Actions workflows, predeploy validation, release publishing, and the composite setup action (bun via SHA-pinned `oven-sh/setup-bun` reading the root `packageManager` pin + Node 22 + Python 3.12 + uv; `bun install --frozen-lockfile` everywhere, publish stays `npm publish --provenance`) |
| **[Performance](./docs-internal/performance.md)** | Cold-start measurements (Application startup complete 2.90 s warm, 21.5 s cold post-`company clean` — was 71 s before the 2026-07-14 boot-delay fixes; first WS connect 8.29 s) + warm and cold per-phase timelines + optimisation history (lazy LangChain imports, lazy LLM-SDK exception refs, esbuild sidecar bundle, bytecode-at-build, PartySocket reconnect, TanStack Query auth bootstrap, `manualChunks`, Vite dep-cache preservation, off-loop Temporal build-id hash) + bottleneck inventory + reproduction commands + anti-patterns to never reintroduce (incl. eager SDK import at LLM provider registration, unconditional `.vite` wipe, synchronous `Worker()` on the event loop). Pairs with the build-time pipeline doc below. |
| **[Release Build Pipeline](./docs-internal/release_build_pipeline.md)** | npm-distribution build pipeline: `typescript@7` (native Go compiler, exact-pinned in **root** devDependencies — client keeps `typescript@^5.x` because typescript-eslint's peer range excludes 6/7, and one manifest cannot declare `typescript` twice) for type-check, measured 5.9× faster than tsc 5.9.3 (820 ms vs 4830 ms); `bun run --filter react-flow-client typecheck` delegates up to the root gate, Vite `manualChunks` + `target: 'es2022'`, esbuild Node.js sidecar bundle (drops `tsx` interpreter cost), bytecode pre-compile in two halves — `[tool.uv] compile-bytecode = true` (uv sync compiles `.venv/` site-packages) + `python -m compileall` for project source (plain `.pyc`, **no `-O`** — per PEP 488 the non-`-O` runtimes never load `.opt-1.pyc`; excludes `.venv/`, `tests/`). Single source of truth for the bytecode-compile path list: `cli.commands.build.COMPILEALL_SOURCE_DIRS`. Dev PM is **bun@1.4** (tracked text `bun.lock`, `bunfig.toml` pins the isolated linker, ranged security pins in top-level `overrides`); `company build` step [1/6] is `bun install`. |
| **[Workflow Schema](./docs-internal/workflow-schema.md)** | JSON schema for workflows, edge handle conventions, config node architecture |
| **[Execution Engine Design](./docs-internal/DESIGN.md)** | Architecture patterns, design standards, and implementation details for the workflow execution engine |
| **[Execution Roadmap](./docs-internal/ARCHIVE/ROADMAP.md)** | *Archived* status snapshot; current state is in [Execution Engine Design](./docs-internal/DESIGN.md) and the Temporal docs |
| **[Setup Guide](./docs-internal/SETUP.md)** | Development environment setup and installation instructions |
| **[Scripts Reference](./docs-internal/SCRIPTS.md)** | `bun run` scripts, `company` CLI verbs, and their usage |
| **[Server Documentation](./docs-internal/server-readme.md)** | *Archived* at [ARCHIVE/server-readme.md](./docs-internal/ARCHIVE/server-readme.md); see [Setup Guide](./docs-internal/SETUP.md), [Authentication](./docs-internal/authentication.md), [Plugin System](./docs-internal/plugin_system.md) |
| **[Skill Creation Guide](./server/skills/GUIDE.md)** | How to create new skills (folder structure, SKILL.md format, metadata, supporting files) |
| **[Known Errors & Troubleshooting](./docs-internal/errors.md)** | Documented root causes and fixes for common errors (SQLAlchemy Windows hang, Temporal issues, WhatsApp timeouts) |
| **[New Service Integration](./docs-internal/ARCHIVE/new_service_integration.md)** | *Archived* pre-Wave-11 guide; integrate a service as one plugin folder per [Node Creation Guide](./docs-internal/node_creation.md), with [server/nodes/google/](./server/nodes/google/) as the OAuth reference |
| **[Onboarding Service](./docs-internal/onboarding.md)** | First-launch welcome wizard with 4 steps, database persistence, and replay from Settings |
| **[CLI Services Integration](./docs-internal/cli_services_integration.md)** | Guide for integrating CLI-based services (Temporal, etc.) with proper lifecycle management |
| **[Temporal Architecture](./docs-internal/TEMPORAL_ARCHITECTURE.md)** | Distributed workflow execution: activities, FIRST_COMPLETED scheduling, horizontal scaling |
| **[Native LLM SDK](./docs-internal/native_llm_sdk.md)** | Native SDK layer in `services/llm/`: protocol-based providers, config-driven base URLs, and 13 providers (11 cloud plus Ollama / LM Studio; xAI is agent-selectable without a standalone chat-model node). A JSON-driven `supports_model_listing: false` flag covers endpoints that speak the OpenAI chat wire format but ship no `/v1/models` route (Sarvam) — `OpenAIProvider.fetch_models` then serves the curated list and probes the key with a one-token completion. Every provider is native; `tests/llm/test_langchain_removed.py` locks the retired-dependency end state. |
| **[RFC-0003 — OpenAI-Compatible Provider Contract](./RFC-0003-OPENAI-COMPATIBLE-PROVIDER-CONTRACT.md)** | Draft. "OpenAI-compatible" is a family of divergent shapes with no spec behind it, and the client currently *guesses* per-provider facts it cannot infer. Twelve decisions (D1–D12) with acceptance gates: a configured `base_url` is copied **verbatim** from vendor docs and never rewritten (`/v1` is neither universal nor positionally predictable — DeepSeek is root-mounted, Groq is `/openai/v1`, Sarvam is `/v1` *and* `/v2`); a **user-supplied** URL is resolved by **probing** `GET {base}/models` then `{base}/v1/models` at save time, because a path-less URL is indistinguishable from a deliberately root-mounted server without probing; 401/403 counts as rooted at probe time and as a credential failure at runtime; the credential is resolved by one function and no provider hardcodes a placeholder key (the `api_key="ollama"` overwrite in `OpenAIProvider.__init__` applies to *every* provider with a proxy row); `api_key=None` must never reach the SDK, since it falls back to `OPENAI_API_KEY` and ships the operator's key to a third-party `base_url`; capabilities are declared, never sniffed, and a flag ships only with a consumer; a 2xx with no `choices` and an error body raises `PROTOCOL` naming the URL called. Five revertible phases, `ProviderSpec` gains no field, `_strip_v1_path` survives scoped to the native probe. Also records that `native_llm_sdk.md`'s DeepSeek `/v1` claim is wrong and the path-less JSON entry is correct. |
| **[Event Waiter System](./docs-internal/event_waiter_system.md)** | In-memory asyncio.Future waiter for push-based trigger nodes on the canvas-Run path (WhatsApp, Telegram, Webhook, Chat, Task completion). Deployed triggers ride the Temporal canary path; the Redis-Streams backend was retired in Wave 15.3 |
| **[Credentials Encryption](./docs-internal/credentials_encryption.md)** | Fernet + PBKDF2 encryption pipeline, separate credentials.db, two credential systems (OAuth vs API keys), multi-backend abstraction |
| **[Status Broadcaster](./docs-internal/status_broadcaster.md)** | WebSocket-first communication: StatusBroadcaster singleton, live count via `len(MESSAGE_HANDLERS) + len(get_ws_handlers())`, broadcast message types, Android two-state model |
| **[RLM Service](./docs-internal/rlm_service.md)** | Recursive Language Model agent with REPL-based execution (llm_query, rlm_query, FINAL) |
| **[Claude Code Agent](./docs-internal/claude_code_agent.md)** | Hub for the Claude Code agent — routes to architecture, interactive mode, CLI framework, canonical-patterns RFC, and the 5 CLI snapshot references. |
| **[CLI Agent Framework](./docs-internal/cli_agent_framework.md)** | Multi-provider CLI runtime (Claude Code / Codex / Gemini): `AICliService.run_batch`, per-task worktree isolation, FastMCP bridge, **memory bridge** (`--continue` for first run + intra-process stream-json multi-turn + `--resume <UUID>` for crash recovery, all on a stable `cwd=repo_root`; `node_parameters_updated` broadcast on every successful turn). **Plugin-folder layout** (post-cutover): all claude-specific code lives in [`server/nodes/agent/claude_code_agent/`](./server/nodes/agent/claude_code_agent/) — `_provider.py`, `_pool.py`, `_skills.py`, `_oauth.py`, `_handlers.py` — and self-registers via three `factory.py` registries (`register_provider` / `register_session_pool` / `register_skill_materialiser`) plus `register_ws_handlers`. The generic framework at `services/cli_agent/` imports nothing from `nodes/`. |
| **[Claude Code Interactive Mode](./docs-internal/claude_code_interactive_mode.md)** | The interactive-mode cutover: OpenCompany no longer uses `claude -p` headless. `ClaudeSessionPool` (keyed by `simpleMemory.node_id`) spawns `claude` as a plain subprocess with stdio pipes — **no PTY** — and drives it over the VSCode-extension protocol: `--output-format stream-json --input-format stream-json --verbose --ide`. User prompts written as JSON to `proc.stdin` (`{"type":"user","message":{"role":"user","content":"..."}}\n`); `system/init` / `assistant` / `result` / `system/compact_boundary` events stream back on `proc.stdout` (parsed by a background `stdout_reader_task` — the on-disk JSONL is persistence-only, not the runtime contract, because `result` events are stdout-only in stream-json mode). Cross-platform via plain pipes (the earlier PTY pattern was broken on Windows because pywinpty/ConPTY's emulated stdin never reached claude's Ink TUI keystroke handler). Stays in interactive billing — entrypoint `claude-vscode`, NOT `sdk-cli`. Multi-turn within one warm subprocess preserves the session UUID (verified end-to-end); cross-batch continuity via `--continue` (first run) or `--resume <UUID>` (crash recovery). Four typed CloudEvents (`claude.session.{spawned,cleared,terminated,usage}`) fire from the pool. `/compact` events forward to `CompactionService`. The non-pooled `AICliSession` path (one-shot prompt-in-argv runs) still uses PTY (out of scope for this refactor; works on POSIX). |
| **[CLI Agent Canonical Patterns RFC](./docs-internal/cli_agent_canonical_patterns_rfc.md)** | Audit of OpenCompany's `services/cli_agent` against the official Claude Code spec — six invariants (skills-as-files, MCP-only transport, `list_changed`, deferral, visible-tool filtering, native-session-continuity-via-stable-cwd) with current implementation status. |
| **[Claude Code CLI Reference (snapshot)](./docs-internal/claude_code_cli_reference.md)** | Verbatim snapshot of [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference) — every CLI subcommand + flag + system-prompt-flag matrix + the subset we emit from `nodes/agent/claude_code_agent/_provider.py`. Fetched 2026-05-11. |
| **[Claude Code Env Vars (snapshot)](./docs-internal/claude_code_env_vars_reference.md)** | Categorised [code.claude.com/docs/en/env-vars](https://code.claude.com/docs/en/env-vars) snapshot — auth, Bedrock/Vertex, model, bash, MCP, telemetry, session/debug, paths. Documents `CLAUDE_CONFIG_DIR`, `MAX_MCP_OUTPUT_TOKENS`, `ENABLE_TOOL_SEARCH` which we touch directly. |
| **[Claude Code Permission Modes (snapshot)](./docs-internal/claude_code_permission_modes_reference.md)** | Verbatim [code.claude.com/docs/en/permission-modes](https://code.claude.com/docs/en/permission-modes) — `default` / `acceptEdits` / `plan` / `auto` / `dontAsk` / `bypassPermissions` semantics, Shift+Tab cycle, protected paths. OpenCompany default is `acceptEdits`. |
| **[Claude Code Headless / Print Mode (snapshot)](./docs-internal/claude_code_headless_reference.md)** | Verbatim [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless) — `claude -p`, `--output-format` (`text` / `json` / `stream-json`), `--input-format`, `--bare`, stream-json event schema (`system/init`, `system/api_retry`, `system/plugin_install`). OpenCompany no longer uses `-p`; the pool path emits `--output-format stream-json --input-format stream-json --verbose --ide` over stdio pipes (interactive billing). The event schema is the contract `nodes/agent/claude_code_agent/_pool.py` parses off `proc.stdout`. |
| **[Claude Code Skills (snapshot)](./docs-internal/claude_code_skills_reference.md)** | Verbatim [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills) — `SKILL.md` frontmatter spec, discovery paths, content lifecycle, `context: fork`, dynamic context injection via `` !`<command>` ``. The spec OpenCompany materialises connected skills against in `_pre_spawn`. |
| **[Autonomous Agent Creation](./docs-internal/ARCHIVE/autonomous_agent_creation.md)** | *Archived* external research notes on Code Mode agents; the shipped plugin is [server/nodes/agent/autonomous_agent/](./server/nodes/agent/autonomous_agent/) |
| **[Polyglot Server](../polyglot-server/ARCHITECTURE.md)** | Plugin registry microservice with MCP gateway (optional integration) |
| **[Authentication](./docs-internal/authentication.md)** | JWT/cookie auth: toggle, single/multi mode, backend + frontend, middleware, startup retry. |
| **[Credentials Panel](./docs-internal/ARCHIVE/credentials_panel.md)** | *Archived* pre-refactor modal reference; its §5 invariants are still what `server/tests/credentials/` and `client/src/test/` lock. The live, config-driven modal is described in [Frontend Architecture → Credentials](./docs-internal/frontend_architecture.md). |
| **[Memory Lifecycle](./docs-internal/ARCHIVE/memory_lifecycle.md)** | *Archived — pre-RFC-0002.* Documented the retired `input-memory` markdown model. See "Context and Memory (RFC-0002)" below, [Agent Context Flow](./docs-internal/agent_context_flow.md) and [Memory Compaction](./docs-internal/memory_compaction.md). |
| **[Node Parameter Panel](./docs-internal/node_panels.md)** | Three-section node config UI (Input / Parameters / Output) logic-flow reference. |
| **[Deployment (legacy reference)](./docs-internal/deployment_legacy.md)** | `company deploy` CLI summary + the historical Docker Compose topology. |
| **[GCP VM Deploy Runbook](./docs-internal/gcp_vm_deploy_runbook.md)** | Manual gcloud + Cloudflare runbook for deploying the released npm package (non-Terraform path). |

## Design Principles & Standards

**CRITICAL: Always follow these principles when modifying backend execution code:**

### 0. Adding a new node — the canonical recipe (Wave 11.H)

Every plugin is a self-contained folder under `server/nodes/<group>/<plugin>/` rooted at `__init__.py`. `BaseNode.__init_subclass__` auto-registers metadata, schemas, handlers, and Temporal activity on import — zero edits anywhere else.

**Where to look:**
- [server/nodes/README.md](./server/nodes/README.md) — 5-minute walkthrough with the canonical folder template
- [docs-internal/plugin_system.md → Self-contained plugin folders](./docs-internal/plugin_system.md#self-contained-plugin-folders) — full reference, plus the **up-to-seven generic registries** plugins self-wire into (`register_ws_handlers`, `register_router`, `register_filter_builder`, `register_trigger_precheck`, `register_service_refresh`, `register_output_schema`, `register_agent_context_builder`)
- [docs-internal/node_creation.md](./docs-internal/node_creation.md) — decision tree for action / trigger / tool / dual-purpose / specialized-agent nodes
- [server/nodes/telegram/](./server/nodes/telegram/) — reference implementation of the multi-file split (`_service.py` / `_handlers.py` / `_filters.py` / `_refresh.py` / `_credentials.py` / `_events.py` / two node files)

**Wire format is the contract — not module paths.** The frontend identifies plugin commands by WebSocket message-type strings (`telegram_connect`, `telegram_status`, …). Moving handler bodies between Python files is invisible to the frontend so long as the registered keys stay the same.

**Don't** import the plugin folder from `routers/` / `services/` / another `nodes/` subfolder. **Don't** edit `event_waiter.py` / `status_broadcaster.py` / `routers/websocket.py` to add a plugin's handler / filter / refresh — register from the plugin's `__init__.py` instead.

### 1. Use Existing Patterns - No Tribal Code
- **Never add ad-hoc workarounds** - Use the established patterns documented in DESIGN.md
- **Conductor Decide Pattern** - All orchestration goes through `_workflow_decide()` loop
- **Continuous Scheduling** - Dependent nodes start immediately via `asyncio.wait(FIRST_COMPLETED)`; the layer-barrier Fork/Join helper (`_execute_parallel_nodes`) was removed
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
├── workflow.py              # Facade (~840 lines) - thin coordinator
├── node_executor.py         # Single node execution with registry pattern
├── parameter_resolver.py    # Template variable resolution
├── agent_team.py            # AgentTeamService for multi-agent coordination
├── model_registry.py        # ModelRegistryService - model constraints from OpenRouter + llm_defaults
├── pricing.py               # LLM and API cost calculation (loads config/pricing.json)
├── markdown_formatter.py    # GFM markdown to platform-specific formatting (Telegram HTML, WhatsApp, plain)
├── ws_handler_registry.py   # Plugin-owned WS commands self-register here (Wave 11.H)
├── browser_service.py       # BrowserService singleton wrapping agent-browser CLI
├── himalaya_service.py      # HimalayaService CLI wrapper for IMAP/SMTP (any email provider)
├── email_service.py         # EmailService orchestrator (credential resolution, provider presets)
├── todo_service.py          # TodoService singleton for writeTodos tool (JSON per-session state)
├── media/                   # Media transport — vendor-neutral, kind-agnostic (see media_transport.md)
│   ├── refs.py              # FileRef (base; FileKind = file|audio|image|video|document)
│   │                        # + AudioRef(FileRef), the probed-container narrowing.
│   │                        # A reference, never bytes; extra="forbid" makes that structural.
│   │                        # kind="audio" ASSERTS inspect_audio ran — never guess it.
│   ├── preview.py           # Single owner of the inline-vs-attachment rule (serves_inline /
│   │                        # preview_kind). TWO consumers: routers/workspace.py picks the
│   │                        # Content-Disposition, the gallery listing sets each row's
│   │                        # `preview`. One function, so the panel can never offer a preview
│   │                        # the route refuses to serve inline.
│   ├── workspace.py         # write_audio / resolve_media / read_media_bytes / coerce_file_param
│   ├── inspect.py           # tinytag -> wave -> PCM arithmetic; NEVER raises
│   └── limits.py            # every size constant, each annotated with what it defends against
├── provider_registry.py     # Generic ProviderSpec + ProviderRegistry + lazy exception refs.
│                            # Shared by services/llm and nodes/speech (which owns two, one per
│                            # direction). Registration must never import a provider SDK.
├── memory/                  # Native Markdown/JSONL/runtime/vector-store helpers
├── memory_store.py          # In-memory conversation sessions using native Message values
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
│   ├── todo.py              # writeTodos execution shim (used by every agent)
│   └── __init__.py          # Docstring only (google_auth.py retired into nodes/google/_auth_helper.py)
├── llm/                     # Native LLM provider SDKs for chat and every new agent execution
│   ├── __init__.py          # Public API exports
│   ├── protocol.py          # ThinkingConfig, Message, LLMResponse, LLMProvider Protocol
│   ├── config.py            # ProviderConfig, resolve_max_tokens, resolve_temperature
│   ├── registry.py          # ProviderSpec + register_provider — sdk_exception_refs are LAZY "module:Class" strings resolved via pkgutil.resolve_name at except/read time; NEVER import an SDK at registration (locked by tests/llm/test_lazy_sdk_imports.py)
│   ├── unifier.py           # ChatUnifier facade — routes chat/fetch_models, translates typed SDK errors → NodeUserError, applies incompatible_models filter
│   ├── vertex.py            # Vertex / Agent-Platform key handling
│   ├── messages.py          # filter_empty_messages, is_valid_message_content
│   └── providers/           # Per-provider implementations (+ _compat.py for the 8 OpenAI-compatible providers)
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
├── paths.py                 # SSOT for on-disk locations (generic helpers only): opencompany_root()/data_path()/workspaces_dir()/workspace_dir()/daemons_dir() + packages_dir()/package_dir(name) all under ~/.opencompany/ (= DATA_DIR); packages/ holds the single shared npm tree + stripe/ + temporal/ binary subdirs; example_workflows_dir() = <repo>/.opencompany/workflows/ (shipped seeds, NOT under DATA_DIR). Plugin-specific subpaths composed inline at the call site, never added here.
├── env_defaults.py          # Env accessor backed by .env.template/.env (the SSOT for port numbers; used by entry points that bypass the CLI env push)
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
├── email_providers.json     # IMAP/SMTP provider presets (Gmail, Outlook, Yahoo, iCloud, ProtonMail, Fastmail, custom)
├── speech_defaults.json     # Per-provider TTS/STT capabilities, per-direction, with per-model overrides
                             # ({"whisper-1": [...], "_default": [...]} resolved exact -> longest-prefix ->
                             # _default). Drives the provider dropdowns, voice/model loaders and validation.
                             # Boolean flags default PERMISSIVE so a missing declaration never silently
                             # disables a working feature. Read by nodes/speech/_config.py.
└── translate_defaults.json  # Same shape, three capability blocks per provider (translate /
                             # transliterate / detect). Both files are resolved by the shared
                             # services/plugin/capabilities.CapabilityConfig.

server/nodejs/                   # Persistent Node.js server for JS/TS execution
├── package.json                 # Dependencies: express, tsx
├── tsconfig.json                # TypeScript config (ES2024)
├── src/
│   └── index.ts                 # Express server (/execute, /health, /packages/*)
└── user-packages/               # User-installed npm packages
```

### Polyglot Server Integration (Optional)
OpenCompany can optionally integrate with the sibling **polyglot-server** repo (a plugin-registry microservice exposing REST + MCP + WebSocket). NOTE: the OpenCompany-side client/handler (`polyglot_client.py`, `handlers/polyglot.py`) are not currently present in the tree — this is a possible future integration, not wired. See [Polyglot Server](../polyglot-server/ARCHITECTURE.md).

### Node.js Code Executor
Persistent Node.js server for JavaScript/TypeScript code execution, replacing subprocess spawning per execution. **Plugin-owned (July 2026):** the sidecar is supervised by `nodes/code/_runtime.py` (`NodeJSExecutorRuntime`, same `BaseProcessSupervisor` pattern as WhatsApp/Temporal) and spawns on demand from `acquire_client()` on the first JS/TS node execution — in every run mode, with no CLI service wiring. Client + config are plugin-owned too (`nodes/code/_client.py`, `NODEJS_EXECUTOR_*` env vars; core `Settings` carries no executor fields).

**Architecture:**
```
┌─────────────────────────────────────────────────────────────┐
│           Python Backend (PYTHON_BACKEND_PORT)               │
│  ┌────────────────┐     HTTP/JSON      ┌──────────────────┐ │
│  │ NodeJSClient   │◄──────────────────►│  Node.js Server  │ │
│  │ (aiohttp)      │ NODEJS_EXECUTOR_PORT │ (Express + tsx)│ │
│  └────────────────┘                    └──────────────────┘ │
│         ▲                                                    │
│         │                                                    │
│  ┌──────┴─────────┐                                         │
│  │ nodes/code/    │                                         │
│  │ plugins        │                                         │
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

server/nodes/code/                # Executor plugins (javascript_executor, typescript_executor)
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
| `NODEJS_EXECUTOR_URL` | `http://localhost:${NODEJS_EXECUTOR_PORT}` | Server URL for Python client |
| `NODEJS_EXECUTOR_TIMEOUT` | `30` | Request timeout in seconds |
| `NODEJS_EXECUTOR_PORT` | see `.env.template` | Server port |
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
8. **Icons → `lucide-react`.** Inline SVGs are reserved for non-iconographic graphics (charts, decorative shapes). Replace any `<svg>...</svg>` icon you encounter while editing. **Icon size is a token, never a class**: backend-declared node/provider icons render through `<NodeIcon size={...}>` with `theme.nodeSize.squareIcon` on canvas nodes (36px, the one node-icon size) or a `theme.iconSize.*` step elsewhere, and `NodeIcon` applies that token as width, height AND emoji font size so every icon kind draws at the same edge. Never pass `h-*` / `w-*` / `text-*` sizing to it — the theme type scale maps `text-3xl` to 44px, which is how emoji nodes once painted half again larger than SVG-backed nodes beside them.
9. **Any `draggable` element that performs a function must ship a pointer-operable, non-drag control reaching the same end state through the same write path.** WCAG 2.2 SC 2.5.7 (Level AA); the sufficient technique is [G219](https://www.w3.org/WAI/WCAG22/Techniques/general/G219), the failure is [F108](https://www.w3.org/WAI/WCAG22/Techniques/failures/F108). Two traps worth stating: **keyboard support does not discharge it** — the Understanding doc is explicit that an equivalent counts only "*unless that equivalent keyboard operation also provides controls that can be clicked or tapped with a pointer*", because touchscreen users may have no keyboard. And the alternative must operate *the same function*, so extract the write into a shared helper rather than reimplementing it — see [`lib/workspaceFileAssign.ts`](./client/src/lib/workspaceFileAssign.ts), which both `ParameterRenderer.handleDrop` and `WorkspaceFilePickerDialog` call, with a test asserting the two paths produce deep-equal results.

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
- **Backend NodeSpec is the single source of truth.** Plugins live in [`server/nodes/<group>/<name>.py`](./server/nodes/) and auto-register via `BaseNode.__init_subclass__`. Authoritative node count is whatever globs out of `server/nodes/**/*.py` (excluding `_*.py` helpers and `__init__.py`); folders cover agent / model / android / google / whatsapp / twitter / telegram / social / email / search / scraper / document / code / filesystem / proxy / location / chat / text / scheduler / trigger / tool / utility / workflow / skill / browser / stripe / vercel / github / cloudflare / gcloud / sarvam.
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
- Audited and converted across the canvas + parameter-panel hot paths: every node component, `Dashboard.tsx`, `useDragVariable`, `useParameterPanel`, `useReactFlowNodes`, `useWorkflowManagement`, `InputSection`, `MiddleSection`, `OutputPanel`, `ParameterRenderer`, `ParameterPanel`. New code should follow.

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
- Applies to `SquareNode`, `AIAgentNode`, `TriggerNode`, `ToolkitNode`, `StartNode`, `TeamMonitorNode`. Add new node components the same way.
- Reference: https://reactflow.dev/learn/advanced-use/performance

### Icon + color — per-plugin folder + visuals.json fallback

Plugin icons and colors live co-located in the plugin folder; `visuals.json` is the legacy central registry for plugins that predate it. Resolution chain: **co-located SVG (`icon_<nodeType>.svg` → `icon.svg`) → the plugin's own `meta.json` → `visuals.json`**, documented in [server/nodes/README.md → Icon + color](./server/nodes/README.md) and [docs-internal/plugin_system.md](./docs-internal/plugin_system.md).

**For a node that belongs to a recognisable product, ship the brand mark as an SVG in the plugin folder** — `icon_<nodeType>.svg` per node type, `icon.svg` for the whole folder. This is what makes a node identifiable at canvas size, and it depends on nothing outside the repo.

`meta.json` can also carry icon *references* (`"icons": {"<nodeType>": "lucide:Send"}` per node type, or `"icon"` folder-wide) for generic utility nodes with no brand of their own. Treat that as the lesser option: a `lucide:<Name>` string is a hard dependency on a third-party export that can be renamed or dropped, and when it breaks the node renders **nothing** rather than erroring. Library glyphs are also monochrome `currentColor` line art, so they read as washed-out grey next to brand artwork.

Three traps: a **file always beats a meta.json ref**, so a folder shipping `icon.svg` shadows refs for every node type in it (this is why the four WhatsApp Business nodes once rendered one glyph); `lucide:` names match the package's **export** identifiers, so `lucide:CheckCheck` resolves and kebab-case `lucide:check-check` silently renders nothing; and emoji, while dependency-free, are generic rather than branded. Locked from both sides — `client/src/assets/icons/index.test.ts` walks every `meta.json` under `server/nodes` and asserts each ref resolves to a real component; `test_node_spec.py` asserts every `icons` key is a registered node type.

Backend endpoints serve SVGs at `GET /api/schemas/nodes/<type>/icon` (plugin icons) and `GET /api/schemas/credentials/<provider>/icon` (credential brand icons). Frontend resolver at [client/src/assets/icons/index.ts](./client/src/assets/icons/index.ts) dispatches `lib:brand` / URL passthrough / emoji / `asset:<key>`. The `asset:` branch is currently inert at runtime (the frontend `ICON_REGISTRY` glob finds no SVGs so it resolves to null) but it is NOT dead code — the backend still emits `asset:google` / `asset:stripe` for palette groups (`server/nodes/groups.py`) and the format is invariant-tested on both sides (`test_node_spec.py` Wave 10.B, `icons/index.test.ts`).

**Do not** declare `icon` / `color` as class attributes on a node (the override path was removed in F1). Put `meta.json` in the plugin folder (colour, plus icon refs as above), and `icon.svg` / `icon_<nodeType>.svg` only when artwork is genuinely needed. SKILL.md icon/color resolves from the first node in `allowed-tools` — only orphan skills keep inline `metadata.icon` / `metadata.color`.

## Key Files & Components

### Core Types
- `src/types/INodeProperties.ts` - Core interfaces for n8n-inspired node properties system
- `src/types/NodeTypes.ts` - Legacy compatibility types (NodeParameter, NodeOutput)
- `src/types/workspaceFiles.ts` - Wire types for the gallery / workspace file explorer (`WorkspaceEntry`, `WorkspaceFileRef`, `FILE_REF_KINDS`, `isWorkspaceFileRef`, `WORKSPACE_FILE_DRAG_TYPE`). Declarations only — it documents which decisions are server-owned and must not be re-derived client-side

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
- `src/components/output/OutputPanel.tsx` - Connected node output display with drag mapping
- `src/components/LocationParameterPanel.tsx` - Location-specific parameter handling
- `src/components/parameterPanel/GalleryPanel.tsx` - Workspace file explorer for the `gallery` node (`isGalleryPanel` uiHint); supporting parts in `parameterPanel/gallery/` (`FilePreviewDialog`, `FileGlyph`, `fileIcons`)
- `src/components/AIAgentNode.tsx` - Spec-driven agent canvas component. Reads `useNodeSpec(type)` for handles / icon / colour / displayName / uiHints; renders any plugin whose backend `component_kind` is `"agent"` or `"chat"`. No `AGENT_CONFIGS` map.
- `src/ParameterPanel.tsx` - Main parameter configuration modal

### AI Chat Model Components
AI model nodes route through `SquareNode` via `Dashboard.tsx`'s `COMPONENT_BY_KIND['model']` lookup. Per-provider visual data (icon, color, displayName) comes from the backend `NodeSpec` declared in `server/nodes/model/<provider>_chat_model/__init__.py`. The pre-Wave-11 per-provider wrappers (`BaseChatModelNode`, `OpenAIChatModelNode`, `ClaudeChatModelNode`, `GeminiChatModelNode`, `ModelNode`) were deleted -- nothing imported them after the migration.

### Specialized UI
- `src/components/maps/GoogleMapsPicker.tsx` - Interactive location picker (click / drag marker); wrapped by `maps/MapsPreviewPanel.tsx` and rendered through `parameterPanel/MapsSection.tsx` for `gmaps_create`. Uses Google's default map styling.
- `src/components/output/OutputPanel.tsx` - Execution result display (the active renderer; the legacy `ui/OutputDisplayPanel.tsx` was deleted)
- `src/components/ui/ComponentPalette.tsx` - Searchable component library with emoji icons and dracula-themed category colors. Categories: Workflow, Triggers, AI Agents, AI Models, AI Skills, AI Abilities, AI Tools, Google Maps, Social Media Platforms (merged WhatsApp + Social), Android, Chat, Code Executors
- `src/components/ui/ComponentItem.tsx` - Draggable node items with hover effects and icon rendering
- `src/components/ui/CodeEditor.tsx` - Syntax-highlighted code editor (react-simple-code-editor + prismjs). Token colours come from the per-theme `--code-*` tokens (see [Theme System](./docs-internal/theme_system.md) tier 6) — the code editor, console/output JSON viewers, and chat code blocks all paint in the active theme's syntax palette, not a global dracula scheme. (Retired the old `--prism-*` block + dead `getPrismTokenCSS()`.)

### Hooks & State
- `src/hooks/useParameterPanel.ts` - Parameter management via WebSocket
- `src/services/executionService.ts` - Node execution via WebSocket (`executeNodeViaWebSocket`)
- `src/hooks/useApiKeys.ts` - API key management via WebSocket
- `src/hooks/useWhatsApp.ts` - WhatsApp operations via WebSocket
- `src/hooks/useDragAndDrop.ts` - Drag-and-drop functionality (palette → canvas, `application/reactflow`)
- `src/hooks/useDragWorkspaceFile.ts` - Drag a workspace file onto another node's parameter (`workspaceFile` payload); mirrors `useDragVariable`'s dual-MIME contract — `application/json` for the structured payload, `text/plain` for the bare path
- `src/hooks/useComponentPalette.ts` - Component palette state with localStorage persistence
- `src/store/useAppStore.ts` - Zustand application state with localStorage persistence for UI settings

### Theme System

12-way visual theme system (2 base: light, dark + 5 utopian: renaissance, greek, edo, steampunk, atomic + 5 dystopian: cyber, wasteland, rot, plague, surveillance) driven by `<html data-theme>` (set by [ThemeContext.tsx](./client/src/contexts/ThemeContext.tsx), which also toggles `.dark` for DARK_FAMILY themes) + per-theme CSS in `client/src/themes/`. Token VALUES are hex + `color-mix()` (never HSL): per-theme files own shadcn/dracula/node/action hex, `base.css` owns the shared `--tint-*` scale, `index.css` is plumbing (`@theme inline` maps `--color-X: var(--X)`). Six token tiers, the per-theme `--pulse-keyframe` animation system, decorative-layer wrappers, the 10-pack WebAudio sound system, and the canvas-wide edge/node status rules (`canvasAnimations.ts`) are all documented in **[Theme System](./docs-internal/theme_system.md)** — read it before adding a theme or a canvas-node component. The strict frontend theme RULES remain normative under "Frontend Design + Theme System (strict)" above.

### WebSocket-First Architecture
The project uses WebSocket as the primary communication method between frontend and backend, replacing most REST API calls:
- `src/contexts/WebSocketContext.tsx` - Central WebSocket context with request/response pattern
- `server/routers/websocket.py` - WebSocket endpoint; the live handler set is the `MESSAGE_HANDLERS` dict plus plugin-registered handlers via `services.ws_handler_registry`. Don't hand-maintain a count here.
- `server/services/status_broadcaster.py` - Connection management and broadcasting

**Canvas mutations from the backend** -- any handler that needs to add / move / delete nodes or edges (auto-add-skill on tool connect, Agent Builder runtime tools called by the LLM mid-execution, future workflow-template features) returns a workflow-ops batch (`{operations: [...]}`) and the frontend applies it through `applyOperations` in [client/src/lib/workflowOps.ts](./client/src/lib/workflowOps.ts). Backend builders live in [server/services/workflow_ops.py](./server/services/workflow_ops.py). Two delivery modes: request/response (frontend-driven, e.g. auto-skill) and push broadcast (`send_custom_event('workflow_ops_apply', ...)`, picked up by `useWorkflowOpsListener`). Full spec: [docs-internal/workflow_ops_protocol.md](./docs-internal/workflow_ops_protocol.md).

## Implemented Node Types

> **Authoritative source: backend plugin registry.** Glob [`server/nodes/**/*.py`](./server/nodes/) (excluding `_*.py` helpers and `__init__.py`) for the live count. The per-node descriptions below are reference material — they drift on every plugin add and should be cross-checked against [`server/nodes/README.md`](./server/nodes/README.md), the per-domain docs in `docs-internal/`, and the actual plugin classes before relying on any specific detail.

### Node Catalogue (collapsed)

> The authoritative, per-node reference is the backend plugin registry plus the per-node "logic-flow" cards under [docs-internal/node-logic-flows/](./docs-internal/node-logic-flows/) (one card per node, grouped by category, with handles / params / outputs / side-effects / edge-cases). Live node list = the plugin registry (one folder per node under `server/nodes/<group>/`; read the total from `len(services.node_registry.NODE_METADATA)` rather than hardcoding it here — a bare `__init__.py` glob overcounts by also matching the group packages). Do NOT maintain a per-node catalogue here — it drifts on every plugin add.

Node groups (palette categories): agent, model, skill, tool, trigger, workflow, search, google, android, whatsapp, whatsapp_business, telegram, discord, twitter, social, email, proxy, chat, scheduler, text, code, document, location, utility, browser, scraper, filesystem, stripe, vercel, github (palette group `vcs`), cloudflare and gcloud (palette group `deployment`), speech and translate (both palette group `language` — provider-abstracted `textToSpeech` / `speechToText` and `translateText` / `transliterateText` / `detectLanguage`; `nodes/sarvam/` was retired into them, and only `sarvamChatModel` under `nodes/model/` remains vendor-named). See [docs-internal/node-logic-flows/](./docs-internal/node-logic-flows/) for the card index.

## Backend Services

### Python Backend (FastAPI)
- **Port**: `PYTHON_BACKEND_PORT` (defaults in `.env.template`)
- **Base URL**: `http://localhost:${PYTHON_BACKEND_PORT}`
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

#### Workspace Router (`server/routers/workspace.py`)
- `GET /api/workspace/{workflow_id}/files/{path:path}` - Serve a file from a workflow's workspace.
  Range/seeking comes free from Starlette's `FileResponse` (`206` / `Content-Range` / `If-Range` /
  `416`) — do NOT wrap it in a `StreamingResponse`. Containment via `resolve_within`; 404 never 403
  (a distinct status would confirm what exists outside the workspace). **The `Content-Disposition`
  is not decided here** — it comes from `services.media.preview.serves_inline`, which the gallery
  listing also reads (via `preview_kind`) so the panel never offers a preview the route refuses to
  serve. Only `audio/ image/ video/` render inline; `NEVER_INLINE` is `{image/svg+xml, text/html,
  text/xml, application/xhtml+xml}`, because nodes can write arbitrary files into a workspace and
  inline markup from the app origin is stored XSS.
- **Listing is a WebSocket command, not an HTTP route.** `list_workspace_files` (owned by the
  gallery plugin) is the listing channel; these HTTP routes stay the *content* channel. The
  consumer is the parameter panel, which already holds an authenticated socket with request
  correlation — a second HTTP listing surface would mean a second auth path and a second error
  envelope for no gain.
- `POST /api/workspace/{workflow_id}/uploads` - Streamed multipart upload (the first in the repo on
  either side of the wire). Chunked read with a running total — `Content-Length` is never trusted —
  413 past `MEDIA_MAX_UPLOAD_BYTES`. Returns an `AudioRef`, which `coerce_file_param` accepts.
- **The URL carries `workflow_id`; the directory is named by `Workflow.slug`.** The router owns that
  lookup because it needs the database, while `services.media` stays synchronous. See
  [Media Transport](./docs-internal/media_transport.md).

#### Workflow Services (`server/services/workflow.py`)
- Node execution dispatches every registered plugin via the `BaseNode` registry (one self-contained folder per node under `server/nodes/<group>/`; live total via `len(services.node_registry.NODE_METADATA)` — do not hardcode it, and note a bare `__init__.py` glob overcounts by also matching the group packages)
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

Workflows execute via Temporal for durability and horizontal scaling, gated by `TEMPORAL_ENABLED`. Three dispatch paths exist — legacy single `execute_node_activity` (fallback), per-type `node.{type}.v{version}` activities (F4.A, `TEMPORAL_PER_TYPE_DISPATCH=true`), and Agent-as-child-workflow (F4.B, `TEMPORAL_AGENT_WORKFLOW_ENABLED=true`) — with `rlm_agent`/`claude_code_agent` always bypassing AgentWorkflow. **Per-queue activity routing is production-default (Wave 16)**: a `TemporalWorkerPool` runs one activity-only worker per plugin-declared `cls.task_queue` (ai-heavy / browser / code-exec / rest-api / messaging / ...) with per-queue concurrency + rate limits + resource-based tuning for ai-heavy/browser; rollback via `TEMPORAL_WORKER_POOL_ENABLED=false`. Resilience knobs (Waves 17-18): `DEPLOYMENT_MODE` (local halves concurrency; sizes the sticky cache), unlimited-with-backoff retry on `agent.execute_llm_step` (terminal provider errors are marked non-retryable; `services/temporal/_retry_policies.py`), observability interceptors (`activity_retry` WARN on re-dispatch), 30s periodic heartbeat during long activity bodies, cron `catchup_window=24h`, poller autoscaling. **Conditional edges are evaluated on this path too, as of the `machina-conditional-edges-v1` patch.** They previously were not: `MachinaWorkflow._find_ready_nodes` scheduled purely on dependency completion, so a condition set in the editor rendered an edge label and then did nothing once execution routed through Temporal — silently, with both branches running. The skip mirrors `WorkflowExecutor._evaluate_incoming_conditions` exactly rather than improving on it: **OR-any** across a node's conditional incoming edges, and **skipping is not transitive** (a skipped node counts as completed, matching `get_completed_nodes` including `SKIPPED`, so an unconditional downstream node still runs). `evaluate_condition` is imported from `services.execution.conditions` **directly, never via the package `__init__`**, which also pulls in the executor / cache / recovery sweeper / DLQ; the module itself is pure (`re` + comparisons, no IO, no clock, no randomness) so it is safe to evaluate inside a workflow. The `workflow.patched(...)` gate is mandatory — the skip changes which activities get scheduled, so an ungated change would break replay for in-flight workflows. Locked by `tests/temporal/test_conditional_edges.py`, whose patch-closed case asserts the pre-fix command sequence is still reproduced verbatim.

Execution routing falls back Temporal → sequential; the Temporal dev server + embedded worker are managed in-process under `server/services/temporal/` (pooch-downloaded CLI, single `temporal server start-dev` process, gRPC `TEMPORAL_FRONTEND_GRPC_PORT` + Web UI `TEMPORAL_UI_PORT`, SQLite at `~/.opencompany/temporal.db`). Full architecture, dispatch matrix, per-node + agent-loop lifecycle, the F4.B `agent.*` activities (unsuffixed since the V1/V2 fold; read the live set from `collect_agent_activities()` rather than a hardcoded count), heartbeat semantics, delegation input contract, the worker tuning recipe, and all `.env` tunables live in [docs-internal/TEMPORAL_ARCHITECTURE.md](./docs-internal/TEMPORAL_ARCHITECTURE.md).

**Plugin failures reach Temporal (September 2026).** `BaseNode.as_activity` and the legacy `execute_node_activity` raise a typed `ApplicationError` for a structured `{success: False}` envelope (`type` = the envelope's `error_type`, `non_retryable` from its `retryable` verdict and the attempt cap, the envelope as `details[0]`), so per-plugin `retry_policy` and `NON_RETRYABLE_ERROR_TYPES` finally apply on the deployed path; before this the envelope was returned as a successful completion and every retry policy was dead configuration. Load-bearing rules: (1) the verdict is classified at the source in `services/plugin/retryability.py` and stamped by `_wrap_error` as `retryable` (`NodeUserError`, validation, credential, invalid-parameters, output-contract and 4xx except 408 / 425 / 429 are permanent; 5xx, timeouts, connection errors and unknown exceptions are transient; a boolean `retryable` attribute on the exception or its `__cause__` wins, which is how a `NodeUserError` from `unifier.py` wrapping a rate-limited `LLMError` still retries). Temporal vetoes by `type` name before it reads `non_retryable`, so a retryable failure carrying a non-retryable name is raised as `<type>.retryable`. (2) `BaseNode.effective_retry_policy()` decides attempts: a class-declared `retry_policy` wins; triggers and mutating nodes (`annotations` `destructive` or `readonly: False`, or no annotations) get one attempt; `readonly: True` keeps three, so `annotations.readonly` now means "safe to re-execute on a transient failure" and `tests/fixtures/effective_retry_attempts_snapshot.json` pins the result per node type. (3) The activity self-caps from `activity.info()`: `max_attempts = min(scheduled policy, effective policy)`, a pre-body refusal (`RetryAttemptsExhausted`) stops any re-dispatch past the cap before a side effect can repeat, and only the final attempt broadcasts `error` (earlier attempts stay `executing` with `attempt` / `max_attempts` / `last_error`; no new status string). (4) `NodeContext.attempt` and `NodeContext.idempotency_key` (`f"{workflow_run_id}-{activity_id}"`) let a node that opts into retries make its writes idempotent. (5) Workflow-side consumers unwrap the `ActivityError` cause with `services/temporal/_failures.activity_failure_envelope` so `errors[]`, the pause-on-failure reason and the LLM tool message keep the plugin text; the effective policy on MachinaWorkflow node activities and on AgentWorkflow tool calls (which previously had no policy at all, Temporal's unlimited default) is gated by `machina-plugin-failure-retries-v1`. Rollback is `TEMPORAL_PLUGIN_FAILURE_RETRIES=false`. Locked by `tests/temporal/test_activity_failure_boundary.py` (on `temporalio.testing.ActivityEnvironment`), `tests/test_failure_classifier.py`, `tests/test_effective_retry_policy.py`, `tests/temporal/test_machina_failure_unwrap.py`, `tests/temporal/test_agent_tool_retry_policy.py`, and the tool-call scenario in the SDK replay gate.

**Months-long durability contract (July 2026).** Running and paused deployments survive backend restarts, are never auto-terminated, and keep executing for months. Load-bearing invariants: (1) `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` stays `false` (debug-only sweep; even enabled, any control row in the shared `WORKFLOW_CONTROL_ACTIVE_STATES` — including `resetting` — vetoes it). (2) New child workflow starts carry **no** `execution_timeout`/`run_timeout` — Temporal's timeout timers keep ticking through a cooperative pause, so lifetime caps silently terminated paused runs; liveness comes from activity heartbeats (30s beat / 2min timeout), never from workflow lifetime caps. Removing/adding start options or retry policies on recorded commands requires a `workflow.patched(...)` guard (see the `*-unbounded-*` / `*-history-bounded-can-*` markers, and `machina-plugin-failure-retries-v1` for the node and tool-call retry policies). (3) Every long-lived workflow loop must continue-as-new under history pressure (`is_continue_as_new_suggested()` + a soft cap) — Temporal hard-terminates around ~51,200 history events; `WorkflowControlWorkflow` carries triggers/queued events/seen-ids/control state across rollovers, so controller handles are addressed **by workflow id only, never run_id-pinned**. (4) `dispatch.emit` skips controllers whose `ControlEventTypes` Search Attribute doesn't match the event (absent attribute = legacy match-all). (5) The Temporal lifecycle is owned by [services/temporal/lifecycle.py](./server/services/temporal/lifecycle.py) (`main.py` only schedules it): dev-server supervision + resident watchdog (`TEMPORAL_HEALTH_MONITOR_INTERVAL_SECONDS`), worker crash-restarts that REBUILD the single-use SDK `Worker` per attempt (manager + every pool queue worker), and the boot-time `reconcile_active_controls_on_boot` pass (converges crash-stranded `starting` rows, re-arms running/paused generations from the persisted graph snapshot). (6) **Recovery policies** (env-driven, defaults in `.env.template`; full semantics in [temporal-workflow-control.md → Recovery policies](./docs-internal/temporal-workflow-control.md)): `WORKFLOW_CONTROL_CRASH_RECOVERY=pause` — after a kill/crash (dirty-bit marker cleared only by graceful teardown) boot recovers running generations as **paused** so the user consciously resumes; `WORKFLOW_CONTROL_MISSING_CONTROLLER=pause` — a killed/vanished controller converges the generation to paused and **Resume rebuilds the controller** (`WorkflowIdConflictPolicy.USE_EXISTING`, re-arm from snapshot) instead of forcing a state-archiving Reset; `WORKFLOW_CONTROL_PAUSE_ON_FAILURE=true` — repeatedly-failing trigger-spawned runs pause their deployment via the `workflow_control.pause_on_failure.v1` activity (circuit breaker; trips only after `WORKFLOW_CONTROL_PAUSE_ON_FAILURE_THRESHOLD` (default 3) failures inside a rolling `_WINDOW_SECONDS` (default 600) so one node hiccup never pauses anything; Resume resets the streak; manual canvas runs never qualify). Temporal's native Pause/Unpause (server 1.28+) is deliberately NOT used — no Python SDK surface, and it halts workflow-task dispatch so a natively-paused controller couldn't process the resume Update. (7) **Workflow-scoped event delivery**: an envelope whose `workflow_id` is set by its producer's call site reaches only that workflow's consumers (`dispatch.emit` narrows both Visibility clauses by `EventWorkflowId`); chat is the first scoped producer — the decision lives in `routers/websocket.py`, the narrowing in core dispatch, and plugin `_events.py` factories only plumb the field (see [event_framework.md](./docs-internal/event_framework.md)). (8) **Canvas editability is the server-owned `can_edit` capability** emitted by `serialize_control` (paused = editable; starting/running/transitional = locked); the FE renders it via `lib/canvasLock.ts` + Dashboard's `guardCanvasEdit` and never re-derives the rule from state strings (see [temporal-workflow-control.md → Canvas editability](./docs-internal/temporal-workflow-control.md)). Locked by `tests/temporal/test_durability_hardening.py` + `test_worker_restart.py`.

**Temporal tracing ownership is strict.** Register the SDK `TracingInterceptor` exactly once on the shared `Client.connect(...)`; the Python SDK automatically prepends compatible client interceptors to every worker. Never repeat `TracingInterceptor` in `Worker(..., interceptors=...)`. Worker lists contain the distinct `ObservabilityWorkerInterceptor` and plugin-specific worker interceptors only. Exact paired `StartActivity` / `RunActivity` / `CompleteWorkflow` console spans with identical Temporal identities indicate duplicate instrumentation, not duplicate execution; use Temporal Event History and the `node.<type>.execute` application span to verify actual attempts. See the tracing and debugging sections in [TEMPORAL_ARCHITECTURE.md](./docs-internal/TEMPORAL_ARCHITECTURE.md) and [errors.md](./docs-internal/errors.md).

## Development Commands

**Package manager: bun** (pinned via `packageManager: bun@1.4.0` in the root package.json). Source checkouts are gated to bun by `scripts/preinstall.js` (keys on `bunfig.toml` + a `bun` user agent) — `npm install` in the repo is rejected with a pointer to https://bun.sh; end-user installs of the published npm package are unaffected. `bunfig.toml` pins the **isolated linker** (pnpm-style symlinked `node_modules` with the store at `node_modules/.bun/`), the lockfile is the tracked text `bun.lock`, and Node 22 remains the runtime — bun only installs and runs scripts, never executes tools with `--bun`. Security pins live in the top-level `overrides` block (version-ranged keys). See [CONTRIBUTING.md](./CONTRIBUTING.md) → "Known differences from pnpm" for the guards that changed in the 2026-08 pnpm→bun migration.

### CLI Commands (after the global install)
```bash
company start        # Start all services (production mode)
company dev          # Start all services in dev mode (Vite HMR)
company dev --force  # ...forcing Vite to re-bundle deps (recovers "Outdated Optimize Dep"; sets VITE_FORCE -> optimizeDeps.force — the dep cache is otherwise preserved across boots)
company stop         # Stop all services
company build        # Build for production
company clean        # Clean build artifacts
company help         # Show all commands
```

### npm Scripts
```bash
# Core (thin wrappers over the Python CLI)
npm run start            # Start all services (python -m cli start)
npm run stop             # Stop all services (python -m cli stop)
npm run build            # Build for production (python -m cli build)
npm run clean            # Clean build artifacts (python -m cli clean)
npm run deploy           # Self-deploy to a cloud VM (python -m cli deploy)
```

### Cross-Platform Scripts
Service orchestration lives in the Python CLI (`company start/dev/stop/build/clean/serve/daemon/deploy/docs/version` — see `cli/`). The `scripts/` directory retains only the npm install lifecycle helpers (`install.js`, `preinstall.js`, `postinstall.js`). `company start` is single-port: uvicorn serves API + WS + built SPA on the backend port (`SERVE_STATIC_CLIENT`, default on); the retired `scripts/serve-client.js` static server and its `:3000` frontend port are gone.

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
✅ **Development Server**: the app is **`http://localhost:${PYTHON_BACKEND_PORT}`** in every mode (defaults in `.env.template`). `company dev` = Vite HMR on that port proxying /api /ws /webhook to the backend (dev override in `.env.dev`); `company start` (production) = uvicorn alone on it (API + WS + SPA)
✅ **WebSocket Integration**: Persistent WebSocket connections for remote Android devices with background tasks and message queue
✅ **Real-time Status WebSocket**: Frontend-backend WebSocket at `/ws/status` for live Android status, node status, and variable updates
✅ **Event-Driven Trigger Nodes**: WhatsApp Receive and Webhook Trigger with asyncio.Future-based event waiting, filter builders, and cancel support
✅ **Continuous Scheduling Execution**: Temporal/Conductor pattern using `asyncio.wait(FIRST_COMPLETED)` for true parallel pipelines where dependent nodes start immediately when their specific dependency completes
✅ **Event-Driven Deployment**: n8n-style architecture where each trigger event spawns an independent, concurrent execution run (no iteration loop)
✅ **HTTP/Webhook Nodes**: HTTP Request for external APIs, Webhook Trigger for incoming requests, Webhook Response for custom responses
✅ **Theme System**: Neutral-slate (dark) + grey-blue paper (light) surfaces with Dracula accent palette, dark mode support, vibrant action buttons, and themed React Flow edges
✅ **Modular Backend Architecture**: workflow.py refactored from 2068 lines into a facade (~840 lines today) over NodeExecutor, ParameterResolver, and DeploymentManager modules
✅ **Node Rename System**: n8n-style node renaming via F2 keyboard shortcut, double-click on label, or right-click context menu with inline editing
✅ **UI State Persistence**: localStorage persistence for sidebar visibility, component palette visibility, dev mode, and collapsed sections
✅ **Normal/Dev Mode**: Toggle in toolbar to filter Component Palette - Normal mode shows only AI Agents, Models, and Skills; Dev mode shows all categories
✅ **Production Deployment**: Docker Compose deployment (4 containers: Redis, Backend, Frontend, WhatsApp), nginx reverse proxy, and Let's Encrypt SSL
✅ **Authentication System**: n8n-style JWT authentication with HttpOnly cookies, single-owner and multi-user modes, rate-limited login. Note `AUTH_MODE=multi` authenticates but does NOT isolate data — see Known Limitations in [authentication.md](./docs-internal/authentication.md)
✅ **Cache System**: n8n-pattern cache with Redis (production) / SQLite (local dev) / Memory fallback hierarchy
✅ **AI Thinking/Reasoning**: Extended thinking for Claude, Gemini 2.5/3, OpenAI GPT-5/o-series, Groq Qwen3 with output available in Input Data & Variables for downstream nodes
✅ **Onboarding Service**: 4-step welcome wizard with shadcn UI, database persistence, skip/resume/replay support
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
        ↓                           └── StartNode.tsx
   Coordinates which node               ↓
   is currently being renamed       Local State:
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
The Console Panel provides a resizable bottom panel with three sections: Chat (AI conversation), Console (node execution logs), and Terminal (server logs). **Hybrid layout (design-handoff)**: split view (default) docks Chat as a resizable pane beside the Console/Terminal tabs so chat and logs stay simultaneously visible during agent runs; tab mode makes Chat the first of three tabs. Toggled via the Columns2 button in the tab row; persisted in `consolePrefs.splitView` (default `true` — existing users see no change). One shared `chatSection` JSX serves both layouts so the per-theme `chat-msg*` decoration co-classes survive.

#### Features
- **Resizable**: Drag handle at top to resize (vertical) + chat-pane divider (horizontal, split view), persisted to localStorage
- **Hybrid Chat placement**: docked split pane (default) or first tab — `consolePrefs.splitView`
- **Tabs**: Chat (tab mode only) / Console / Terminal
- **Chat Section**: Send messages to Chat Trigger nodes, view conversation history; `chatFocusRequest` switches to the Chat tab before focusing when in tab mode
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
Each workflow execution gets a persistent workspace directory where nodes save output files and agents such as Coding Agent access them through connected filesystem tools.

**Directory**: `~/.opencompany/workspaces/<workflow_slug>/` (Wave 14 — keyed by the human-readable slug, not the UUID; see "Workflow naming" below).

**Configuration** (`server/core/config.py`):
```python
workspace_base_dir: str = Field(default="workspaces", env="WORKSPACE_BASE_DIR")  # resolved under DATA_DIR -> ~/.opencompany/workspaces/
```

**How it works:**
- `workflow.py` creates the workspace dir and injects `workspace_dir` into the execution context. The dir name is the `workflow_slug` resolved from the DB (falls back to `"default"` for one-off Runs without a saved row).
- `fileDownloader` saves to `{workspace_dir}/downloads/` by default
- Code executors (Python/JS/TS) receive `workspace_dir` in their execution namespace
- The `fileRead`, `fileModify`, `fsSearch`, `shell` and `gallery` nodes use the native `WorkspaceBackend` rooted at `workspace_dir`; Coding Agent can invoke the first four when they are connected as tools. **`gallery` is deliberately `usable_as_tool = False`** — it carries destructive operations, and `fsSearch` + `fileModify` already cover what an agent needs, so shipping an editor panel must not hand every agent a delete tool as a side effect
- `WorkspaceBackend` resolves and validates paths beneath that root, rejects traversal and symlink escapes, and uses per-path locks plus atomic replacement for writes
- Rename follows the workflow: when the user renames a workflow, `save_workflow` recomputes the slug and `os.rename`s the workspace dir to match (existing files preserved).

**Key Files:**
| File | Description |
|------|-------------|
| `server/core/config.py` | `workspace_base_dir` setting |
| `server/services/workflow.py` | `_get_workspace_dir()`, injects into context |
| `server/nodes/document/file_downloader/` | `fileDownloader` saves to the workspace |
| `server/nodes/code/` | `workspace_dir` available in Python/JS/TS executors |
| `server/nodes/filesystem/_backend.py` | Native contained workspace filesystem implementation |

### Workflow Naming (Wave 14)
The workflow record carries three identity fields with strict separation:

| Field | Carrier | Stable? | Surfaces |
|---|---|---|---|
| `Workflow.id` | opaque 32-hex UUID (`uuid.uuid4().hex`) | yes — never changes on rename | FK target (`Execution.workflow_id`), `EventWorkflowId` Search Attribute in Temporal Visibility, legacy `WorkflowEvent.workflow_id` compatibility field, `log_context(workflow_id=...)`, Redis cache keys, `DeploymentManager._deployments` dict key, frontend `useAppStore.currentWorkflow.id` |
| `Workflow.name` | free-form display ("AI Assistant") | mutable | sidebar, parameter panel, exported JSON |
| `Workflow.slug` | `<Sanitized_Name>_<N>` (`AI_Assistant_1`) | mutable, recomputed on rename | `~/.opencompany/workspaces/<slug>/`, Temporal workflow IDs (visible in Temporal Web UI), cron Schedule IDs, export filenames |

Single source of truth: [`server/services/workflow_naming.py`](./server/services/workflow_naming.py) — `slugify_name` (via `python-slugify` for Unicode transliteration, emoji strip, case preservation, length cap), `next_available_slug(name, database, *, exclude_id=None)` (fill-gap counter; pass `exclude_id=workflow_id` on rename so the row doesn't bump itself), `new_workflow_id()` (bare hex UUID), `node_label_slug(node)` (sandbox-safe stdlib slug from `node.data.label` or `node.type`, used inside Temporal `@workflow.defn` modules where `python-slugify` can't import safely).

**Temporal workflow ID convention** — uniform `<workflow_slug>-<node_label>` shape across every workflow type. The Temporal Web UI's "Workflow Type" column already distinguishes the kind (TriggerListenerWorkflow / PollingTriggerWorkflow / CronTriggerWorkflow / AgentWorkflow / MachinaWorkflow), so no middle `-trigger-` / `-agent-` tag in the id.

| Surface | Format | Example |
|---|---|---|
| Trigger listener (push/poll) | `<slug>-<trigger_label>` | `AI_Assistant_1-chatTrigger` (or `AI_Assistant_1-Customer_Inbox` after F2 rename) |
| Per-firing run (child of listener) | `<slug>-<trigger_label>-<event_id>` | `AI_Assistant_1-chatTrigger-evt-abc` |
| Cron Schedule | `<slug>-<trigger_label>` | `AI_Assistant_1-cronScheduler` |
| Cron firing (per-tick child) | `<slug>-<trigger_label>-<ScheduledStartTime>` | `AI_Assistant_1-cronScheduler-2026-05-27T12:00:00Z` |
| Agent child workflow | `<root_temporal_workflow_id>-agent-<node_id>` | `AI_Assistant_1-Chat_Trigger-<event>-agent-1:aiAgent:1`. Uses the IMMUTABLE node id and does carry a middle `-agent-` tag, unlike every other row. The label-derived `<slug>-<agent_label>` form survives only for histories recorded before the `machina-agent-child-id-v2` patch. |
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
- `server/services/workflow.py`: Thin facade (~840 lines) delegating to specialized modules
- `server/services/node_executor.py`: Single node execution with registry-based dispatch
- `server/services/parameter_resolver.py`: Template variable resolution (`{{node.field}}`)
- `server/services/deployment/manager.py`: Deployment lifecycle, spawn runs, cancel
- `server/services/deployment/triggers.py`: Cron and event trigger management
- `server/services/deployment/state.py`: DeploymentState, TriggerInfo dataclasses
- `server/services/execution/models.py`: `ExecutionContext.create()` with `_pre_executed` support
- `server/services/execution/executor.py`: Continuous scheduling with `asyncio.wait(FIRST_COMPLETED)`

### AI Chat Model System (5-Layer Architecture)

Chat-model nodes (`openaiChatModel`, `anthropicChatModel`, ...) render through `SquareNode` from the backend NodeSpec. Direct chat and every new agent execution route through the native SDK facade (`ChatUnifier` in `services/llm/`); the 13-provider surface is 11 cloud providers plus Ollama and LM Studio. Twelve providers have standalone chat-model nodes under `server/nodes/model/`, while xAI is selected directly from agent parameters. Model params (max output, context length, thinking type, temperature range) come from `ModelRegistryService`. The provider architecture, proxy/local-LLM routing, and the per-provider model + thinking/reasoning matrix (budget / effort / format) all live in **[Native LLM SDK](./docs-internal/native_llm_sdk.md)**. A Temporal history recorded before the native cutover carries no `llm_engine` marker and messages in a retired wire format; `agent.execute_llm_step` refuses it with a non-retryable `InvalidAgentLLMEngine` rather than misreading it, and the deployment must be Reset. The runtime output schema (`thinking` field for downstream nodes) is backend-served via `register_output_schema` (see [Plugin System](./docs-internal/plugin_system.md)).

## AI Agent Node Architecture

AI Agent (`aiAgent`) and Chat Agent / Zeenie (`chatAgent`) both use the plain-async `run_native_agent_loop` in `server/services/agent_runtime.py` and support memory / skills / tools / task input. `AIService.execute_agent()` and `execute_chat_agent()` prepare the same canonical native messages and `AgentToolSpec` values, then the loop appends each lossless assistant message before executing its tool calls, hot-rebinds the tool surface after canvas mutations, and stops when the model returns no tool calls (with `max_iterations` as a safety cap). Connection collection is `collect_agent_connections` in `server/services/plugin/edge_walker.py` (5-tuple: memory, skill, tool, input, task); the pre-Wave-11 `handle_ai_agent`/`handle_chat_agent` handlers are gone (dispatch is per-plugin `execute_op` under `server/nodes/agent/<plugin>/__init__.py`). `_run_agent_loop` remains only for replaying pre-cutover or explicitly legacy-pinned Temporal histories.

**`max_iterations` precedence (highest->lowest): per-node `parameters.max_iterations` > `UserSettings.agent_recursion_limit` > env `AGENT_RECURSION_LIMIT` (default 200) > `llm_defaults.json:agent.recursion_limit`.**

Full reference — agent loop, skill injection, tool building, input/auto-prompt fallback (message>text>content>str), handle topology, spec-driven `AIAgentNode`, durable Task Manager delegation (Temporal child workflows plus the legacy bridge), and specialized-agent routing — in [docs-internal/agent_architecture.md](./docs-internal/agent_architecture.md); low-level compatibility mechanics in [agent_delegation.md](./docs-internal/agent_delegation.md), and the authoritative team-lead contract in [agent_teams.md](./docs-internal/agent_teams.md).

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
curl -I http://localhost:${PYTHON_BACKEND_PORT:-5678}

# TypeScript validation — the gate is TypeScript 7 (native Go) at the REPO ROOT.
bun run typecheck                               # root: tsc --noEmit -p client/tsconfig.json
bun run --filter react-flow-client typecheck    # what CI runs; delegates up to the same gate
# NOT `npx tsc --noEmit` — there is no root tsconfig.json.
# NOT client's `typecheck:tsc` — that resolves typescript@5.9.3, kept only because
# typescript-eslint's peer range excludes 6/7. It is a second opinion, not the gate.

# Build verification
npm run build
```

## Production Deployment

### Self-Deploy CLI (`company deploy`) — current path
One command provisions a login-gated OpenCompany VM on a cloud provider. Two stages: the
operator's **cloud CLI** (gcloud; aws planned) handles auth + project/region/zone resolution +
ADC verification + API enablement, then **Terraform** (`cli/terraform/gcp/`) owns all resources —
VM (new deployments use the `opencompany` resource id), firewall, artifact bucket (local `npm pack` source), service
account, and a cloud-init startup script that installs Node 22 + uv + the package and runs
`company serve` under systemd. Login gate = built-in auth (`VITE_AUTH_ENABLED=true`,
`AUTH_MODE=single`) with the owner credential generated at deploy time and seeded on first boot.
`build_app_env` (`cli/commands/deploy/_secrets.py`) also sets `DEPLOYMENT_MODE=cloud` on
deployed VMs and mints fresh `JWT_SECRET_KEY` / `SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` per deploy.

```bash
company deploy up --provider gcp --owner-email you@example.com   # provision + install + print URL/creds
company deploy status                                            # URL + /health
company deploy destroy                                           # terraform destroy + clear state
```

Key files: `cli/commands/serve.py` (single-port runtime: uvicorn fronts API + WS + built SPA, plus
the node sidecar), `cli/commands/deploy/` (verbs, secrets, Terraform driver, provider CLI adapters),
`cli/terraform/gcp/` (HCL module + `startup.sh.tftpl`). Deployment state lives at
`<user-data>/deploy/opencompany/` (preserved by `company clean` — see `_OPENCOMPANY_KEEP`); only
`company deploy destroy` removes it. The deploy code is fully delinked from `company build` /
`company clean` (lazy verb stubs in `cli/cli.py`; nothing in the build pipeline imports it).
For upgrades, `_state.py` also discovers pre-rebrand `deploy/machinaos/` state
under the configured root, `~/.machina`, or `<repo>/.machina`. Such deployments
retain their durable `machinaos` cloud/systemd id; changing it would cause
Terraform replacement or strand live state. Fresh deployments use `opencompany`.
The `machina` executable remains only as a deprecated legacy alias; use `company`
for all new commands and automation.

The legacy `deploy.sh` (docker-compose images over SCP to a GCE box) was removed. The Docker
Compose notes below are retained for reference for the historical container topology.

### Docker Deployment (legacy reference)

The historical Docker Compose topology (4-container stack: redis / backend / frontend / whatsapp, nginx reverse proxy, dev + prod compose files, env config, resource usage, useful commands) and the `npm run build` / `npm run preview` local-build commands are preserved in **[Deployment (legacy reference)](./docs-internal/deployment_legacy.md)**.

## Authentication System

n8n-style JWT auth in HttpOnly cookies. `VITE_AUTH_ENABLED=false` bypasses login (anonymous owner, dev); when enabled, `AUTH_MODE=single` (first user = owner, registration then closed) or `multi` (open registration). Backend: `User` (bcrypt) + `UserAuthService` + `/api/auth/*` router + `AuthMiddleware` (public-path allowlist + `/webhook/` prefix). `/login` and `/register` are throttled by the in-process `core/rate_limit.py` sliding window (`AUTH_RATE_LIMIT_*`; no `slowapi` dependency — the primary runtime is a single uvicorn worker). Frontend: `AuthContext` (TanStack-Query bootstrap with full-jitter backoff, plus `useMutation` for login/register/logout) + `ProtectedRoute` + `LoginPage` (RHF + zod via the shadcn `Form` primitives); all API calls send `credentials: 'include'`, and the WebSocket refuses to connect without the cookie.

**Two context distinctions that are load-bearing, not stylistic:** `isLoading` is the bootstrap query (gates the whole app in `ProtectedRoute`) while `isSubmitting` is per-request — using the former to disable the login form disables nothing, since it has already settled by the time that form renders. And `error` means "cannot reach the server" while `submitError` is the server's own rejection text; a `setQueryData` write is a *success* value, so routing a login failure through the status cache surfaces no error at all and additionally collapses `can_register`.

**Read the Known Limitations section of [authentication.md](./docs-internal/authentication.md) before building on this.** `AUTH_MODE=multi` provides authentication but no data isolation — `request.state.user_id` is written by the middleware and read by nothing, so all users share one workflow store and one credential store. There is also no CSRF token and no token revocation (`User.is_active` is the only lever).

**Load-bearing rule:** JWT handling uses **PyJWT** (`import jwt`, HS256 with `Settings.jwt_secret_key`). Do NOT reintroduce `python-jose` — it drags in pure-Python `ecdsa` with the unpatchable Minerva timing-attack advisory (GHSA-wj6h-64fc-37mp). Full reference (models, router, middleware, config, startup retry, key files, deps) in **[Authentication](./docs-internal/authentication.md)**.

## Encrypted Credentials System

API keys and OAuth tokens live in a separate encrypted database (`credentials.db`): Fernet (AES-128-CBC + HMAC-SHA256) with the key derived via PBKDF2HMAC-SHA256 (600K iterations, OWASP-2024) from the server-scoped `API_KEY_ENCRYPTION_KEY` (`.env`) + a salt stored in `credentials.db`, initialized at startup and held for the process lifetime. Two distinct systems that never cross: API keys (`store_api_key`/`get_api_key` → `EncryptedAPIKey`) and OAuth tokens (`store_oauth_tokens`/`get_oauth_tokens` → `EncryptedOAuthToken`). Multi-backend (Fernet default / Keyring / AWS Secrets Manager) via `CREDENTIAL_BACKEND`.

**Load-bearing rule:** every credential operation MUST go through `AuthService` (`from core.container import container; container.auth_service()`); routers must NEVER touch `CredentialsDatabase` directly. Full pipeline, cache contract, backends, config, key files, and design decisions are in **[Credentials Encryption](./docs-internal/credentials_encryption.md)**.

## Example Workflows

### Overview
Example workflows are pre-built workflow templates that auto-load on first use. They provide users with starting points to explore the platform's capabilities. Seeds live as JSON files at **`<repo>/.opencompany/workflows/`** — the only git-tracked content under `<repo>/.opencompany/` (everything else there is `.gitignore`d as runtime state). The path is also preserved by `company clean` (see `_OPENCOMPANY_KEEP` in `cli/commands/clean.py`).

### Architecture
```
<repo>/.opencompany/workflows/        # Shipped seed JSONs (git-tracked)
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
2. **Auto-Import**: If `examples_loaded=false`, calls `example_loader.get_example_workflows()` which reads from `core.paths.example_workflows_dir()` = `<repo>/.opencompany/workflows/`
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
| `.opencompany/workflows/*.json` | Shipped seed workflow JSONs (git-tracked) |
| `server/core/paths.py` | `example_workflows_dir()` → `<repo>/.opencompany/workflows/` (fixed path, NOT under `DATA_DIR`) |
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
2. Copy the JSON file to `<repo>/.opencompany/workflows/` (git-tracked seed location)
3. Edit the `id` and `name` fields as needed
4. Delete `~/.opencompany/workflow.db` (or set `examples_loaded=false` in DB)
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

A new chat-model provider is one self-contained folder under `server/nodes/model/<provider>_chat_model/` with `__init__.py` declaring a `ChatModelBase` subclass (auto-registers via `BaseNode.__init_subclass__`; the frontend renders it through `SquareNode` from the NodeSpec with zero TS changes). Credentials live in `server/nodes/model/_credentials.py`. The full recipe — native provider registration under `services/llm/providers/`, `llm_defaults.json` configuration, the `_COMPAT_PROVIDERS` entry required for OpenAI-compatible endpoints, and the per-provider implementation-file map — is in **[Native LLM SDK](./docs-internal/native_llm_sdk.md)**.

## Context and Memory (RFC-0002)

Two different things, deliberately separated. Read [RFC-0002](./RFC-0002-AGENT-CONTEXT-AND-MEMORY.md) before touching either.

**Context** is the plain conversation store — what the agent actually sent and received, as JSON messages. One table (`agent_conversations`, [models/agent_context.py](./server/models/agent_context.py)) keyed by **`(workflow_id, generation, agent_node_id)`** → `messages` (MessageWire list) + `updated_at`; the service is four functions in [`services/agent_context/conversation.py`](./server/services/agent_context/conversation.py) (`load_conversation` / `save_conversation` (per-key lock, whole-list upsert, post-commit notify) / `clear_conversation` / `list_conversations`). Every firing of an agent — chat message or taskTrigger review — loads the row at run start and saves the live message list back per turn, so the one conversation continues across firings; Reset admits a new generation = new key = fresh conversation, AND the Context node's `reset_execution_state` clears the workflow's stored rows + fences warm claude subprocesses + broadcasts `context.updated` — the generation bump alone is not enough because the panel shows the newest STORED generation, so surviving rows would render the pre-Reset conversation as the live context and Reset would look like a no-op. (A plain Stop→Start leaves prior generations as inert, non-browsable history until workflow delete or Reset.) The `context` node (`server/nodes/context/`, `input-context` handle) is the **opt-in switch and viewing panel**, nothing more — it declares no parameters. This is the LangGraph `thread_id` / OpenAI `conversation_id` pattern; the previous hash-chained journal (threads, epochs, checkpoints, blobs, provider bindings, session/task/execution thread routing) is deleted.

Load-bearing invariants, learned the hard way: (1) *attaching a Context node must never change what the agent sends* — requests are always built from `messages`; `conversation_key` only says where to save. (2) *The store records what was sent, after it was sent* — `agent.execute_llm_step` saves `[...sent, assistant]` immediately after `ChatUnifier.chat` returns; nothing writes before a request exists (the retired `prepare_context` activity did, and journalled fabricated requests). (3) **Loads are LOUD, saves are best-effort** — `agent.prepare_payload` raises `ApplicationError("ConversationLoadFailed")` (retryable) / `ApplicationError("ConversationTooLarge")` (non-retryable, 1 MB cap) rather than silently running an amnesiac agent that burns tokens on an empty prompt; the save runs post-billing and swallows failures with a WARN. (4) Seeding precedence is **carried rollover transcript > stored conversation > bare build** — a `continue_as_new` resume carries the live transcript in its resume marker (size-guarded ≤ 1 MB) and must win over the store. Locked by `tests/temporal/test_agent_workflow.py::TestConversationIdentity`.

Both paths implement the same contract: Temporal via `prepare_agent_payload` (load) + `_save_conversation` in the LLM activity; in-process via `AIService._prepare_context` → `_AgentContextRuntime{key, history, database}` + the loop's `conversation_saver=runtime.save` hook. Specialized providers (claude_code / codex / rlm / vertex) use `SpecializedAgentContextBridge` ([services/cli_agent/context_bridge.py](./server/services/cli_agent/context_bridge.py)): `resolve` loads, `augment_prompt` renders the transcript into the prompt, `record_turn(original_prompt, response)` saves — always the ORIGINAL prompt, never the augmented one, or the save nests the transcript inside itself. The claude pool key is the conversation key (`(workflow_id, agent_node_id, generation)`), so a Reset's generation bump fences warm subprocesses at `acquire`; a same-generation panel Clear calls `ClaudeSessionPool.terminate_conversations`. Manual canvas Runs persist nothing (`generation > 0` gate — only Start admits a generation).

**Live viewing is emitted at the persistence boundary, not per caller.** `save_conversation` is the one place every writer passes through, and it announces durable saves through the fanout registry in [`services/agent_context/listeners.py`](./server/services/agent_context/listeners.py) — `register_conversation_listener` / `notify_conversation_saved` — with `nodes/context/__init__.py` registering the broadcaster. Same shape as the plugin registries, same reason: the store must never import `nodes/`. The notification fires after the commit, and a listener can never fail a save (failures log at WARNING — a silently failing listener is exactly how "the panel never updates live" becomes undiagnosable). The single Context event (`context.updated`) **broadcasts directly via `get_status_broadcaster()`, not through `services.events.dispatch.emit`** — no canary consumer exists for `com.opencompany.context.*`, so `emit`'s Visibility query would match nothing once per save. Payload is identity + count only; the panel refetches through the authorized `get_agent_context` WS handler (`clear_agent_context` is the other handler; the journal-era fork/export handlers are gone). One `case 'context.updated'` in `WebSocketContext.tsx` invalidates `['agentContext']`; the panel contains no subscription code. See [event_framework.md → UI-only lifecycle events](./docs-internal/event_framework.md).

**The store also stamps timestamps.** `save_conversation` attaches a `ts` (UTC ISO) to every stored message: callers regenerate wires from live `Message` objects each turn, so the store keeps the original stamp for the unchanged prefix and stamps only the appended turn — a message's time is when it first persisted, not when the conversation last saved. `ts` is a stored-view field only; `message_from_wire` ignores it, so it never reaches a provider on any path.

**Compaction never touches the system prompt, and its summary rides a USER message.** The AgentWorkflow's threshold compaction swaps `messages` for `[original system (verbatim), user(summary + current request)]`. The system prompt is the agent's byte-stable contract (prompt caching, behavioral consistency), and the seeding filter drops stored system messages — so a summary stored under the system role (the earlier design) silently vanished on the next firing while the noisy tool tail survived it. Prior tool calls/results are dropped at the swap and live only inside the summary; tool messages after the summary in the panel are new post-compaction work (their `ts` postdates it). Trigger, swap (before→after counts), and summarizer usage all log at INFO. Locked in `tests/temporal/test_agent_workflow.py`. See [agent_context_flow.md → Compaction](./docs-internal/agent_context_flow.md).

**Memory** (node type `simpleMemory`, displayed as **"Memory"** — the wire type is stored verbatim in graphs/pool keys and must not be renamed; plugin `server/nodes/tool/simple_memory/`) is a `ToolNode` on `input-tools` — durable facts the agent explicitly remembers, recalls, updates and forgets. It is not conversation history and has no markdown surface; `SimpleMemoryParams` declares only `reset_policy` (with `extra="ignore"`, so leftover legacy keys on migrated graphs are inert). `reset_policy` is in `server_controlled_fields`, which stops the **model** overriding it through tool arguments — it does not stop the operator editing it. Two model-facing contracts are load-bearing: (1) the `tool_description` ClassVar is imperative — *check memory (recall/list) before answering anything about the user; never claim ignorance without checking* — because with a passive "store and retrieve when useful" wording, models answered "I don't know who you are" from priors while the user's name sat one recall away (`tool_schema_locked = True` pins this description against stale ToolSchema rows). (2) Every durable mutation (remember/update/forget/clear, from the agent tool call or the panel) broadcasts the identity-only `memory.updated` CloudEvent ([`_events.py`](./server/nodes/tool/simple_memory/_events.py), same direct-broadcast rationale as `context.updated`), which `WebSocketContext.tsx` routes to invalidate the `memoryItems`/`memoryItem` queries.

Lifecycle helpers live in `server/services/memory/`; token tracking and compaction thresholds are in **[Memory Compaction](./docs-internal/memory_compaction.md)**. The pre-RFC-0002 markdown model is archived at [ARCHIVE/memory_lifecycle.md](./docs-internal/ARCHIVE/memory_lifecycle.md).

## Memory Compaction, Token Tracking, and Cost Calculation

Memory-connected standard agents aggregate native `Usage` across every model
turn. The in-process path calls `CompactionService.track()` after execution to
persist session metrics and can trigger `compact_context()`, which performs a
shared client-side summarization call through `ChatUnifier`. The Temporal
`AgentWorkflow` also aggregates and returns loop usage and can invoke the same
summarizer, but it does not currently persist that loop aggregate through
`CompactionService.track()`. Provider-native Anthropic/OpenAI context-management
compaction is not used by the agent runtime. Executions without connected
memory still aggregate usage inside the loop but do not create session
compaction records.

**Threshold precedence inside `CompactionService.track()` (highest-to-lowest): per-session `SessionTokenState.custom_threshold` > per-user `UserSettings.compaction_ratio` > env `COMPACTION_RATIO` (default 0.8) > `llm_defaults.json:agent.compaction.ratio`.** The Temporal agent workflow currently prepares its threshold from the model/user/env ratio without reading the per-session custom threshold, so do not promise that override on every execution path.

Full reference — the `CompactionService` API, shared client-side compaction, the 5-section summary format, DB models (`TokenUsageMetric` / `SessionTokenState` / `CompactionEvent`), WS handlers, and the broadcast events — in **[Memory Compaction](./docs-internal/memory_compaction.md)**; per-service API cost tracking is in **[Pricing Service](./docs-internal/pricing_service.md)**.

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

`credentials/sections/ApiUsageSection.tsx` shows per-service API usage and costs; `LlmUsageSection.tsx` shows per-provider token usage. Both read **precomputed** costs (`SUM(APIUsageMetric.cost)` / `TokenUsageMetric`) — neither reads `config/pricing.json`, which is consumed only server-side by `PricingService` at execution time.

## AI Agent Tool System

### Overview
Tool nodes provide capabilities that AI Agents can invoke during reasoning. Each tool node connects to the AI Agent's `input-tools` handle and defines a schema for the LLM to understand how to call it.

### Architecture
```
Tool Node (calculatorTool) → (tool output) → AI Agent (input-tools handle)
                                                    ↓
                                            AIService builds AgentToolSpec values
                                                    ↓
                                            run_native_agent_loop sends ToolDef schemas
                                                    ↓
                                            LLM selects zero or more tool calls
                                                    ↓
                                            Tool executor runs handlers
                                                    ↓
                                            Native tool results return to LLM
```

### Tool Execution Flow
1. **Tool Discovery**: AI Agent scans edges for nodes connected to `input-tools` handle
2. **Schema Building**: `_build_tool_from_node()` in `ai.py` combines the Pydantic validation model with an inlined JSON schema in an `AgentToolSpec` / `ToolDef`
3. **Provider Compilation**: `run_native_agent_loop` passes provider-neutral `ToolDef` values through `ChatUnifier`; each native provider compiles the schema it supports
4. **LLM Decision**: LLM decides when to call tools based on user query
5. **Status Broadcast**: `executing_tool` status broadcast with tool_name for UI animation
6. **Tool Execution**: `execute_tool()` in `tools.py` dispatches to appropriate handler
7. **Result Return**: Native tool-result messages are appended for continued reasoning

### Key Files
| File | Description |
|------|-------------|
| `server/services/handlers/tools.py` | Tool execution handlers |
| `server/services/ai.py` | `_build_tool_from_node()` / `_get_tool_schema()` - provider-neutral tool specs and Pydantic validation |
| `server/services/agent_runtime.py` | `AgentToolSpec` and the shared native agent loop |
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
- New specialized agent: update `AI_AGENT_TYPES` and the canonical teammate
  discovery/validation surfaces so a team lead can authorize it through
  `input-teammates`. Team leads delegate with Task Manager; `delegate_to_*`
  remains an internal/legacy dispatch identity and must not be taught as the
  model-facing team contract.
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
| taskManager | `TaskManagerParams` | `_execute_task_manager()` | Intrinsic durable team assignment; returns queued through detached Temporal runner, preserves cross-run history, review, retry/reassignment, cancellation, acceptance, timestamps, elapsed time, and token usage |
| writeTodos | WriteTodosSchema | `execute_write_todos()` / `handle_write_todos()` | Structured task list planning with checklist rendering |
| braveSearch | BraveSearchSchema | `handle_brave_search()` | Brave Search API web results |
| serperSearch | SerperSearchSchema | `handle_serper_search()` | Google SERP via Serper API |
| perplexitySearch | PerplexitySearchSchema | `handle_perplexity_search()` | AI-powered search with citations |
| Android service nodes | Per-service schema | `_execute_android_service()` | Direct Android service tools (see below) |

### Direct Android Service Tools
Android service nodes (batteryMonitor, wifiAutomation, etc.) connect directly to any agent's `input-tools` handle — this is the only Android tool path (the former `androidTool` aggregator was retired; legacy graphs are migrated on load by `workflow_migrations.normalize_legacy_android_toolkit`). The `execute_tool()` function detects these via `ANDROID_SERVICE_NODE_TYPES` and routes to `_execute_android_service()`.

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

### Android Toolkit Pattern (retired)
The former `androidTool` gateway node (single `android_device` tool aggregating multiple Android service nodes, n8n Sub-Node / LangChain Toolkit pattern) no longer exists. Android service nodes connect straight to `input-tools`, and sub-node exclusion keys solely on AI-agent config handles (`input-context` / `input-tools` / `input-skill` / `input-teammates`) — the `TOOLKIT_NODE_TYPES` constant was deleted along with all its usage sites. Legacy `service -> androidTool -> agent` graphs are rewritten on load by `services/workflow_migrations.normalize_legacy_android_toolkit`.

### Tool Schemas (per-service customization)
Custom LLM-visible schemas for Android service tools persist in the `tool_schemas` table and are read by the AI service at execution time.

**The write path has no reachable entry point — this is a known gap, not a design.** `ToolSchemaEditor.tsx` shipped with the retired Android Toolkit node and was removed with it; the `useToolSchema` client hook was its only consumer and was deleted in turn once it had zero importers. The WebSocket handlers and database CRUD remain, but nothing reachable calls `save_tool_schema` any more: no REST route writes the table and no server-internal code calls `database.save_tool_schema` outside the WS handler itself. So rows can only survive from before the editor was removed, or be written by hand.

The **read** path is live and load-bearing — do not delete it as "unused". [`services/ai.py`](./server/services/ai.py) fetches the stored row during tool construction and overrides `tool_name` / `tool_description` / `connected_services` from it, and `_build_schema_from_config` turns `schema_config["fields"]` into the actual LLM-visible tool schema. Restoring an editor means giving `save_tool_schema` a caller again; it does not require touching the read side.

#### Architecture
```
(no client writer — see above)
        ↓
  Database (tool_schemas table)
        ↓
  AI Service reads schemas at execution
```

#### Key Files
| File | Description |
|------|-------------|
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
    connected_services: Optional[Dict]  # Legacy field from the retired toolkit aggregation
```

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
Config nodes (context, tools, models) connect to parent nodes via special "config handles" (e.g., `input-context`, `input-tools`). These are auxiliary connections for configuration, not main data flow. The UI intelligently handles visibility of connected inputs based on this architecture.

### Config Handle Convention
Config handles follow the pattern `input-<type>` where type is NOT 'main':
- `input-context` - Context node (RFC-0002; observation surface onto the journal)
- `input-tools` - Tool nodes (including `simpleMemory`, which is a ToolNode)
- `input-model` - Model configuration nodes
- `input-skill` - Skill nodes
- `input-task` - Task completion trigger nodes
- `input-teammates` - Team member agent nodes
- `input-main` - Main data flow (NOT a config handle)
- `input-memory` - **retired.** Kept only so immutable V1 graph snapshots replay; no agent declares it. `normalize_workflow_graph` rewrites legacy `simpleMemory -> input-memory` edges into a Context node plus an ordinary tool edge.

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

### Sub-Node Execution Exclusion

Sub-nodes (tools, memory, skills, teammates) connect TO an agent, not from it, so in parallel execution mode Kahn's algorithm would see them with in-degree 0 and incorrectly schedule them in layer 0. The executor excludes them: any node whose edge targets an AI-agent config handle (`input-context`, `input-tools`, `input-skill`, `input-teammates`) is detected as a sub-node in `ExecutionContext.create()` / `_compute_execution_layers()` and skipped from execution layers — it executes only when the agent invokes it (e.g. as an AI tool).

Toolkit aggregator nodes (the former `androidTool` and its `TOOLKIT_NODE_TYPES` constant) were retired; Android service nodes now connect directly to the agent's `input-tools` handle. Legacy `service -> androidTool -> agent` graphs are migrated on load by `services/workflow_migrations.normalize_legacy_android_toolkit` (pure, idempotent: services re-wire directly to each agent, orphaned toolkits are removed with a warning).

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
- **Shared base**: `server/nodes/android/_base.py` — `AndroidServiceBase`, `SERVICE_ID_MAP`, `execute_android_service_tool`
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
- **Relay Connection**: The WebSocket connection to `wss://relay.opencompany.sh/ws` - can be active without a device
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
WhatsApp nodes use square design with integrated QR code viewing and proper error handling. The integration proxies all requests through the Python backend to the WhatsApp RPC service (port from `WHATSAPP_RPC_PORT`, or the `--port` CLI flag). Supports individual chats, groups, and newsletter channels (sending, querying, follow/unfollow, create, mute, mark viewed, react, live updates, media download, profile pics). All 14 WhatsApp events handled.

### Architecture
```
Frontend (WhatsAppNode.tsx) → Python Backend (/api/whatsapp/*) → WhatsApp RPC Service (localhost:${WHATSAPP_RPC_PORT})
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
**Fix**: Added `"routers.whatsapp"` to wiring list in `server/main.py`

(Historical snippet — the router set has since changed: `routers.whatsapp` /
`routers.maps` / `routers.android` moved into plugin folders (Wave 11.I) and
`routers.nodejs_compat` was deleted (July 2026). The live wire list is in
`server/main.py`. The lesson stands: every wired module must be listed or the
reloader child crashes.)

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

#### Output Schema (plugin-owned, `server/nodes/whatsapp/whatsapp_receive.py`)
Runtime output shapes are fetched lazily by InputSection per the Wave 3 source-of-truth decision. WhatsApp's schemas live in the plugin folder (the node's `Output` Pydantic classes — `WhatsAppGroupInfo` / `WhatsAppReceiveOutput` / `WhatsAppSendOutput` / `WhatsAppDbOutput`) and self-register from `nodes/whatsapp/__init__.py` via `register_output_schema(...)` — same pattern as telegram; `services/node_output_schemas.py` carries no whatsapp code.
Served via `GET /api/schemas/nodes/whatsappReceive.json` + `get_node_output_schema` WS handler. See [docs-internal/plugin_system.md](./docs-internal/plugin_system.md) (the original RFC is archived at [ARCHIVE/schema_source_of_truth_rfc.md](./docs-internal/ARCHIVE/schema_source_of_truth_rfc.md)).

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
| `agent_progress` | CloudEvents v1.0 envelope (type=`com.opencompany.agent.progress`) — per-step agent-loop iteration count, drives the live "N / max" badge on AI Agent canvas nodes |
| `agent_capability` | CloudEvents v1.0 envelope (`com.opencompany.agent.(skill|tool).*`) — exact-agent capability lifecycle with deterministic Temporal IDs, `(source,id)` deduplication, sanitized data, and an optional exact target node |
| `deployment_snapshot` | CloudEvents v1.0 envelope (type=`workflow.deployment.snapshot`) — pushed once per WS connect from `broadcaster._send_deployment_snapshot`; lets the FE reconcile stale `deploymentStatus.isRunning=true` after a backend restart wiped `DeploymentManager._deployments`. Empty list is meaningful (forces reset). |
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

### Node execution (`client/src/services/executionService.ts`)
`ExecutionService.executeNodeViaWebSocket()` is the live path, called from
[`ParameterPanel.tsx`](./client/src/ParameterPanel.tsx). It delegates to the
context's `executeNode`, which sizes its request budget from the node's own
`uiHints.executionTimeoutMs` rather than any local list of "slow" node types.
(A `useExecution` hook once wrapped this; it was unreachable and was deleted.)

### useApiKeys (`client/src/hooks/useApiKeys.ts`)
Hook for API key management via WebSocket:
```typescript
const { validateApiKey, getStoredKey, saveApiKey, deleteApiKey } = useApiKeys();
```

### Android operations (no dedicated hook)
Android work goes through the WebSocket context directly — `getAndroidDevices` / `executeAndroidAction` / `androidStatus` off `useWebSocket()`, or a raw `sendRequest('android_relay_connect', …)`. A `useAndroidOperations` hook once wrapped those context methods; every live surface bypassed it, so it was deleted.

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
- **Modular Backend**: workflow.py reduced from 2068 lines to a facade (~840 lines today)
  - NodeExecutor: Registry-based dispatch with `functools.partial` for dependency injection
  - ParameterResolver: Compiled regex for `{{node.field}}` template resolution
  - DeploymentManager: Handles deploy/cancel lifecycle with TriggerManager for cron/events
  - No global state: `_active_cron_jobs` moved to TriggerManager instance variable
- **Performance**: Fast HMR updates and clean TypeScript compilation
- **AI Architecture**: 5-layer system with factory pattern and secure credential management
- **Android Architecture**: Factory-based node creation with ADB integration for device automation
- **WebSocket-First Architecture**: most frontend-backend RPC (parameters, execution, API keys, Android, WhatsApp, skill operations) goes through WebSocket. Live handler set lives in the `MESSAGE_HANDLERS` dict in `server/routers/websocket.py` plus plugin-registered handlers via `services.ws_handler_registry`.
- **WebSocket Hooks**: Dedicated React hooks (useWhatsApp, useApiKeys, useParameterPanel) for clean component integration; node execution goes through `services/executionService.ts`
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
  - Backend proxies all requests to WhatsApp RPC service (`WHATSAPP_RPC_PORT`)
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
- **Conditional Redis (historical)**: the Docker Compose topology (Redis profiles, `scripts/docker.js` wrapper) is historical — see [docs-internal/deployment_legacy.md](./docs-internal/deployment_legacy.md)
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
  - Configurable port via `--port` CLI flag, `PORT` or `WHATSAPP_RPC_PORT` env vars (OpenCompany passes WHATSAPP_RPC_PORT; the npm package's own default is 9400)
  - QR codes generated as base64 PNG in memory (no file I/O, no `data/qr` directory)
  - Source: https://github.com/trohitg/whatsapp-rpc
- **Node Data Architecture**: `node.data` only stores `label` (display name). All parameters are stored in the database via `save_node_parameters` WebSocket handler. This prevents parameter bloat in workflow JSON exports and keeps React Flow state lightweight. `useDragAndDrop.ts` saves default parameters to DB on drop, not to `node.data`.
- **Workflow Export/Import with Parameters**: Exported workflow JSON includes a `nodeParameters` field containing all node configuration (provider, model, prompt, skillsConfig, etc.) fetched from the database at export time. On import, embedded `nodeParameters` are saved back to the database. `sanitizeNodes()` in `workflowExport.ts` still strips `node.data` to UI-only fields (`label`, `disabled`, `condition`). The `parameterSanitizer.ts` utility strips credential-bearing keys (SENSITIVE key sets + substring matching + runtime-key stripping) and is ACTIVE in the export path (`buildExportParameters` in `workflowExport.ts` calls `sanitizeParameters`). Old exports without `nodeParameters` import cleanly (backward compatible). Key files: `client/src/utils/workflowExport.ts`, `client/src/utils/parameterSanitizer.ts`, `client/src/Dashboard.tsx` (export/import handlers), `server/services/example_loader.py`.
  - **The sanitizer is client-only, and the backend depends on it without enforcing it.** There is no server-side sanitizer and no server-side export endpoint; the raw material is freely available to any caller via `handle_get_all_node_parameters` (`routers/websocket.py`), which returns stored parameters verbatim. So *any* export path built outside the React app is unsanitized by construction. This is deliberate but load-bearing: `server/tests/test_export_sanitizer_sync.py` reads `parameterSanitizer.ts` off disk and asserts its `RUNTIME_KEYS` covers everything `clear_agent_session_state` wipes — a cross-language invariant that exists because these two lists *already drifted once*, when the sanitizer held camelCase `memoryContent` against the real `memory_content` and every export carried full conversation histories into the shipped example workflows and from there into a public repo. Two consequences worth knowing before touching either side: adding a field to `clear_agent_session_state` fails that test until `RUNTIME_KEYS` matches, and adding a server-side export path silently bypasses stripping entirely. Known accepted hole: `sanitizeParameters` recurses into objects but passes arrays through untouched (documented in its own docstring and locked by `parameterSanitizer.test.ts`), so `{tasks: [{api_key: 'sk-1'}]}` exports the key.
- **Skill System Architecture**: Skills organized in `server/skills/<folder>/` subfolders. Each folder appears in Master Skill dropdown. DB/Master Skill customization is authoritative. Standard skills use progressive disclosure without modifying the agent system prompt: the dynamically connected `Skill` tool carries the bounded name/description catalogue, loads instructions, then separately reads/searches declared resources. Personality skills remain eager. Duplicate connected names block with `DUPLICATE_CONNECTED_SKILL_NAME`. Runtime state and sanitized `agent.skill.*` observability live in [`skill_runtime.py`](./server/services/skill_runtime.py); tool results persist in the current conversation while canvas badges clear at turn end. Icon resolution mirrors `BaseNode._metadata_dict`: per-plugin `<plugin>/icon.svg` (served as `/api/schemas/nodes/<type>/icon`) → `visuals.json` (emoji / `lobehub:<brand>`); color: per-plugin `<plugin>/meta.json` → `visuals.json`. Lives in [`skill_loader.py::_parse_skill_metadata`](./server/services/skill_loader.py). Native DOM keydown handler prevents React Flow from intercepting Ctrl shortcuts in skill editor. **Mutation broadcasts use the CloudEvents-typed `skill_lifecycle` wire key** with stages `created` / `updated` / `deleted` / `content_saved` — see "Plugin-folder location for plugin-specific CloudEvents factories" below.
- **Example Workflows**: Auto-load example workflow seeds from `<repo>/.opencompany/workflows/` on first use (git-tracked; the only non-ignored content under `<repo>/.opencompany/`). Path resolved by `core.paths.example_workflows_dir()` — fixed, NOT under `DATA_DIR`, preserved by `company clean` via `_OPENCOMPANY_KEEP`. Uses `UserSettings.examples_loaded` flag; supports anonymous users (`user_id="default"`); embedded `nodeParameters` saved to DB on import.
- **Onboarding Service**: 4-step welcome wizard (Welcome, How it works, Connect your AI, Try it) using shadcn primitives + lucide icons. Database-backed via `UserSettings.onboarding_completed` + `onboarding_step`. Existing users auto-skip (migration marks `examples_loaded=1` as completed). Replayable from Settings "Help" section. No new WebSocket handlers needed. See [Onboarding Service](./docs-internal/onboarding.md) for details.
- **Node.js Code Executor**: Persistent Node.js server (Express + tsx) at `NODEJS_EXECUTOR_PORT` for JavaScript/TypeScript execution, replacing subprocess spawning per execution. The code executor plugins under `server/nodes/code/` call `NodeJSClient` which makes HTTP requests to the Node.js server. All config via environment variables (`NODEJS_EXECUTOR_URL`, `NODEJS_EXECUTOR_PORT`, etc.).
- **writeTodos Tool Node**: Dedicated AI tool for task planning connecting to any agent's `input-tools` handle. `TodoService` singleton (`server/services/todo_service.py`) stores JSON-based per-session todo state keyed by workflow_id. Handler broadcasts `phase: "todo_update"` via WebSocket on each update for real-time UI. (The former checklist rendering — `formatTodoOutput()` — lived only in the legacy `ui/OutputDisplayPanel.tsx`, which was deleted as dead code; the active `components/output/OutputPanel.tsx` shows todo results as regular JSON.) Schema uses `TodoItem`/`TodoStatus` Pydantic enum. Skill at `server/skills/assistant/write-todos-skill/SKILL.md` teaches the plan-work-update loop.
- **Temporal Activity Heartbeats**: Activities send `activity.heartbeat()` on every non-matching WebSocket broadcast inside the read loop in `services/temporal/activities.py`. This keeps long-running browser and claude_code_agent operations alive past the 2-minute `heartbeat_timeout`. Start/end heartbeats alone were causing `TIMEOUT_TYPE_HEARTBEAT` failures on ops taking 5-10 minutes. Connection config: `heartbeat=30`, `receive_timeout=None` (each receive is bounded by a 30 s `asyncio.wait_for`; liveness comes from heartbeats, and per-node activities run under a 24 h `start_to_close_timeout`).
- **WebSocket `_safe_send` Guard**: `server/routers/websocket.py` checks `websocket.client_state.name != "CONNECTED"` before sending and logs at `debug` level (not `error`) on failure. Prevents "ASGI message after websocket.close" errors when broadcasts race with disconnects.
- **Claude Code CLI Flag**: the Claude Code agent (`nodes/agent/claude_code_agent/`) uses `--max-budget-usd <amount>` (previously incorrectly `--max-cost`, which caused "unknown option" errors on every run).
- **Persisted-prefix cache contract**: `PersistQueryClientProvider` hydrates entries with the QueryClient's *default* options, so any prefix in `PERSISTED_KEY_PREFIXES` (`client/src/lib/queryPersist.ts`) MUST also have a matching `queryClient.setQueryDefaults(['<prefix>'], { staleTime: FOREVER, gcTime: FOREVER })` declaration in `client/src/lib/queryClient.ts`. Per-call options don't apply on hydration. Canonical set: `nodeSpec`, `nodeGroups`, `skillContent`. `credentialValues` was previously persisted here but was removed per OWASP HTML5 Security Cheat Sheet / ASVS V9.9 — decrypted API keys must never live in `localStorage`. The in-memory TanStack Query cache (`gcTime: ∞`) keeps the credentials form populated for the session lifetime; on reload the panel refetches via WS. `credentialCatalogue` has its own `idb-keyval` warm-start path so it is intentionally not in either list.
- **WebSocket `sendRequest` replay queue**: when the socket is not open, requests enqueue with `AbortController`-backed per-request timeouts (default 30s) and replay on reconnect inside `ws.onopen` before `setIsReady(true)`. Queue capped at 200 with FIFO eviction. Intentional close (`event.code === 1000`) drops the queue; transient closes preserve it. Eliminates indefinite spinners during the 3s reconnect window. Implementation in `client/src/contexts/WebSocketContext.tsx` (`pendingSendQueueRef` + `drainPendingSends`).
- **`currentWorkflowId` single source**: lives in `useAppStore.currentWorkflow.id` only. Non-React listeners (WS handlers) read via `useAppStore.getState().currentWorkflow?.id` -- the documented Zustand escape hatch. The push to `nodeStatusStore.setCurrentWorkflowId` is driven from one `useEffect` in `Dashboard.tsx`. Removed the prior `currentWorkflowIdRef` mirror inside WebSocketContext that misrouted broadcasts during workflow switches.
- **Execution correlation IDs**: `handle_execute_node` issues a `uuid4().hex` token at request entry, propagates it through every `node_status` / `node_output` broadcast for the run, and returns it in the response payload as `execution_id`. Frontend `ExecutionResult.executionId` carries it; `OutputSection` dedups runs by it instead of `JSON.stringify(outputs)` (which collapsed distinct executions whose payloads matched). Same trace-id pattern as OpenTelemetry / HTTP request ids.
- **`clear_node_status` idle reset**: `StatusBroadcaster.clear_node_status(node_id)` resets the slot to `{status: "idle", data: {}, cleared: true}` instead of `del`'ing it. Deleting created a race window where the in-flight execution's `success` broadcast re-created the entry and stuck the UI on "completed" for a cancelled node. Idle reset preserves entry identity so subsequent broadcasts update normally; the `cleared: true` flag distinguishes "never ran" from "explicitly cleared."
- **`get_node_output` race**: in-memory `_outputs` cache re-population after a DB-fallback `await` previously overwrote a fresh in-memory write with a stale DB value. Fix uses nested `dict.setdefault` (atomic at the CPython GIL level -- no lock needed). `store_node_output` and `clear_all_outputs` had no real race because asyncio coroutines do not preempt at synchronous statements. Reference for the Python concurrency model: https://docs.python.org/3/library/asyncio-task.html#asyncio-await
- **`NodeUserError` (services.plugin)**: typed exception for user/LLM-correctable failures (string not found, command not found, bad cwd, missing required field, operator-only Twitter query, Python sandbox `import`, Node.js sidecar down). `BaseNode.execute()` catches it specifically: single WARN line in the operator log (no traceback) + structured `{success: False, error_type: "NodeUserError", error: ...}` envelope. Genuine bugs still flow through the generic `except Exception` branch and keep their full stacktrace via `logger.exception`. Adopted across `fileRead` / `fileModify` / `fsSearch` / `gallery` / `process_manager` / `pythonExecutor` / `javascriptExecutor` / `typescriptExecutor`. **Plugin WS handlers that can fail user-correctably must use `@ws_response` (from `services.plugin.ws`), not `@ws_handler`** — the latter logs every exception at ERROR with a full traceback, which breaks the one-WARN-line `NodeUserError` contract. `gallery/_handlers.py` is the reference. Reach for it whenever the LLM (or user) can fix the input and retry — never for actual server bugs.
- **Process manager Windows shim resolution**: `process_service.start()` resolves `argv[0]` via `shutil.which()` before `asyncio.create_subprocess_exec`. On Windows, `shutil.which` honours `PATHEXT` and returns the absolute `.cmd` path (e.g. `C:\...\npm.cmd`); `CreateProcessW` then launches it directly. No `cmd /c` wrap — same canonical idiom used by `browser_service`, `claude_code_service`, `claude_oauth`, `himalaya_service`. Without this, bare `argv[0]="npm"` raises `WinError 2` because `CreateProcessW` does NOT apply `PATHEXT` to bare names. Missing binary → early `Command not found: '<bin>'. Check spelling or ensure the binary is on PATH.` envelope (no traceback).
- **Gallery — the workspace file explorer (`gallery`, `nodes/filesystem/gallery/`)**: sibling of the process-manager pattern below — the `isGalleryPanel` uiHint gives it a full-height MiddleSection panel (breadcrumbs, grid/list, image thumbnails, search, preview, upload) instead of the plain params list. It pairs the hint with `hideInputSection` but **keeps** the Output section, because unlike `processManager` it produces output worth seeing and dragging. Four rules worth knowing before touching it: (1) **The panel edits the node's own params** — navigating writes `path`, pinning writes `selection` — so what you browse is what the node emits, rather than a second copy of that state free to drift. (2) **The backend decides; the panel renders.** Each listing row arrives with a finished `ref` (a serialized `FileRef`, `null` for directories) and a `preview` verdict from `services/media/preview.py`; breadcrumbs arrive as `crumbs`; a search term is turned into a glob by `search_to_pattern` server-side. Re-deriving any of these client-side would be a second copy of a server rule — and for `preview` specifically, a second copy of a *security* decision. (3) **Drag uses its own `workspaceFile` discriminator, never the existing `nodeOutput`** — that branch calls `onChange(value)` unconditionally, so reusing it would mean dropping a file into a half-written prompt destroys the prompt. `ParameterRenderer.handleDrop` therefore branches: a `file` param takes the ref whole, everything else appends the path with smart spacing. (4) **`isFileRef` accepts every `FileKind`, not just `audio`** — the gallery emits `kind: "file"` even for a `.wav`, because `kind: "audio"` asserts `inspect_audio` probed the container and a fabricated duration would mis-bill a per-second provider downstream; narrowing the check back would render a dropped file as raw JSON. Listing rides the WebSocket (`list_workspace_files`) because the consumer already holds an authenticated socket with request correlation; `GET /api/workspace/{id}/files/{path}` stays the *content* channel (preview, download, Range). Frontend: [GalleryPanel.tsx](./client/src/components/parameterPanel/GalleryPanel.tsx) + `parameterPanel/gallery/`, [useDragWorkspaceFile.ts](./client/src/hooks/useDragWorkspaceFile.ts), [types/workspaceFiles.ts](./client/src/types/workspaceFiles.ts).
- **Canvas — the pushed-content display board (`canvas`, `nodes/tool/canvas/`)**: the third full-height-panel sibling (gallery / processManager / canvas), and the first with a SECOND host — a docked resizable right sidebar ([CanvasDock.tsx](./client/src/components/ui/CanvasDock.tsx) + [canvasDockStore.ts](./client/src/stores/canvasDockStore.ts)) that auto-opens on push and serves ephemeral click-to-preview from the gallery dialog. Both hosts render one shared component ([parameterPanel/canvas/CanvasContent.tsx](./client/src/components/parameterPanel/canvas/CanvasContent.tsx)). Rules worth knowing before touching it: (1) **The broadcast is identity-only** — `canvas_updated` carries `{workflow_id, node_id, revision}`; content flows solely through the authorized `canvas_list` handler (simple_memory security preamble: external socket + owner + graph-ownership, exactly-one-node-of-type check). (2) **The board stores references, never bytes** — `display(paths=…)` builds refs via `resolve_media` + stat (never `coerce_file_param`; don't read content to make a pointer), notes cap at 64KB with a visible marker, the board FIFO-caps at 200, and the op's Output is ids+counts only. (3) **Iframe sandboxes are test-locked security decisions**: external URLs get exactly `sandbox="allow-scripts allow-forms"` + `no-referrer`; workspace HTML renders ONLY as `srcDoc` + `sandbox="allow-scripts"` (opaque origin, no cookies — `NEVER_INLINE` respected, not worked around); PDF is a plain same-origin iframe enabled by the `INLINE_EXACT` exact-match set in `preview.py`. (4) **Never `addEventListener('canvas_updated')`** — the WebSocketContext switch case shadows the default-case fan-out. (5) The `tool` group auto-derives `isConfigNode: True`; canvas declares it `False` because `input-main` is real dataflow. (6) `hooks/useWorkspaceText.ts` is the repo's first client-side file-content fetch (streamed, cancelled at 512KB, key self-busts on `size_bytes`+`modified_at`) — attachment disposition doesn't block `fetch()`. Browser screenshots became its feedstock: both browser plugins persist shots as workspace FileRefs via the tolerant, containment-checked [nodes/browser/_screenshots.py](./server/nodes/browser/_screenshots.py). Full reference: [docs-internal/canvas_node.md](./docs-internal/canvas_node.md).
- **Process manager port admission + middle panel**: server commands declare listener `ports` (with compatibility inference for `--port`, `-p`, `PORT`, and `*_PORT`). `ProcessService.start()` serializes admission, checks managed reservations plus OS TCP listeners, and returns structured `PORT_IN_USE` without killing the owner or allowing framework auto-port fallback. Explicit ports/environment survive restart. The `isProcessManagerPanel` UI hint gives only `processManager` a full-height workflow-scoped process table with PID/ports/timestamps/elapsed/output and stop/restart controls. See [docs-internal/process_manager.md](./docs-internal/process_manager.md).
- **Telegram message auto-split + caption spill**: the Bot API caps `sendMessage.text` at 4096 and media captions at 1024 (constants `_TG_TEXT_LIMIT` / `_TG_CAPTION_LIMIT` in `nodes/telegram/_service.py`; per https://core.telegram.org/bots/api). **Both limits are measured in UTF-16 code units, not characters** — an emoji costs 2 — so length goes through `_tg_len`, never `len()`. `_split_head` cuts one chunk at the cleanest paragraph → line → sentence → space boundary past the halfway mark, else hard-cuts; `_split_text` loops it. `send_message` chunks long text and threads each part under the previous (`reply_to_message_id` cascade), returning the first message's metadata + `parts` + `message_ids[]`. `_send_captioned_media` (used by `send_photo` / `send_document`) truncates an over-long caption at the cap and sends the remainder as a threaded reply with `disable_notification=True`, reporting `caption_truncated` + `follow_up_message_ids` — previously the whole send just failed with `BadRequest("Message caption is too long")`. **Ordering invariant: split the RAW body before `_resolve_body` runs markdown→HTML.** Splitting after conversion can separate a `<b>` from its closing tag, which Telegram rejects with "can't find end of the entity"; Telegram measures the cap against entity-parsed text, so truncating raw markdown is conservative. Locked by `tests/nodes/test_telegram_service.py`.
- **Telegram inbound content types**: `_format_message` extracts detail dicts for all 11 types via the `_CONTENT_PROBES` / `_DETAIL_EXTRACTORS` tables, plus a normalized `media` block (`kind` / `file_id` / `mime_type` / `file_name` / …) that downstream nodes read instead of branching per type; MIME and filename are synthesised where Telegram omits them. **Probe order is load-bearing**: Telegram sets `message.document` on animation messages too, so `animation` and `video_note` must be probed before `document` and `video` or every GIF classifies as a document. The trigger never downloads — media travels as a `file_id`, never bytes or base64, because node results are persisted, broadcast, and replayed into the LLM conversation every turn.
- **Google OAuth scope expansion**: Google's authorisation server legitimately returns a wider scope set than requested when the OAuth Client's "Data Access" page lists extra scopes (commonly `cloud-platform`) or when `include_granted_scopes` replays a previously-granted scope. `oauthlib` does strict set-equality and aborts with `Warning: Scope has changed`. As of 2026 (`google-auth-oauthlib` 1.2.4, `oauthlib` upstream issue #562 still open), no constructor flag, context manager, or `expected_scopes` argument exists — the documented relief is the env var. `services/google_oauth.py` sets `OAUTHLIB_RELAX_TOKEN_SCOPE=1` via `os.environ.setdefault` BEFORE the `google_auth_oauthlib.flow` import (oauthlib reads it once at parameters-module import; request-time setting races under uvicorn workers), paired with `warnings.filterwarnings(message=r"Scope has changed.*")` to keep the operator log clean. Long-term root cause: audit the Cloud Console Data Access page and remove `cloud-platform` if no handler uses it.
- **Code-executor error mapping** (`pythonExecutor` / `javascriptExecutor` / `typescriptExecutor`): wrap user-code execution and sidecar calls in `try/except`. Python: detect `ImportError("__import__ not found")` (the LLM tried `import X` against the sandboxed builtins) and surface the pre-injected names list (`math, json, datetime, timedelta, re, random, Counter, defaultdict`) plus the suggestion to use `process_manager` for unsupported modules; other exceptions get formatted as `<ErrorName> at line N: <message>` (line N walked from `<string>` frame in the traceback) plus any captured stdout. JS/TS: detect `aiohttp.ClientConnectorError` and surface "JavaScript executor not running on NODEJS_EXECUTOR_PORT. Start the dev runner or fall back to python_executor." All raise `NodeUserError` so the framework logs one WARN line — no aiohttp/CreateProcessW noise in the operator log.
- **Workflow-scoped chat + console history**: chat messages persist with `chat_messages.session_id == <workflow_id>` (or `"default"` when no workflow is open); console logs persist with `console_logs.workflow_id`. Backend `database.get_console_logs(limit, workflow_id=None)` and `clear_console_logs(workflow_id=None)` filter by workflow when given. `handle_clear_console_logs` broadcasts `console_logs_cleared` carrying the `workflow_id` so the existing frontend filter in `WebSocketContext` keeps other workflows' panels intact. Frontend reads `currentWorkflow.id` via the documented Zustand escape hatch (`useAppStore.getState().currentWorkflow?.id`) at call time for `clearChatMessages` / `clearConsoleLogs` / `sendChatMessage` so the callbacks don't re-create on every workflow switch. A dedicated `useEffect([currentWorkflowId, isReady])` resets local `chatMessages` / `consoleLogs` and refetches both when the user opens / switches workflow. Incoming `console_log` broadcasts are filtered by `currentWorkflow.id` so a parallel run on another workflow never bleeds into the active panel. Legacy logs without `workflow_id` still surface (transition guard).
- **Auto-derived `isConfigNode` uiHint**: `_derive_auto_ui_hints(group)` in `services/plugin/base.py` automatically sets `uiHints.isConfigNode: True` on any plugin whose `group` tuple contains `memory` or `tool` (centralized as `_CONFIG_NODE_GROUPS = frozenset({"memory", "tool"})`). Explicit `cls.ui_hints` always wins (merge order: auto first, then `dict.update`). Frontend `InputSection.tsx` and `OutputPanel.tsx` consume the flag via `definition?.uiHints?.isConfigNode === true` — the old `groups.includes('memory') || groups.includes('tool')` heuristic is gone. Pytest invariant `test_ui_hints_only_carry_known_flags` locks the flag name. Adding a new auxiliary node type costs zero per-plugin code; opting out costs one line (`ui_hints = {"isConfigNode": False}`).
- **`isMasterSkillEditor` uiHint replaces `node.type === 'masterSkill'` checks**: 6 frontend callsites (`Dashboard.tsx:98` component dispatch, `useAutoSkillEdges.ts` constant + edge filter, `MiddleSection.tsx` × 3) now read `getCachedNodeSpec(type)?.uiHints?.isMasterSkillEditor === true` instead of comparing the type string. The `MasterSkillNode` plugin already declared the hint — no backend change. Renaming the plugin's `type` is now a single backend edit followed by a NodeSpec deploy; the frontend never needs to know the string.
- **`outputMode: "terminal"` uiHint — spec-driven CLI output rendering**: the output panel (`components/output/OutputPanel.tsx`, the active renderer — `ui/OutputDisplayPanel.tsx` is legacy/unimported) renders string responses through ReactMarkdown by default, which whitespace-collapses CLI text. CLI-wrapper plugins (`githubAction`, `vercelAction`, `shell`) declare `ui_hints = {"outputMode": "terminal"}`; the panel resolves the spec via `useNodeSpec(selectedNode?.type)` and renders their text in a `<pre>` on the per-theme `--code-*` surface, routes wholly-JSON strings to the JSON tree via the shared `tryParseJson` (`utils/formatters.ts`), and surfaces object/array `result` payloads (server-side-parsed CLI JSON; arrays survive `unwrap` un-peeled) in the Response section. Pair with the `_shape` convention: parsed JSON in `result` OR text in `stdout` — never both — empty keys omitted. Locked by `client/src/components/__tests__/OutputPanel.test.tsx` (render tests: real `\n`/`\t` preserved) + the uiHints known-set in `test_node_spec.py`. Cache note: nodeSpecs are session-sticky (`staleTime: FOREVER`, revision-busted at page load) — after a backend restart a hard browser reload is needed before new uiHints reach an open canvas.
- **`--action-X-hover` triplet + ActionButton zero-arithmetic**: each of the 6 action roles (`run`/`stop`/`save`/`config`/`secret`/`tools`) now exposes a `-hover` variant (0.25 alpha) alongside the existing `-soft` (0.15) and `-border` (0.6). ActionButton's CVA reads `hover:bg-action-X-hover` directly; disabled state is the shadcn-idiomatic `disabled:opacity-50` on the base class. No per-token `/25`, `/40`, `/10` opacity arithmetic at any call site. Credential-modal panels (`OAuthConnect`, `EmailPanel`, `QrPairingPanel`) and the skill editor (`MasterSkillEditor`) consume `<ActionButton intent="...">` directly. `ActionDef` carries an `intent` key; the catalogue adapter maps server-sent `theme_color` palette strings to intents via `SERVER_COLOR_TO_INTENT`.
- **Credentials: DB as single source of truth + symmetric broadcasts + cache dedup**: `CredentialsDatabase` (encrypted SQLite) is canonical; every other layer is a derived cache with explicit invalidation. Backend `AuthService._memory_cache + _models_cache` collapsed into one `_api_key_cache: Dict[str, ApiKeyCacheEntry]` dataclass — single write/evict site. Per RFC 9700 (OAuth 2.0 BCP, 2024) the `_oauth_cache` no longer carries `refresh_token`; new `get_oauth_refresh_token(provider, customer)` reads from the encrypted DB on every call. `validate_api_key`, `save_api_key`, `delete_api_key`, `twitter_logout`, `google_logout` now broadcast symmetrically: `update_api_key_status` (in-memory map) + `broadcast_credential_event(...)` (refetch signal) wrapping `WorkflowEvent` (CloudEvents v1.0 from `services/events/envelope.py`, the same envelope the Wave 12 EventSource framework uses). The dead-letter `credential_catalogue_updated` event is finally emitted by the backend. Frontend retired the 200-LOC `client/src/components/credentials/providers.tsx` static fallback — `useCatalogueQuery` is the only source; cold-boot renders `<Skeleton>`, server-unreachable shows an explicit error state. `ApiKeyStatus.hasKey` mirror dropped (duplicated catalogue's `provider.stored`); two new selector hooks (`useProviderStored`, `useStoredProviderCount`) read the catalogue. Pytest invariant `test_credential_broadcasts.py` (14 tests) locks the broadcast contract via `inspect.getsource` introspection + the CloudEvents v1.0 envelope shape + AuthService DB-write-then-cache-update ordering + the no-refresh-token-in-cache rule.
- **Local-LLM provider routing + per-model context**: Ollama and LM Studio are first-class providers (12 total for agents: 10 cloud + 2 local). Provider detection is driven by `detect_ai_provider` in `server/constants.py` plus the `provider` Literal in `nodes/agent/{ai_agent,chat_agent,_specialized}.py` — both MUST list `ollama` / `lmstudio` or chat-model nodes / agent dropdowns silently fall through to `'openai'` and `execute_chat` calls api.openai.com with the placeholder key. The validator at `nodes/model/_local_validator.py` probes via the official SDKs (`ollama.AsyncClient.ps()` for typed `ProcessResponse.Model`, `lmstudio.AsyncClient.llm.list_loaded()` for typed `LlmInstanceInfo`) — reads only typed fields (`context_length`, `max_context_length`, `vision`, `trained_for_tool_use`, `architecture`, `params_string`, `format`, plus Ollama's `details.{family,parameter_size,quantization_level}`). No regex, no Modelfile-parameters parsing, no `/api/show` modelinfo dict-key hunting. Per-model params persist in `EncryptedAPIKey.models["model_params"]` (via `save_api_key(model_params=...)`) AND in `model_registry.json` via `register_local_model()` — sync `get_context_length()` / `get_max_output_tokens()` find real values without async DB lookups, and entries survive process restart. `is_model_valid_for_provider` returns `True` for open-world providers (`openrouter` / `ollama` / `lmstudio`) so local model names like `qwen/qwen3.6-27b` aren't rejected by the cloud-style pattern check. Runtime path uses `OpenAIProvider(base_url={user_proxy_url}, api_key="ollama")` — traffic stays on `localhost`.
- **Typed SDK error → `LLMError` → `NodeUserError`**: Native providers normalize SDK failures into structured `LLMError` values carrying category, retryability, HTTP status, provider code, request ID, and retry-after metadata. `ChatUnifier` translates them at the execution boundary into a user-safe `NodeUserError`; `BaseNode.execute()` logs that at WARN with one line and no stack trace. Unexpected exceptions retain the full-traceback path. In-process agent turns retry only errors marked retryable, while Temporal owns activity retries and disables the inner retry loop.
- **Plugin extraction (Wave 11.I)**: every plugin's WS handlers, OAuth client, FastAPI router, and lifecycle service live entirely under `nodes/<plugin>/`. Eight plugin domains migrated this round (whatsapp / twitter / google-workspace / android / browser / email / code / credential-validation-scaffold for maps+apify+ollama+lmstudio) follow the telegram pattern. `routers/websocket.py` shrunk from ~3,785 to ~2,977 LOC (-808). Three plugin routers (`routers/twitter.py`, `routers/google.py`, `routers/android.py`) moved into `nodes/<plugin>/_router.py` and mount via the plugin-router loop in `main.py`; the explicit `app.include_router(<plugin>.router)` calls and `from routers import <plugin>` imports in `main.py` are gone. **`register_router(router, name=...)` is the new sibling helper to `register_ws_handlers` (same file: `services/ws_handler_registry.py`)** — seven generic registries total now (ws_handlers, router, filter_builder, trigger_precheck, service_refresh, output_schema, agent_context_builder). **`Credential.validate(data) -> dict` + `Credential._probe(api_key) -> ProbeResult` is the new shared validator scaffold** in `services/plugin/credential.py` — replaces the per-router `_SPECIAL_PROVIDER_VALIDATORS` dict; one `_LLMApiKey._probe` method serves all 10 cloud LLM providers, dedicated `_probe` overrides handle Maps + Apify, `_LocalLLM.validate` overrides for Ollama / LM Studio's 2-storage edge case. **`tests/test_plugin_self_containment.py` locks the contract** with 7 invariant classes (forbidden-imports / no-router-outside-nodes / per-plugin self-registration / registry-API sanity / stale-paths-absent / main.py-does-not-mount / WS_HANDLERS-non-empty); same `inspect.getsource` introspection style as `test_credential_broadcasts.py`. Wire format unchanged — frontend WS message-type strings (`whatsapp_status`, `twitter_oauth_login`, `google_oauth_status`, `android_relay_connect`, etc.) and HTTP route paths (`/api/twitter/callback`, `/api/google/callback`, `/api/android/*`) are byte-identical post-migration.
- **Typed CloudEvents factory pattern for new broadcasts** (b6aecd3): every new server→FE lifecycle broadcast follows the recipe locked by `broadcast_credential_event`, `broadcast_agent_progress`, and `broadcast_agent_capability`. (1) Add a typed factory — convention: `source = "opencompany://services/<area>"`, reverse-DNS `type = "com.opencompany.<area>.<event>"`, and `subject = <exact primary entity id>`. Put snake_case workflow/execution scope inside `data`; CloudEvents extension attribute names MUST be lowercase-alphanumeric, so the historical top-level `workflow_id`/`trigger_node_id`/`correlation_id` fields are compatibility debt and are forbidden on new contracts. (2) Add a broadcaster wrapper that emits `broadcast({type: "<wire_key>", data: event.model_dump(mode="json", exclude_none=True)})`. (3) FE validates `specversion`, ID/source/type, subject ownership, and event-specific payload invariants, deduplicates by `(source,id)`, then writes only the exact scoped entity. (4) Add contract tests in `test_events.py` and keep `tests/test_status_broadcasts.py`'s raw-callsite allowlist empty. Raw `node_status` remains allowed only as the established high-frequency latest-state/reconnect projection paired with a typed lifecycle occurrence; it is not the event of record.
- **Plugin-folder location for plugin-specific CloudEvents factories** (RFC §6.4): when a typed event is owned by one plugin (telegram, whatsapp, android, email, google, master_skill, agent_builder, webhook_trigger, chat_trigger, agent), the factory + `broadcast_<event>` wrapper live in [`server/nodes/<plugin>/_events.py`](./server/nodes), NOT on `WorkflowEvent` / `StatusBroadcaster` directly. Telegram's [`_events.py`](./server/nodes/telegram/_events.py) is the canonical template — `broadcast_telegram_status` constructs the envelope via a plain factory function and calls `get_status_broadcaster().broadcast(...)` itself. Latest example: [`server/nodes/skill/master_skill/_events.py`](./server/nodes/skill/master_skill/_events.py) (`broadcast_skill_lifecycle("created" \| "updated" \| "deleted" \| "content_saved", name=..., **data_extra)`) — replaces four raw-dict broadcasts in `handle_create_user_skill` / `handle_update_user_skill` / `handle_delete_user_skill` / `handle_save_skill_content` (the last one was previously silent). FE routes wire key `skill_lifecycle` to invalidate `userSkills` + `folderSkills` queries and drop the `skillContent` cache on delete + content_saved so the Master Skill panel refreshes live across every connected client. Cross-cutting events (credential, workflow_lifecycle, agent_progress, agent_capability, node_parameters_updated) keep their `WorkflowEvent` classmethods + `StatusBroadcaster` wrappers — only plugin-scoped events move to the plugin folder.
- **Operator-metadata fields in credential panels** (f746ddf): when a credential panel needs a non-credential follow-up field (e.g. Telegram bot token + owner chat id), declare it as a second entry in the provider's `fields` array in `server/config/credential_providers.json`. The first field stays the validate/connect target; subsequent fields render below as plain text inputs via `SecondaryFieldRow` in `ApiKeyPanel.tsx` (shadcn `Input` + `Label` + `<ActionButton intent="save">`). Each field carries an optional `help` slot (`FieldDef.help` / `ServerFieldDef.help`) for always-visible explanatory text under the input. Save writes via `panel.actions.save(field.key, value)` → `auth_service.store_api_key` (which is permissive about provider string). The credential class must declare the new key in `extra_fields` (`Credential` subclass attribute) so the storage layer accepts it. Reference: `telegram_owner_chat_id` field.
- **Telegram owner detection — three layers** (f746ddf): the bot owner is captured by THREE independent paths so the realistic setup flows all converge to a working state. (1) **Explicit field**: secondary `FieldDef` in the credentials modal lets the user paste their Telegram user ID directly (recommended; works without DM'ing the bot). (2) **Pre-poll peek** in `connect()` via `_capture_owner_from_pending_updates()` — calls `bot.get_updates(timeout=0)` BEFORE `start_polling(drop_pending_updates=True)` discards the queue, scans for any historical private DM, captures + persists atomically. (3) **Atomic write-through in `_on_message_received`** — persists FIRST, sets in-memory ONLY on success. Invariant: "in-memory has owner ⇒ DB has owner" so a process restart can re-capture cleanly. Failure logged at `ERROR` with `exc_info=True` (was `WARNING` previously, masking persist failures). The lazy fallback in `telegram_send.py` (read DB → `service.set_owner`) sits on top of all three.
- **Supervisor Job Object loud-failure (cli/tree.py + cli/supervisor.py)** (8a64eb9): the Windows process-tree mechanism depends entirely on a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (no fallback — `start_new_session` is POSIX-only). `pywin32>=308` is a hard supervisor dependency on Windows; the declaration in the root `pyproject.toml` (where `opencompany-cli` lives — `cli/pyproject.toml` was removed as a stale duplicate) carries an inline comment so future maintainers don't drop it. `_JobObject.__init__` splits failures into explicit `ImportError` (with reinstall command) and `Exception` (with `repr`) branches — both write to `stderr` instead of swallowing. `_JobObject.add()` verifies enrollment via `win32job.IsProcessInJob(handle, self._handle)` after assignment; on mismatch it queries `IsProcessInJob(handle, None)` so the warning explicitly names the wrapper-job hypothesis (npm / bun / conhost wrappers occasionally place us in their own non-nesting Job). `supervisor._spawn_once` checks `add_to_job` return value and emits a yellow `WARN: pid=N not enrolled in Job Object` per child if False. Without these guards, a stale pywin32 install or a wrapping Job Object silently leaks orphan Python processes on every Ctrl-C and they accumulate to the point of holding SQLite locks that block subsequent backend startup.
- **Credentials envelope-shape invariant** (dc94cde): the `useCredentialPanel` query stores `{values: CredentialFormValues, hadStored: boolean}` (an envelope, NOT a flat dict). The earlier `writeValues` called `setQueryData<CredentialFormValues>` with a spread `(prev) => ({...prev, [key]: value})`, which at runtime merged the typed character at the envelope level next to `values` and `hadStored` instead of inside `.values` — the input selector `panel.values[field.key]` re-rendered with the original (server) value on every keystroke and the input felt frozen. Fix: `writeValues` now preserves the envelope shape and updates only the inner `values` dict; `hadStored` is preserved verbatim (it reflects real server state, not local edits). When introducing other queries shaped as `{data, meta}` envelopes, follow the same pattern — never spread `prev` as if it were the inner payload.
- **`core.paths` is the SSOT for on-disk locations**: only generic helpers (`data_path` / `packages_dir` / `package_dir(name)` / `daemons_dir` / `workspaces_dir` / `workspace_dir(workflow_id)` / `example_workflows_dir`). Plugin-specific subpaths are composed inline at the plugin's call site — adding a new package never requires touching `core.paths`. Reference subtree under `opencompany_root()` (= `~/.opencompany/` per default `DATA_DIR`): `workspaces/<slug>/` (per-workflow scratch), `daemons/` (supervised event-source daemon cwds — `stripe listen` etc.; shared root by default, no per-namespace subdir), `claude/` (Claude Code's `CLAUDE_CONFIG_DIR` state — composed as `data_path("claude")` in `nodes/agent/claude_code_agent/_oauth.py`), `packages/` (OpenCompany-managed install root). `packages/` holds a **single shared npm tree** — one `package.json` + `package-lock.json` + `node_modules/` covering every OpenCompany-managed npm package (`@anthropic-ai/claude-code`, `edgymeow`, `agent-browser`). Each plugin's `_install.py` runs `npm install <pkg> --prefix <packages_dir>` which extends the shared tree idempotently; npm manages everything else. Non-npm binaries (Stripe CLI, Temporal CLI) sit under sibling subdirs `packages/stripe/`, `packages/temporal/` via `package_dir(name)`. Also under `<DATA_DIR>/`: `whatsapp/` (WhatsApp session DB), `credentials.db` / `workflow.db` / `temporal.db`. Pre-fix `packages_dir()` routed through `platformdirs.user_cache_path("OpenCompany")` (`~/.cache/OpenCompany/` etc.) and the Temporal CLI sat in its own `pooch.os_cache("opencompany-temporal")` namespace — operators reported both as "not local". The WhatsApp Go bridge (`edgymeow`) used to be a top-level pnpm dep at `<repo>/node_modules/edgymeow/`; now OpenCompany-managed via `nodes/whatsapp/_install.py` like the other CLIs. Daemon cwds used to live under `workspaces/_<namespace>/` and polluted per-workflow scratch with framework state. Out of scope: globally-installed binaries (Himalaya — system package manager). Shipped seed workflows are the lone exception — `example_workflows_dir()` is hardcoded to `<repo>/.opencompany/workflows/` (git-tracked seeds, NOT under `DATA_DIR`) so they survive `company clean` and stay at the same path across `DATA_DIR=~/.opencompany` vs `DATA_DIR=.opencompany` configs.
- **`company clean` preserves `.opencompany/{workflows,deploy,packages}/`**: `cli/commands/clean.py` iterates the canonical `<repo>/.opencompany/` and pre-rebrand `<repo>/.machina/` roots and skips anything in `_OPENCOMPANY_KEEP = frozenset({"workflows", "deploy", "packages"})`. Wipes `claude/`, `workspaces/`, `*.db` as before. `workflows/` holds the shipped seed JSONs (git-tracked); `deploy/` holds Terraform state for LIVE cloud resources (only `company deploy destroy` removes it); `packages/` holds the OpenCompany-managed binaries (Temporal CLI ~114 MB, Stripe CLI, shared npm tree) — re-fetchable but expensive, so clean+build cycles stay offline-safe cache hits. Test `cli/tests/test_clean.py::test_opencompany_keep_preserves_workflows_deploy_and_packages` locks the keep-list.
- **No raw `print()` outside three sanctioned helpers**: `main._startup_log` (pre-logger boot markers), `core.container._clog` (DI-bootstrap markers), and `nodes.code.python_executor.captured_print` (the sandbox builtin handed to user code). Everything else goes through `logger = get_logger(__name__)`. The supervisor prefixes every aggregated line with `[HH:MM:SS.fff]` so no inner `TimeStamper` is needed; structlog console mode is deliberately timestamp-less. Test `server/tests/test_no_raw_prints.py` AST-walks the tree and flags any unsanctioned `print(...)` call.
- **`configure_logging(settings)` must run BEFORE plugin self-registration imports**: in `main.py`, `Settings()` + `configure_logging(settings)` + `init_tracing()` + `get_logger(__name__)` happen ahead of `from core.container import container` and `from routers import …`. Otherwise plugin folders that register on import call `logger.debug(...)` while structlog is still on its default processor chain (which includes `TimeStamper` + no `filter_by_level`) — symptom is double timestamps + debug records leaking despite `LOG_LEVEL=INFO`.
- **Plugin-identifier shape validator (`services/plugin/identifiers.py`)** (`7700f87`): `NODE_TYPE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"` + `is_valid_node_type(value)`. Single source of truth used by FastAPI URL routes (`Path(pattern=NODE_TYPE_PATTERN)` in `routers/schemas.py`) AND internal helpers (`nodes/_visuals.py::get_plugin_meta` / `get_plugin_icon_path`). Hardens the icon-resolution endpoints: the registry lookup `get_node_class(node_type)` already gates the taint in practice, but CodeQL can't follow registry-mediated sanitization, so `fullmatch` at the function boundary states the constraint explicitly. **It does NOT close the CodeQL `py/path-injection` alerts, despite what this line used to claim.** Verified against the live SARIF (2026-07-26): `_visuals.py:191`, `_visuals.py:196` and `schemas.py:144` are still reported on every scan and were closed by *manual dismissal*, not by the regex. The reason is structural and worth knowing before you attempt a fix: the flagged sink is the path-construction/`resolve()` call itself, so no guard placed after it can clear the taint, and an interprocedural `fullmatch` in a helper is not treated as a barrier either. **`py/path-injection` on a containment helper is dismissed here, not coded around** — see `nodes/filesystem/_backend.py::resolve_within` and alerts #29-33, #39, #140, #142, #154-157. Contract test (`tests/services/test_identifiers.py`, 47 cases) locks the regex against `../etc/passwd`, `..\windows\system32`, `foo\x00bar`, `%2e%2e%2fetc`, `foo;bar`, `${HOME}`, `` foo`bar ``, CRLF, and non-string input.
- **CLI recovery-resilience invariants** (May 2026): `python -m cli clean` must run end-to-end on a system Python with no third-party deps. Achieved by: (a) every verb in [`cli/cli.py`](./cli/cli.py) is a lazy stub that imports its impl inside the function body — `import cli.cli` does NOT pull in `anyio` / `psutil` / `cli.supervisor` / sibling verb modules (300 → 131 modules at boot). (b) [`cli/_common.py`](./cli/_common.py) defers `cli.supervisor` imports inside `build_backend_spec` so importing `cli._common` (and therefore `clean.py`) doesn't drag in rich. (c) [`cli/platform_.py`](./cli/platform_.py) lazy-imports `platformdirs` inside the four `user_*_dir` helpers — module loads without the wheel. (d) [`cli/commands/clean.py`](./cli/commands/clean.py) uses stdlib `print()` (no rich) + lazy `cli.ports` import inside `_kill_running_processes` wrapped in `try/except ImportError` (skip-with-warning if psutil is missing). (e)+(f) retired July 2026: the CLI no longer supervises Temporal at all — the backend lifespan owns the dev server via `services.temporal._runtime.ensure_started()` (`cli/commands/_temporal_specs.py` and the `_supervised_runtime.py` shim were deleted; long-running specs spawn the server venv interpreter directly via `cli.platform_.server_venv_python`, no resident `uv run` parents). (g) `daemon` is now a verb-per-file package ([`cli/commands/daemon/`](./cli/commands/daemon/) — `_state.py` + `start.py` + `stop.py` + `status.py` + `restart.py`) following pdm's `commands/venv/` shape; PID-file resolution is a function (`pid_dir()`) not a module-level attribute so platformdirs only loads at call time.
- **Wave 13 canary fixes** — six load-bearing corrections to the Wave 12 event framework. See [docs-internal/event_framework.md → Wave 13 fixes](./docs-internal/event_framework.md#wave-13-fixes) for the full breakdown. Key invariants to remember when working on canary triggers: (1) `register_canary_trigger_type(node_type, cloudevent_type)` requires the CloudEvents reverse-DNS string as second arg — must match the producer's `WorkflowEvent.type` exactly or the deployment manager's `EventType` SA won't match `dispatch.emit`'s Visibility query (silent firing failure). Diverging re-registration raises `ValueError` so plugin upgrades surface loudly. (2) Plugin `_events.py` for canary-registered triggers is canary-only — no `event_waiter.dispatch` / `send_custom_event` calls (those have zero consumers in canary-on mode). `dispatch.emit(envelope, wire_routing_key=...)` handles BOTH Temporal Signal fan-out AND in-process WS broadcast. (3) `TriggerListenerWorkflow` + `PollingTriggerWorkflow` call `broadcast_trigger_status_activity` before/after each child spawn for firing-pulse UX (matches legacy `triggers.py` collector/processor). (4) `PollingTriggerNode.as_poll_activity` returns `seen_ids: list(current)` — NOT `list(prior_seen | current)` (the latter grows unboundedly; Gmail at ~100/day hit ~36K entries in a year). The legacy `_build_poll_coroutine` does `seen = set(current)` at end of cycle for the same reason. Visibility-filtered providers (Gmail-unread) re-emit on re-surface, which is correct. (5) Canary trigger output persistence: `MachinaWorkflow.run`'s pre-executed loop schedules `store_node_output_activity` for every firing trigger so `ParameterResolver` can resolve `{{triggerNode.field}}` in downstream nodes (the legacy `_execute_from_trigger` did this via `_store_output(trigger_node_id, "output_0", ...)`; canary skipped it pre-fix). Skips non-firing siblings (`_trigger_output={"not_triggered": True}`). (6) `DeploymentManager.cancel` sweeps stuck node statuses via `_clear_stuck_node_statuses(workflow_id, include_waiting=True)` + emits terminal `update_workflow_status(executing=False, workflow_id=...)`. Without these the FE leaves downstream nodes glowing forever after deployment cancel and the toolbar Start/Stop indicator stays at `executing=True`. The delegation guard inside `_clear_stuck_node_statuses` still protects in-flight fire-and-forget child agents.
- **Deployment reconcile snapshot on WS connect** (de8df87): a stale FE `deploymentStatus.isRunning=true` used to survive a backend restart because the in-memory `DeploymentManager._deployments` dict was wiped (no recovery wiring — the `RecoverySweeper.set_recovery_callback` extension point exists but is intentionally unwired; see the "workflow status not properly recognized" investigation). The Start button therefore stayed showing "Stop" forever. Fix: new `WorkflowEvent.deployment_snapshot(running_workflow_ids)` typed factory (`source = opencompany://services/workflow`, `type = workflow.deployment.snapshot`) + `broadcaster._send_deployment_snapshot(websocket)` that runs at the tail of `broadcaster.connect()` (single-target, NOT fan-out — only the just-connecting client needs to reconcile). FE `case 'deployment_snapshot'` in `WebSocketContext.tsx` iterates `useAppStore.workflowUIStates` and clears `isExecuting=true` on any workflow NOT in the snapshot's running set (empty list is meaningful — this is the load-bearing reset). Also reconciles `deploymentStatus.isRunning` for the active workflow. Distinct from `workflow_lifecycle("deployment.started")` (state-transition edge event) — the snapshot is an idempotent state dump tied to client connect, not a transition.
- **Agent progress badge — defensive status invariant** (15b8d9d): `AIAgentNode`'s "N / max" iteration badge is gated on `isExecuting && typeof iteration === 'number' && typeof maxIterations === 'number'` where `isExecuting = nodeStatus?.status === 'executing'`. The `agent_progress` handler USED to only set `data.iteration` / `data.max_iterations`, relying on a prior `node_status='executing'` broadcast to have already stamped the status. If agent_progress arrived first (race, single-step agent completion, out-of-order Temporal activity delivery), `status` stayed undefined and the badge was hidden even though the iteration data was populated. Fix: the agent_progress handler now defensively sets `status='executing'` on the slot — but preserves `'success'` / `'error'` if a later terminal broadcast already flipped the slot (terminal states win over the resurrection).
- **Temporal Worker tuner exclusivity** (Wave 16.4, 190c896): `Worker.__init__` rejects `tuner` alongside ANY of `max_concurrent_workflow_tasks` / `max_concurrent_activities` / `max_concurrent_local_activities` / `max_concurrent_nexus_tasks` — the tuner OWNS every slot supplier via its `WorkerTuner.create_composite(workflow_supplier, activity_supplier, local_activity_supplier, nexus_supplier)` and cannot coexist with the individual max-count kwargs. In `TemporalWorkerPool.start`, ALL FOUR max-count kwargs must be pulled from the base `worker_kwargs` when `tuner=` is added; the ai-heavy / browser queues use the tuner and preserve their 10-workflow-slot ceiling via `_tuner_for -> FixedSizeSlotSupplier(10)` on the workflow_supplier slot. The framework worker at `worker.py:188` does NOT use a tuner, so it keeps the plain `max_concurrent_workflow_tasks=10` + `max_concurrent_activities=self.pool_size` kwargs directly. `TEMPORAL_WORKER_POOL_ENABLED` defaulted to True in the same commit (`test_task_queue_coverage.py::TestWorkerPoolDefaultOn` locks the default via source-introspection so the flip survives a hasty revert).
- **No hardcoded port numbers in code or docs**: `.env.template` is the single place port numbers live (`.env` overrides, process env wins). Python resolves via `core.env_defaults.env_value/env_int` (raises loudly when unconfigured — never a numeric fallback literal); the Vite config and the Node executor sidecar require their env vars the same way; scripts/CI/installers parse the template. Docs reference env var names (`PYTHON_BACKEND_PORT`, `TEMPORAL_UI_PORT`, ...) instead of numerals; user-facing quickstarts may show the default app URL once. See `docs-internal/cli_services_integration.md` for the plugin-daemon recipe.
- **Multi-vendor nodes: one node, a `provider` dropdown, two registries.** `nodes/speech/` is the reference and the first multi-credential plugin in the repo (`credentials = (A, B, C)`; `ctx.connection(id)` is a dict lookup over that tuple). Four load-bearing rules. (1) **Imperative `@Operation` only** — the declarative `routing=` path resolves `credentials[0]` (`base.py:571`), so a routed op authenticates every provider with the first key in the tuple and `test_plugin_contract.py` would not catch it. (2) **Registry membership IS the capability** — one registry per direction, the node's provider enum literally *is* `tts_providers()`, so a synthesis-only vendor cannot be selected for transcription and there is no `supports_x` flag to keep honest. (3) **Capabilities live in JSON** (`server/config/speech_defaults.json`), per-model overrides resolved exact -> longest-prefix -> `_default`, boolean flags defaulting **permissive**; no shared code branches on a vendor name. (4) **Vendor divergence stays in the vendor module** — auth scheme, query-vs-body, response shape. The v1 set diverges on all three axes and three of those divergences fail *silently* (ElevenLabs ignores a body-placed `output_format`, Deepgram ignores body options entirely, Sarvam returns a base64 array), so each has a test asserting the outgoing request rather than the parsed result. Full reference: **[Speech Provider RFC](./docs-internal/speech_provider_rfc.md)** + [plugin_system.md -> Multi-credential nodes](./docs-internal/plugin_system.md#multi-credential-nodes).
- **Never name a Params field `model` or `api_key` on a node that also has a `provider` field.** An effect at [`ParameterRenderer.tsx:866`](./client/src/components/ParameterRenderer.tsx#L866) keys on those two literal names: with a sibling `provider` field present it overwrites `model` with the *chat-model* list and clears `api_key` — and it never validates that the provider is an LLM provider, so `provider: "elevenlabs"` still triggers it and the field is wiped the moment the user picks a vendor. Line 930 also writes options onto the literal string `'model'`, not `parameter.name`. Prefix instead (`tts_model` / `stt_model`), locked by a test in `tests/nodes/test_speech.py`. Other reserved sibling names with name-based magic in that file: `parameters`, `message_type`, `group_id`, `group_name`, `channel_jid`, `sender_number`, `sender_name`, `session_id`, `service_id`, `action`.
- **`usable_as_tool = True` auto-hides both canvas handles.** `base.py:215` sets `hide_input_handle` / `hide_output_handle` to `True` unless the class declares them, so a dual-purpose node that must stay wirable has to declare both `False` explicitly. Symptom: the node works fine as an AI tool but cannot be connected to anything on the canvas.
- **Temporal LLM tool calls carry unmerged `tool_args`, and ToolNodes take `execute_as_tool`.** The AgentWorkflow schedules each tool call as the tool's own per-type activity; the payload carries BOTH the merged `node_data` (legacy/dual-purpose contract) AND the model's raw arguments as `tool_args`, forwarded through `as_activity`'s extras into the legacy handler, which routes ToolNodes through `execute_as_tool` for real `ToolInput` validation. Without this, the merged dict validated against `Params` (`extra="ignore"`) and a split-schema tool's model arguments were silently DROPPED — every Simple Memory `remember` degraded to a harmless `list`, reported success, and stored nothing. Dual-purpose ActionNodes deliberately keep their documented merged-params behavior. Locked by `tests/nodes/test_tool_call_dispatch.py` (including an end-to-end store assertion). Related guard: a function-local `import` makes its name local for the WHOLE function, so any use above it raises `UnboundLocalError` at runtime that `py_compile` cannot catch — a mid-function `import json` below the conversation size guard failed every Context-connected run this way (AST-locked in `test_agent_workflow.py`).
- **Inspection panels opt out of the global `refetchOnMount: false`.** The QueryClient default ("trust the cache; the broadcast bridge keeps it in sync") assumes an active observer exists when the invalidation lands — but Context/Memory data mutates while those panels are CLOSED, so the broadcast invalidates a query nobody observes and a later mount renders stale cache until a manual Refresh. `ContextPanel` and `MemoryToolPanel` therefore declare `refetchOnMount: 'always', staleTime: 0`; the `context.updated` / `memory.updated` broadcasts cover the panel-open case. Apply the same override to any future panel whose data is written by agent runs rather than by the panel itself.
- **Media leaves a node as a reference, never as bytes.** `services/media/` + `FileRef` (~400 B, `extra="forbid"`) with `AudioRef(FileRef)` as the probed-container narrowing — set `kind="audio"` only when the duration actually came from `inspect_audio`, because a fabricated one silently mis-bills per-second providers; the `gallery` node therefore emits `kind="file"` even for a `.wav`. A node result is persisted 3x, broadcast 2x, retained in `_status` forever, aggregated into the workflow result, copied into every downstream activity *input*, and — if `usable_as_tool` — serialized into an LLM message; a 12 MB base64 payload therefore fails the activity, **retries 3x re-billing the provider each time**, and reports a generic error. Nodes call `write_audio` (out) and `coerce_file_param` (in); both enforce containment through `resolve_within`, which closed a live traversal that let `audio_file="../../credentials.db"` read the encrypted credential store and upload it. **id/slug trap:** workspace dirs are named by the mutable `Workflow.slug` while a ref stores the immutable `workflow_id` so refs survive rename — `workspace_root()` raises rather than composing a wrong path, and the lookup now lives in `services/workspace_locator.py`, which every database-holding caller delegates to (the HTTP route, the gallery WS handlers, `WorkflowService`). Reads may fall back to the anonymous `"default"` workspace; **mutating callers must pass `allow_default=False`**, or a stale tab's unresolvable id destroys files in a different context than the caller believes. Full reference: **[Media Transport](./docs-internal/media_transport.md)**.
- never use emojis in prints
