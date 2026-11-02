# Diagrams

Canonical project assets live here. Internal guides link to these files rather than vendoring copies.

## Maintainer architecture suite

[Architecture index](architecture/README.md) · [Node capability index](architecture/NODES.md) · [Provenance](architecture/catalog.json)

The new suite is pinned to `56f0b397d0b313ec550791c1e2772525a9f7cf12`: twelve current-implementation views and four separately
labeled proposal views. Each includes an ImageGen PNG, editable Mermaid, source brief and saved prompts.
Current technical guide embeds use this suite; existing SVG URLs are retained for compatibility.

## Earlier SVG and product illustrations
Dark-theme architecture diagrams in SVG and a generated product explainer in PNG.
This folder holds the project assets; nothing under `docs-internal/` vendors them.

**Original overview assets** (product views remain linked from the README):

- [how-ai-employees-work.png](how-ai-employees-work.png) (README's guide for
  everyone: input channels, employee team, browser/app/computer work, results,
  and the memory/skills feedback loop). Inputs show Chat, Email, WhatsApp,
  Telegram, Discord, Schedules, and Webhooks (Dev mode).
  App tiles show Gmail, Google Calendar, Google Drive, Google Sheets, GitHub,
  and Stripe.
  Created with built-in ImageGen using the
  onboarding's product style; the [generation prompt](how-ai-employees-work.prompt.txt)
  records the brief and source references. It is a conceptual product illustration.
- `how-it-works.svg` (editable hiring overview: connect AI, describe a job,
  review the setup, hire, and coach)
- `default-workflows.svg` (README's technical section: example workflow structures)
- `system-overview.svg`, `execution-flow.svg`, `ai-agent-routing.svg`, `node-anatomy.svg` (CONTRIBUTING)

**Retained architecture and product-panel SVGs**: `system-context`, `runtime-trust-topology`,
`workflow-execution-routing`, `durable-deployment-events`,
`plugin-agent-team-composition`, `persistence-secret-plane`,
`workspace-anatomy`, `node-configuration-anatomy`, `credentials-architecture`,
`team-operations`, `agent-context-memory`, `master-skill-editor`,
`workspace-files`, `runtime-observability-dock`. Each file's `<desc>` lists
the source files it was drawn from.

The architecture diagrams were checked against the code in September 2026;
`how-it-works.svg` was updated for Home's employee-hiring experience in October 2026.
Counts come from the live registries (`len(NODE_METADATA)`,
`len(CREDENTIAL_REGISTRY)`, `config/llm_defaults.json`), and ports are
named by their `.env.template` variables rather than numbers.
