<img width="1584" height="672" alt="OpenCompany banner" src="https://github.com/user-attachments/assets/cebd0198-4c09-4757-9407-a7ad79a7d71e" />

# OpenCompany

<a href="https://www.npmjs.com/package/@zeenie-ai/opencompany" target="_blank"><img src="https://img.shields.io/npm/v/%40zeenie-ai%2Fopencompany.svg" alt="npm version"></a>
<a href="https://opensource.org/licenses/MIT" target="_blank"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
<a href="https://discord.gg/c9pCJ7d8Ce" target="_blank"><img src="https://img.shields.io/discord/1455977012308086895?logo=discord&logoColor=white&label=Discord" alt="Discord"></a>
<a href="https://deepwiki.com/zeenie-ai/OpenCompany" target="_blank"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>

**Your own AI workforce, running on your own machine.**

OpenCompany is an open-source, self-hosted canvas for AI agent workflows — think n8n, built agent-first. Drag, drop, and connect AI agents to your email, calendar, messages, browser, phone, and 25+ other services, with 115+ nodes to build from. No code required. No subscription. No usage limits. Bring your own API keys, or run models locally with Ollama / LM Studio for free.

**[Read the docs →](https://docs.zeenie.xyz)**

## Quick Start

**Prerequisites:** Node.js 22+, Python 3.12

```bash
npm install -g @zeenie-ai/opencompany
company start
```

Open http://localhost:3000 and click the key icon (**API Credentials**) in the toolbar to connect your first AI provider.

> The canonical npm package is `@zeenie-ai/opencompany`. The unscoped `opencompany` package is unrelated to this project and is neither installed nor removed by OpenCompany's tooling.
>
> **Upgrading from MachinaOS?** Existing `~/.machina` and checkout-local `.machina` state is detected when the new `.opencompany` location does not yet exist, so databases and deployment state are not stranded. The `machina` command remains available as a deprecated legacy alias; new scripts should use `company`.

<details>
<summary><b>Run from source (for contributors)</b></summary>

```bash
npm install -g pnpm
git clone https://github.com/zeenie-ai/OpenCompany.git OpenCompany
cd OpenCompany
pnpm run build
pnpm run dev
```

The `dev` task starts the Python backend, Vite client, WhatsApp service, and Temporal in parallel. See [SETUP.md](docs-internal/SETUP.md) and [SCRIPTS.md](docs-internal/SCRIPTS.md) for details, and [CONTRIBUTING.md](CONTRIBUTING.md) for the codebase map and contribution recipes.

</details>

## See it in action

**Hello-world setup, end to end ↓**

https://github.com/user-attachments/assets/a5a5583f-bb5f-4d27-a387-8522c556e89e

**AI building itself for complex tasks ↓**

https://github.com/user-attachments/assets/035a2293-0837-4969-8b9d-8d680e023b89

**Multiple specialized loop agents orchestrating ↓**

https://github.com/user-attachments/assets/5798fe61-8d26-4d3a-90aa-189bf4eec79f

## How It Works

[![How OpenCompany Works](docs/diagrams/how-it-works.svg)](https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/docs/diagrams/how-it-works.svg)

Pick nodes from the palette, drag them onto a canvas, connect them with lines, and give your AI agent some memory and skills. Press **Run** on a node to test it in place, or press **Start** to deploy the whole workflow as a durable background listener — waiting for emails, responding to messages, checking in on a schedule, doing the work you'd rather not.

[![Default workflows that ship with OpenCompany](docs/diagrams/default-workflows.svg)](https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/docs/diagrams/default-workflows.svg)

Three example workflows load automatically on first launch. Open them on the canvas to see exactly how the pieces fit together, then edit any node and save your own version.

## What You Can Build

- **Personal AI assistants that remember.** A chat assistant that knows your calendar, reads your inbox, and follows up on tasks. Conversations are saved as readable markdown you can edit; long-term memory uses vector search so years of context stay accessible.
- **Agent teams that delegate.** Hire an **AI Employee** as a team lead, connect specialist agents (coding, web, productivity...), and the lead automatically exposes each one as a `delegate_to_*` tool — the AI decides who gets which subtask.
- **Automations that run themselves.** Recurring jobs ("every weekday at 9 AM, summarize my unread emails"), event-driven replies ("when a customer texts on WhatsApp, draft a response"), and multi-step background pipelines. Any workflow can also expose a live `/webhook/{path}` HTTP endpoint that fires on GET, POST, PUT, DELETE, or PATCH.
- **Email, calendar, and document workflows.** Send and search Gmail, manage Calendar, Drive, Sheets, Tasks, and Contacts. Read any inbox over IMAP (Gmail, Outlook, Yahoo, iCloud, ProtonMail, Fastmail, or custom servers) — including a polling trigger that fires a workflow on every new message.
- **Messaging bots.** Send and receive on **WhatsApp** (groups, contacts, newsletter channels), **Telegram** (bots with owner detection), and **Twitter/X** (post, reply, search). A unified social node normalizes incoming messages into one format so the same workflow handles them all.
- **Phone control from a workflow.** Pair your Android phone via QR code and control it from any agent: battery and network status, app launching, WiFi / Bluetooth / airplane toggles, camera, sensors, media playback — 16 device services.
- **Web automation and research.** An interactive browser with accessibility-tree navigation (click, type, screenshot); an alpha harness that drives your *real* Chrome over CDP; scraping with Crawlee and Apify actors (Instagram, TikTok, LinkedIn, Facebook, YouTube, Google Search); search via DuckDuckGo (free), Brave, Serper, and Perplexity; residential proxies with geo-targeting and rotation.
- **Code, deploys, and pull requests.** Run Python / JavaScript / TypeScript in per-workflow sandboxed workspaces, keep dev servers alive with the Process Manager node (output streams to the Terminal tab), open and merge PRs with the **GitHub** node, and ship with the **Vercel** node — both can authenticate through their own CLIs, no token pasting required.
- **Payments.** **Stripe** action node (charges, subscriptions) plus a signed-webhook receiver for reacting to payment events in real time.
- **Your own knowledge base.** RAG out of the box: parse PDFs and HTML, chunk, embed locally or via OpenAI, store in ChromaDB / Qdrant / Pinecone, query from any agent.

## AI Capabilities

### 12 providers (11 dedicated model nodes, plus xAI through the OpenAI-compatible path) — bring your own keys or run locally

| Provider     | Notes                                                                    |
|--------------|--------------------------------------------------------------------------|
| OpenAI       | GPT-5 family, GPT-4.1, o-series reasoning models                         |
| Anthropic    | Claude Fable 5, Opus 4.x, Sonnet 4.6, Haiku 4.5 — with extended thinking |
| Google       | Gemini 3 Pro/Flash, 2.5 Pro/Flash — with reasoning budgets               |
| DeepSeek     | DeepSeek V4 (Flash/Pro); chat/reasoner legacy aliases                    |
| Kimi         | Kimi K2.6, K2.5, K2.7-Code                                               |
| Mistral      | Mistral Large/Medium/Small, Codestral                                    |
| Groq         | Llama 3.x, Qwen3, GPT-OSS (ultra-fast inference)                         |
| Cerebras     | GPT-OSS-120b, GLM-4.7, Gemma-4-31b (custom AI hardware)                  |
| OpenRouter   | 200+ models via one unified API                                          |
| **Ollama**   | Run any local model on your machine — free, private, offline             |
| **LM Studio**| Run any local model with a desktop app — free, private, offline          |

Local providers (Ollama, LM Studio) are first-class — context length is detected automatically from your running server (LM Studio additionally reports vision and tool-use capability). No paid API needed.

### 16 specialized agent types

| Agent              | Specialized for                                                          |
|--------------------|--------------------------------------------------------------------------|
| **AI Employee** / **Orchestrator** | Team leads that coordinate other agents                  |
| Android Agent      | Phone control                                                            |
| Web Agent          | Browser automation, scraping, search                                     |
| Coding Agent       | Writing and running code (Python / JS / TS)                              |
| Productivity Agent | Gmail, Calendar, Drive, Sheets, Tasks, Contacts                          |
| Social Agent       | WhatsApp, Telegram, Twitter messaging                                    |
| Task Agent         | Scheduling, reminders, cron jobs                                         |
| Travel Agent       | Maps, location lookup, planning                                          |
| Payments Agent     | Stripe + financial workflows                                             |
| Consumer Agent     | Customer support, order management                                       |
| Claude Code Agent  | Anthropic's Claude Code CLI for advanced coding sessions                 |
| Codex Agent        | OpenAI Codex CLI integration                                             |
| RLM Agent          | Recursive Language Model — write code that calls itself recursively      |
| Autonomous Agent   | Code-mode loops that reduce token usage 80-98%                           |
| Tool Agent         | General-purpose tool orchestration                                       |

The Claude Code agent keeps warm interactive sessions in a pool (same session across turns, automatic resume after a crash) and runs on interactive billing — a Claude subscription login works instead of per-token API cost. The Codex agent sandboxes parallel tasks in git worktrees.

### Skills you can edit yourself

Skills are short markdown files that teach an agent how to do something well — when to use which tool, what arguments to pass, common mistakes to avoid. Edit them in the UI; changes apply immediately. Built-in skills cover Android control, Google Workspace, social messaging, web research, coding, terminal use (Bash, PowerShell, WSL, Nushell), and more.

### Memory that scales with your context window

Agents track token usage and automatically compact long conversations as you approach your model's context limit (80% by default, configurable). Compaction summarizes in five sections — Task Overview, Current State, Important Discoveries, Next Steps, Context to Preserve — so the agent picks up exactly where it left off. Anthropic and OpenAI use native API compaction; everywhere else, the agent summarizes itself.

### Cost tracking, built in

Every LLM call and API request is tracked with USD cost. See per-provider spend in the API Credentials panel. Configure your own pricing in `pricing.json` if you switch providers mid-flight.

## Built Like Production Infrastructure

- **Durable execution via Temporal.** Every node runs as an independent Temporal activity with automatic retries; cron schedules have a 24-hour catch-up window so missed ticks backfill; per-queue worker pools scale horizontally. Falls back to a local executor when disabled.
- **Credentials encrypted at rest.** API keys and OAuth tokens live in a separate `credentials.db`, encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and a PBKDF2-SHA256 key at 600,000 iterations. Nothing leaves your machine.
- **Login-gated by choice.** Runs open on localhost by default; flip on single-owner JWT auth (or multi-user mode) for shared and cloud deployments — `company deploy` enables it automatically.

## The Canvas

- **12 visual themes** — light, dark, Renaissance, Greek, Edo, Steampunk, Atomic, Cyber, Wasteland, Rot, Plague, Surveillance — each with its own icon set, sound pack, and decorative ornaments. Animations honor `prefers-reduced-motion`.
- **Drag-to-map outputs** from one node's output directly onto another's input fields.
- **Live execution animations** — nodes glow while running, AI agents show iteration counts, errors surface inline.
- **Chat + Console panel** — a resizable bottom panel with a chat pane for talking to trigger nodes, plus Console and Terminal tabs for logs and live process output.
- **Component palette** with search, categories, and a Normal/Dev mode toggle that hides advanced nodes when you don't need them.
- **5-step onboarding wizard** for first-time users, replayable any time from Settings.

## For Developers

Want to add a node, LLM provider, skill, or integration? One Python file = one node. The backend owns all the schemas; the frontend renders from them automatically. No frontend code required for most extensions.

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — codebase map, architecture diagrams, contribution recipes
- **[server/nodes/README.md](server/nodes/README.md)** — 5-minute plugin recipe + folder map
- **[docs-internal/](docs-internal/)** — deep-dive architecture docs (execution engine, Temporal, LLM layer, credentials, event system, performance, build pipeline)
- **[CLAUDE.md](CLAUDE.md)** — comprehensive project memory (great for AI-assisted contributions)
- **Hosted docs:** https://docs.zeenie.xyz/
- **DeepWiki:** https://deepwiki.com/zeenie-ai/OpenCompany

## Community

[Discord](https://discord.gg/c9pCJ7d8Ce) — the fastest way to get help, request features, and follow design discussions.

## License

MIT
