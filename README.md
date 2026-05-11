# MachinaOS

<a href="https://www.npmjs.com/package/machinaos" target="_blank"><img src="https://img.shields.io/npm/v/machinaos.svg" alt="npm version"></a>
<a href="https://opensource.org/licenses/MIT" target="_blank"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
<a href="https://discord.gg/c9pCJ7d8Ce" target="_blank"><img src="https://img.shields.io/discord/1455977012308086895?logo=discord&logoColor=white&label=Discord" alt="Discord"></a>
<a href="https://deepwiki.com/trohitg/MachinaOS" target="_blank"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>

Self-hostable workflow runtime for AI agents. Plugin-first Python backend, schema-driven React Flow frontend, Temporal-backed distributed execution, and a native-SDK LLM layer that runs against cloud providers or local servers (Ollama, LM Studio).

Think n8n's visual builder with the agent ergonomics of an SDK — workflows are JSON, nodes are typed Python plugins, and the executor is durable.

## See It In Action ↓

https://github.com/user-attachments/assets/5ee81bb3-12cf-4755-8532-7470c6f1d841

## Full Capabilities ↓

https://github.com/user-attachments/assets/5798fe61-8d26-4d3a-90aa-189bf4eec79f

## Quick Start

**Prerequisites:** Node.js 22+, Python 3.12+

```bash
npm install -g machinaos
machina start
```

Open http://localhost:3000. Backend on `:3010`, Temporal UI on `:8080`.

<details>
<summary><b>Run from source</b></summary>

