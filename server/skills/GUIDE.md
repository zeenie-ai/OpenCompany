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
├── aws/                      # aws-skill (AWS SDK: EC2, S3, any API operation)
├── cloudflare/               # cloudflare-skill (cf CLI)
├── coding_agent/             # Code execution skills
│     python-skill, javascript-skill, monty-skill, file-read-skill,
│     file-modify-skill, fs-search-skill
├── employee/                 # Home Settings > Skills catalogue (see "The `employee` folder" below)
├── gcloud/                   # gcloud-skill (Google Cloud CLI)
├── github/                   # github-skill (gh CLI)
├── language_agent/           # speech-skill, translation-skill
├── payments_agent/           # stripe-skill, dodo-payments-skill
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
└── web_agent/                # Web automation skills
      browser-skill, http-request-skill, apify-skill,
      crawlee-scraper-skill, tikhub-skill, treg-skill, proxy-config-skill, duckduckgo-search-skill,
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
| `allowed-tools` | No | Space-delimited tool identifiers or supported visual aliases. Callable names come from each plugin's `tool_name` or dynamic binding, not a universal snake_case conversion. The loader also uses these tokens to resolve a node's icon/color. For example, Python's callable is `python_code`, while the shipped visual token `python_executor` identifies `pythonExecutor`. Check both the actual tool schema and visual resolution. |
| `metadata` | No | Arbitrary key-value pairs (author, version, category). **Don't set `icon`/`color` for skills that target a node** — those are resolved from the first `allowed-tools` entry that names a node (`SkillLoader._parse_skill_metadata`): the icon from that plugin's `icon_<type>.svg` / `icon.svg`, else its `visuals.json` entry; the colour from its `meta.json`, else `visuals.json`. `meta.json` icon references (`lucide:...`) are not consulted, so a skill targeting a node that uses only such a reference gets no icon from it. Skills with no node target (personality skills, memory operators, autonomous patterns) **may** set inline `icon` and `color` — they're the only visual source for those. |

### Name Format Rules

- Lowercase letters and numbers only
- Words separated by single hyphens
- No consecutive hyphens
- Examples: `my-skill`, `brave-search-skill`, `python-skill`
- Invalid: `My_Skill`, `my--skill`, `MySkill`

### Node types, callables, and visual aliases

These are separate identifiers:

| Identifier | Authority | Example |
|---|---|---|
| Registered node type | Plugin `type` | `pythonExecutor` |
| Callable tool name | Plugin `tool_name`, or a dynamic tool binding | `python_code` |
| Skill visual token | `SkillLoader._parse_skill_metadata` and plugin/visual aliases | `python_executor` |
| Skill name | This file's `name` | `python-skill` |

The loader tries supported visual aliases and snake-to-camel resolution for
`allowed-tools`; this metadata is not a universal naming or authorization
contract. Other examples include node `shell` with callable `shell_execute`,
and `githubAction` with callable/alias `github`. Do not mechanically rename
existing tokens: that can remove their icons without changing callable names.
Teach actual tool names in examples, then verify the chosen metadata token
resolves to the intended visuals with `server/tests/test_skill_icon_resolution.py`.
Runtime grants and connected-node policy determine available tools.

## Optional Supporting Files

Skills can include additional files that the model reads on demand after loading the skill:

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

- **scripts/**: Every top-level file is read as text and declared as a resource
- **references/**: Only top-level `.md`, `.txt`, `.json` files are read and declared as resources

Nothing here is executed or put into the prompt up front. `Skill.load` returns
the instructions plus a manifest of these resources (`references/<file>`,
`scripts/<file>`); the model then pulls their text through the same tool's
`read_resource` / `search_resource` actions (`server/services/skill_runtime.py`).

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

2. **No per-skill nodes**: Master Skill is the only skill node (`SKILL_NODE_TYPES` in `server/constants.py`); it connects to an agent's `input-skill` handle. There are no dedicated palette nodes per built-in skill.

3. **At Execution Time**: Only personality skills (names ending in `-personality`) are injected eagerly (`server/services/skill_prompt.py`). On aiAgent and chatAgent, in-process and on the Temporal agent path, their full instructions **replace** the agent's system message, so the node's own `system_message` parameter is dropped. The RLM agent is the exception: it puts them in front of the task prompt under a `## Skill Instructions` heading and keeps its system message. Every other enabled skill uses progressive disclosure through the bound `Skill` tool (`server/services/skill_runtime.py`): the tool description carries a bounded name/description catalogue, `load` returns a skill's instructions and resource manifest, and `read_resource` / `search_resource` read its declared files. The system prompt is not changed for these skills.

## Skill Content Lifecycle

1. **First Load**: Instructions are read from the `SKILL.md` file on disk
2. **Seeded to DB**: On first activation, instructions are saved to the database
3. **DB is Source of Truth**: Subsequent loads read from the database
4. **Customization**: Users can edit instructions in the UI. Edits are saved to DB only
5. **Reset**: "Reset to Default" reloads from the original `SKILL.md` file on disk
