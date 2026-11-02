# Architecture and system design

Code baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Audience: OpenCompany maintainers.

Twelve implemented-architecture views and four separately labeled proposal views share a dark visual style.
The PNGs were generated with built-in ImageGen, then checked against the editable Mermaid and source briefs.
Process, storage, ownership and status boundaries carry meaning; color alone does not establish guarantees.

[Node capability index](NODES.md) · [Registry data](nodes.json) · [Provenance catalogue](catalog.json) · [Review and validation](REVIEW.md)

## Current architecture

| View | Diagram | Status | Editable source and provenance |
|---|---|---|---|
| 01 | [System context](01-system-context-v1.png) | current | [Mermaid](01-system-context.mmd) · [Evidence](01-system-context.brief.md) · [Prompt](01-system-context.prompt.txt) |
| 02 | [Local runtime](02-local-runtime-v1.png) | current | [Mermaid](02-local-runtime.mmd) · [Evidence](02-local-runtime.brief.md) · [Prompt](02-local-runtime.prompt.txt) |
| 03 | [Distributed deployment](03-distributed-deployment-v1.png) | current | [Mermaid](03-distributed-deployment.mmd) · [Evidence](03-distributed-deployment.brief.md) · [Prompt](03-distributed-deployment.prompt.txt) |
| 04 | [Frontend architecture](04-frontend-architecture-v1.png) | current | [Mermaid](04-frontend-architecture.mmd) · [Evidence](04-frontend-architecture.brief.md) · [Prompt](04-frontend-architecture.prompt.txt) |
| 05 | [Workflow execution](05-workflow-execution-v1.png) | current | [Mermaid](05-workflow-execution.mmd) · [Evidence](05-workflow-execution.brief.md) · [Prompt](05-workflow-execution.prompt.txt) |
| 06 | [Independent-node execution](06-independent-node-execution-v1.png) | current | [Mermaid](06-independent-node-execution.mmd) · [Evidence](06-independent-node-execution.brief.md) · [Prompt](06-independent-node-execution.prompt.txt) |
| 07 | [Plugin and node contracts](07-plugin-node-contracts-v1.png) | current | [Mermaid](07-plugin-node-contracts.mmd) · [Evidence](07-plugin-node-contracts.brief.md) · [Prompt](07-plugin-node-contracts.prompt.txt) |
| 08 | [Chat and agent turns](08-chat-agent-turns-v1.png) | current | [Mermaid](08-chat-agent-turns.mmd) · [Evidence](08-chat-agent-turns.brief.md) · [Prompt](08-chat-agent-turns.prompt.txt) |
| 09 | [Teams and context](09-teams-context-v1.png) | current | [Mermaid](09-teams-context.mmd) · [Evidence](09-teams-context.brief.md) · [Prompt](09-teams-context.prompt.txt) |
| 10 | [Deployment and events](10-deployment-events-v1.png) | current | [Mermaid](10-deployment-events.mmd) · [Evidence](10-deployment-events.brief.md) · [Prompt](10-deployment-events.prompt.txt) |
| 11 | [Approvals and external effects](11-approvals-effects-v1.png) | current | [Mermaid](11-approvals-effects.mmd) · [Evidence](11-approvals-effects.brief.md) · [Prompt](11-approvals-effects.prompt.txt) |
| 12 | [Persistence and secrets](12-persistence-secrets-v1.png) | current | [Mermaid](12-persistence-secrets.mmd) · [Evidence](12-persistence-secrets.brief.md) · [Prompt](12-persistence-secrets.prompt.txt) |

## Documented proposals

These views document accepted roadmaps, drafts and sketches. They do not enable features.
The Event OS views follow RFC-0005 as amended by RFC-0006/0007. Implemented but dormant approval
waiting is labeled separately from future activation. UASTL sandbox alternatives remain unresolved;
the widget registry is a pending refactor while the current renderer remains switch-based.

| View | Diagram | Status | Editable source and provenance |
|---|---|---|---|
| 13 | [Event OS data plane and source migration](13-event-os-data-plane-v1.png) | accepted-roadmap | [Mermaid](13-event-os-data-plane.mmd) · [Evidence](13-event-os-data-plane.brief.md) · [Prompt](13-event-os-data-plane.prompt.txt) |
| 14 | [Event OS control plane and resource lifecycle](14-event-os-control-plane-v1.png) | accepted-roadmap | [Mermaid](14-event-os-control-plane.mmd) · [Evidence](14-event-os-control-plane.brief.md) · [Prompt](14-event-os-control-plane.prompt.txt) |
| 15 | [UASTL connector and optimization architecture](15-uastl-v1.png) | draft | [Mermaid](15-uastl.mmd) · [Evidence](15-uastl.brief.md) · [Prompt](15-uastl.prompt.txt) |
| 16 | [Parameter widget-registry proposal](16-parameter-widget-registry-v1.png) | sketch | [Mermaid](16-parameter-widget-registry.mmd) · [Evidence](16-parameter-widget-registry.brief.md) · [Prompt](16-parameter-widget-registry.prompt.txt) |

## Reading the diagrams

- Local embedded SDK workers share the backend process; the local Temporal server is a separate child.
- Optional single-image Docker, desktop and development hosts are distinct from distributed deployment.
- Application state, Temporal history, secrets and live owner-local resources are separate boundaries.
- Graph runs, canvas Run, agent tools and direct Workspace tasks have different admission contracts.
- Live WebSocket delivery and the selected-frame worker relay do not provide a universal durable event bus.
- Approval waiting exists but ordinary new generations have not activated its AP.7 rollout.
- Node declaration/eligibility is not proof of successful provider, browser or device execution.

## Maintenance and validation

Each numbered bundle contains a PNG, `.mmd`, source brief, generation prompt and metadata record.
Targeted edit prompts and attempt records are retained. The catalogue hashes each selected asset.
Briefs record exact connections, prerequisites and known limitations; source evidence controls the review.

To update a view, review its implementation and status, edit its brief/Mermaid/prompt, generate with built-in
ImageGen, inspect the selected image at native and documentation width, and record the review and attempts.
After all sixteen views are reviewed, rebuild the catalogue:

```bash
python scripts/architecture_diagrams.py build-catalog
bun run docs:architecture:check
```

Run these commands at the repository root. Mermaid is parsed using the version already locked in
`bun.lock`, with the existing frontend test DOM; install the normal development dependencies first.
Python asset/navigation checks run in the CLI test job; live registry coverage runs in backend tests.
Image content requires human/source review in addition to structural checks.

Regenerate the capability snapshot with the backend Python environment from the repository root:

```bash
python scripts/architecture_diagrams.py export-nodes
```

The exporter reads declarations without executing nodes or contacting providers. Review runtime-source
changes against the pinned baseline before updating a diagram; its historical source SHA remains explicit.

## Earlier assets

Existing SVG and product-illustration URLs remain available in the [parent asset index](../README.md).
Outdated technical embeds now point to this suite. Product-specific references remain separately dated.