Source builds require [pnpm](https://pnpm.io/):

```bash
npm install -g pnpm
git clone https://github.com/trohitg/MachinaOS.git
cd MachinaOS
pnpm install
pnpm run dev
```

The `dev` task starts the Python backend, Vite client, WhatsApp RPC, and Temporal in parallel. See [SETUP.md](docs-internal/SETUP.md) and [SCRIPTS.md](docs-internal/SCRIPTS.md).

</details>

## Architecture

```
+--------------------------+      WebSocket       +--------------------------+
|  React 19 + Vite client  | <==================> |   FastAPI backend        |
|  React Flow canvas       |   (typed RPC +       |   Plugin registry        |
|  TanStack Query cache    |   CloudEvents v1.0   |   Native LLM SDK layer   |
|  Zustand slice stores    |   broadcasts)        |   Temporal executor      |
+--------------------------+                       +-------------+------------+
                                                                 |
                                              +------------------+------------------+
                                              v                                     v
                                      +---------------+                     +---------------+
                                      | Temporal      |                     | Encrypted     |
                                      | (durable      |                     | credentials   |
                                      |  activities)  |                     |  store        |
                                      +---------------+                     +---------------+
```

- **Plugin-first backend.** One Python file per node. The plugin class declares metadata, typed input/output schemas, and an execute method; the framework auto-registers it on import. The backend spec is the single source of truth — the frontend renders entirely from server-served schemas.
- **Three execution modes** with automatic fallback: Temporal (distributed, durable) → Redis-backed parallel scheduling → sequential.
- **Event-driven deployment.** Each trigger event spawns an independent execution run with isolated context. Multiple runs of the same workflow can execute concurrently.
- **Dual-path LLM execution.** Chat completions go through a native-SDK layer to keep cold-start fast; agent runs use LangChain + LangGraph for tool calling and state graphs.
- **WebSocket-first RPC** with reliable reconnect, replay queue, request correlation, and CloudEvents v1.0 envelopes for typed broadcasts.

Full diagrams and deep-dives: **[CONTRIBUTING.md](CONTRIBUTING.md)** and [docs-internal/](docs-internal/).

## What's In The Box

### Node plugins
Categories: AI agents, chat models, social (WhatsApp / Telegram / Twitter / Discord / Slack / Signal / SMS / Webchat / Email / Matrix / Teams), Google Workspace (Gmail / Calendar / Drive / Sheets / Tasks / Contacts), Android (16 service nodes via ADB + relay), browser automation, web scraping (Crawlee, Apify, HTTP), document RAG (parsers, chunkers, embeddings, vector stores), filesystem + shell (Nushell), code executors (Python / JS / TS), process manager, scheduling (cron + timer), webhooks, location (Google Maps), payments (Stripe), residential proxies.

### LLM providers (11 chat-model backends)
| Provider     | Path        | Models                                                                |
|--------------|-------------|------------------------------------------------------------------------|
| OpenAI       | Native + LC | GPT-5.x, GPT-4.1, o-series (reasoning effort), GPT-4o                  |
| Anthropic    | Native + LC | Claude Opus 4.x, Sonnet 4.x, Haiku 4.5 (budget thinking)               |
| Google       | Native + LC | Gemini 3-pro/flash, 2.5-pro/flash/flash-lite (thinking budget)         |
| DeepSeek     | Native + LC | deepseek-chat, deepseek-reasoner                                       |
| Kimi         | Native + LC | kimi-k2.5, kimi-k2-thinking                                            |
| Mistral      | Native + LC | mistral-large, mistral-small, codestral                                |
| Groq         | LC          | Llama 3.x/4, Qwen3-32b, GPT-OSS                                        |
| Cerebras     | LC          | Llama 3.1, GPT-OSS, Qwen-3-235b                                        |
| OpenRouter   | Native + LC | 200+ models via unified API                                            |
| **Ollama**   | Native      | Local models — context length and capabilities probed via official SDK |
| **LM Studio**| Native      | Local models — context length and capabilities probed via official SDK |

### Specialized agents
17 agent types covering Android control, web automation, coding, task management, social messaging, travel planning, productivity (Google Workspace), payments, consumer interactions, autonomous loops, and recursive language models. Team-lead agents (AI Employee, Orchestrator) accept other agents as teammates and auto-expose them as delegation tools.

### Skills system
Markdown-driven, editable in-UI. Skills carry their own instructions, allowed tools, and metadata; defaults live on disk as `SKILL.md` files and get seeded into the database on first load. Folder layout maps to agent specialization (assistant, android, coding, productivity, social, terminal, web, etc.).

### Code execution
Sandboxed Python with curated stdlib imports, plus JavaScript and TypeScript executors. The process-manager node owns long-running children (dev servers, watchers, build tools) and streams their output to the Terminal tab.

### Filesystem isolation
Per-workflow workspace directory. Filesystem and shell nodes operate in a sandboxed virtual mode — path validation rejects traversal attempts uniformly across Windows and POSIX. Default shell is **Nushell** (same grammar everywhere), with fallback to the host's native shell when Nushell isn't installed.

## Configuration

Credentials live in a separate encrypted SQLite database with field-level encryption (Fernet + PBKDF2-SHA256, 600k iterations per OWASP 2024). OAuth tokens and API keys use separate storage paths by design, and refresh tokens never live in process memory.

OAuth redirect URIs are derived at runtime from the request context — no port hardcoding, works identically in dev and behind a reverse proxy. Credential backends are pluggable: local encrypted SQLite, OS-native keyring, or AWS Secrets Manager.

Click **Credentials** in the toolbar UI to connect providers.

## Documentation

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — repository map, architecture diagrams, contribution recipes
- **[server/nodes/README.md](server/nodes/README.md)** — 5-minute plugin recipe + folder map + shared helpers
- **[docs-internal/](docs-internal/)** — full architecture index: execution engine, Temporal, native LLM SDK, event waiter, credentials encryption, status broadcaster, RLM, Deep Agent, Claude Code agent, performance, build pipeline
- **Hosted docs:** https://docs.zeenie.xyz/
- **DeepWiki:** https://deepwiki.com/trohitg/MachinaOS

## Community

[Discord](https://discord.gg/c9pCJ7d8Ce) — help, feedback, and design discussions.

## License

MIT
