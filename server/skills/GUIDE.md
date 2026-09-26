# Skill Creation Guide

This guide explains how to create new skills for OpenCompany.

## Folder Structure

Skills are organized in subfolders under `server/skills/`:

```
server/skills/
├── GUIDE.md                  # This file
├── assistant/                # General-purpose assistant skills
│     advisor, agent-builder-skill, assistant-personality, compaction-skill,
│     data-skill, humanify-skill, memory-skill, skill, subagent-skill,
│     task-manager, vision-skill, write-todos-skill
├── android_agent/            # Android device control skills
│     personality + one skill per Android service node (battery, wifi,
│     bluetooth, location, app-launcher, app-list, audio, screen-control,
│     camera, motion, environmental)
├── autonomous/               # Autonomous agent patterns
│     code-mode-skill, agentic-loop-skill, progressive-discovery-skill,
│     error-recovery-skill, multi-tool-orchestration-skill
├── cloudflare/               # cloudflare-skill (cf CLI)
├── coding_agent/             # Code execution skills
│     python-skill, javascript-skill, monty-skill, file-read-skill,
│     file-modify-skill, fs-search-skill
├── gcloud/                   # gcloud-skill (Google Cloud CLI)
├── github/                   # github-skill (gh CLI)
├── language_agent/           # speech-skill, translation-skill
├── payments_agent/           # stripe-skill
├── productivity_agent/       # Google Workspace + Microsoft 365 skills
│     google-gmail-skill, google-calendar-skill, google-drive-skill,
│     google-sheets-skill, google-tasks-skill, google-contacts-skill,
│     ms-mail-skill, ms-calendar-skill
├── rlm_agent/                # rlm-reasoning-skill
├── social_agent/             # Social messaging skills
│     whatsapp-send-skill, whatsapp-db-skill, twitter-send-skill,
│     twitter-search-skill, twitter-user-skill
├── task_agent/               # Scheduling skills (timer-skill, cron-scheduler-skill)
├── terminal/                 # Shell skills (shell, bash, powershell, wsl, process-manager)
├── travel_agent/             # geocoding-skill, nearby-places-skill
├── vercel/                   # vercel-skill (Vercel CLI)
├── vertex_agent/             # vertex-agent-skill, vertex-agent-admin-skill
└── web_agent/                # Web automation skills
      browser-skill, http-request-skill, apify-skill,
      crawlee-scraper-skill, tikhub-skill, proxy-config-skill, duckduckgo-search-skill,
      brave-search-skill, serper-search-skill, perplexity-search-skill
```

The tree above is a snapshot — the source of truth is the live glob
`server/skills/*/*/SKILL.md`.

Each top-level folder (e.g. `assistant`, `android_agent`) appears as an option in the Master Skill node's folder dropdown. Skills inside are discovered recursively via `SKILL.md` files.

## Creating a New Skill

### 1. Create the skill directory

```
server/skills/<folder>/<skill-name>/SKILL.md
```

- `<folder>` - Group folder (use an existing one or create a new one)
- `<skill-name>` - Lowercase with hyphens (e.g. `my-new-skill`)

### 2. Write the SKILL.md file

Every skill requires a single `SKILL.md` file with YAML frontmatter followed by markdown instructions:

```markdown
---
name: my-new-skill
description: Brief description visible to the LLM when listing available skills
allowed-tools: tool1 tool2
metadata:
  author: your-name
  version: "1.0"
  category: general
---

# My New Skill

Instructions for the AI model go here. Write in clear markdown.
The AI reads these instructions when the skill is activated.

## What This Skill Does

Describe the skill's capabilities.

## How to Use

Provide usage guidelines, examples, and constraints.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lowercase, hyphens only. Must match pattern `^[a-z0-9]+(-[a-z0-9]+)*$` |
| `description` | Yes | One-line summary shown in skill lists and to the LLM |
| `allowed-tools` | No | Space-delimited list of LLM-facing tool names (snake_case, e.g. `stripe_action`, `apify_actor`, `whatsapp_send`). The first token whose snake→camel conversion (`stripe_action` → `stripeAction`) matches a node `type` in `server/nodes/visuals.json` becomes the skill's visual source — its icon/color come from that entry via the `_visuals` handler. **Convention:** the LLM tool name MUST be the snake_case form of the node type (e.g. `stripeAction` → `stripe_action`). The same string is the plugin's `tool_name` ClassVar (locked by `server/tests/fixtures/tool_names_snapshot.json`) so the LLM and the skill agree on the name. Mismatching the snake_case (e.g. `stripe_cli` for a `stripeAction` node) silently breaks icon resolution. |
| `metadata` | No | Arbitrary key-value pairs (author, version, category). **Don't set `icon`/`color` for skills that target a node** — those are resolved from the target node's `visuals.json` entry so the skill always mirrors the canvas. Skills with no node target (personality skills, memory operators, autonomous patterns) **may** set inline `icon` and `color` — they're the only visual source for those. |

### Name Format Rules

- Lowercase letters and numbers only
- Words separated by single hyphens
- No consecutive hyphens
- Examples: `my-skill`, `brave-search-skill`, `python-skill`
- Invalid: `My_Skill`, `my--skill`, `MySkill`

### Tool naming — snake_case ↔ camelCase contract

Three places have to agree for a skill's icon to render correctly:

| Place | Form | Example |
|---|---|---|
| Plugin node `type` (in `<plugin>.py`) | camelCase | `stripeAction` |
| `server/nodes/visuals.json` key | camelCase (= node type) | `"stripeAction": { "icon": "asset:stripe", ... }` |
| Plugin `tool_name` ClassVar (snapshot: `tests/fixtures/tool_names_snapshot.json`) | snake_case (LLM tool name) | `tool_name = "stripe_action"` |
| Skill `allowed-tools` | snake_case (matches the LLM tool name) | `allowed-tools: "stripe_action"` |

The skill resolver (`SkillLoader._parse_skill_metadata`) takes each `allowed-tools` token, runs snake→camel (`stripe_action` → `stripeAction`), and looks it up in `visuals.json`. If the conversion doesn't equal the node type, no icon resolves and the skill renders without one.

**Common pitfall:** picking a "creative" LLM tool name that doesn't snake-back to the node type. For example, calling a `stripeAction` node's tool `stripe_cli` (because it wraps a CLI) breaks the convention — `stripe_cli` snakes to `stripeCli`, not `stripeAction`, so `visuals.json` lookup fails. Stick to `<node_type_in_snake_case>` unless you're prepared to also add an alias entry to `visuals.json`.

**If you do keep a short tool name, the alias entry is mandatory, not optional.** Real precedent: `githubAction` / `vercelAction` use the tool names `github` / `vercel`, and their skills rendered blank Master Skill rows until `visuals.json` gained lowercase alias entries keyed by the tool name. The alias must carry both `icon` and `color` (the color fallback via the plugin's `meta.json` is keyed by node type, so it misses too): `"github": {"icon": "lobehub:Github", "color": "#8250df"}`. Every shipped skill must resolve a non-empty icon — locked by `server/tests/test_skill_icon_resolution.py`, so a mismatch fails CI with a message pointing back here.

## Optional Supporting Files

Skills can include additional files that are loaded alongside the main instructions:

```
server/skills/assistant/my-new-skill/
├── SKILL.md              # Required: main skill file
├── scripts/              # Optional: code snippets
│   ├── helper.py
│   └── utils.js
└── references/           # Optional: reference documents
    ├── api-docs.md
    ├── config.json
    └── examples.txt
```

- **scripts/**: All files loaded as text, available to the AI as code context
- **references/**: Only `.md`, `.txt`, `.json` files loaded as reference material

## Creating a New Folder Group

To create a new skill group (appears as a new option in the folder dropdown):

1. Create a directory under `server/skills/`:
   ```
   mkdir server/skills/my-group
   ```

2. Add at least one skill with a `SKILL.md` file inside it:
   ```
   mkdir server/skills/my-group/my-skill
   # Create SKILL.md with frontmatter + instructions
   ```

3. The folder will automatically appear in the Master Skill node dropdown.

## The `employee` folder (Normal mode)

`server/skills/employee/` is what Home's Settings > Skills offers under
Discover: skills an owner can add to their library, which every employee
hired afterwards gets. They differ from the other folders in three ways:

- **No `allowed-tools`.** A hired employee may not have the tool a skill
  would teach, so these are plain instructions. With no node to take visuals
  from, each sets `icon` (a `lucide:` reference) and `color` in `metadata`.
- **Owner-facing card copy.** `metadata.title` and `metadata.summary` are what
  the card shows. `description` stays written for the model: it says when to
  load the skill.
- **Unique, non-reserved names.** The name must not appear in another
  folder, and must not be `skill` or end in `-personality`.

`server/tests/test_home_catalog_contract.py` holds the folder to these rules,
and holds the starter bundles (`client/src/features/home/hire/starters.json`)
to the skills in it. Adding a skill here needs no code change: it appears in
Discover.

## How Skills Are Used

1. **Master Skill Node**: Select a folder from the dropdown. Enable/disable individual skills with checkboxes. Edit instructions inline.

2. **Individual Skill Nodes**: Each built-in skill also has a dedicated node in the Component Palette (e.g. WhatsApp Skill, Memory Skill). These connect directly to an AI Agent's skill handle.

3. **At Execution Time**: When the AI Agent runs, enabled skills' instructions are injected into the system prompt, giving the AI context about its available capabilities.

## Skill Content Lifecycle

1. **First Load**: Instructions are read from the `SKILL.md` file on disk
2. **Seeded to DB**: On first activation, instructions are saved to the database
3. **DB is Source of Truth**: Subsequent loads read from the database
4. **Customization**: Users can edit instructions in the UI. Edits are saved to DB only
5. **Reset**: "Reset to Default" reloads from the original `SKILL.md` file on disk
