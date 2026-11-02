# Documentation and supported-node audit

**Baseline:** committed `56d4154e713845d93124875b271144acac8d26cd`, reviewed on 2026-11-02 in an isolated worktree. Earlier investigation used older commits; findings here were rechecked against this baseline. Uncommitted changes in the main checkout were excluded.

This audit corrects documentation and shipped skill defaults and adds offline documentation checks. It does not change product APIs, node schemas, runtime flags or execution behavior. Stored user/project skill overrides remain authoritative.

## Coverage and method

The baseline contains 413 tracked Markdown files. Every document is classified in the [corpus inventory](audits/MARKDOWN_COVERAGE.md); implementation evidence and review boundaries are recorded in these ledgers:

- [Reference docs](audits/REFERENCE_DOCS.md): runtime, UI/desktop, security, workspace and design/reference material.
- [Operations docs](audits/OPERATIONS_DOCS.md): setup, CLI, deployment, integrations and entrypoints.
- [Core nodes](audits/CORE_NODES.md): core node types, every declared operation, selector branches, shared execution paths and core skills.
- [Integration nodes](audits/INTEGRATION_NODES.md): 69 integration types and their complete declared operation/selector inventory, plus integration and role skills.

The runtime registry reconciles 157 node types at this baseline. Counts are observations, not fixed test targets. The registry gate also checks metadata/spec/handler relationships and discovery failures. `twitterReceive` is registered but explicitly unavailable; retired `browserHarness` is a migration card, not another working implementation.

Direct canvas Run, graph execution, agent-tool calls, controlled Temporal runs and specialized Workspace tasks are assessed separately. Independence from employee teams does not remove graph, credential, runtime, ownership or connection prerequisites. A hidden Run button or agent-shaped renderer does not establish backend capability.

Shared contracts are reviewed once and each node's overrides are recorded. Dynamic MCP, SDK, CLI and named-provider dispatch are audited as adapter contracts and built-in presets; arbitrary remote servers and future upstream operations are not claimed verified.

The two `SKILL.v1.md` files are active, opt-in Browser/Process defaults selected by `services/skill_defaults.py`. They preserve legacy defaults and user overrides; they are not historical snapshots.

## Corrections

| Area | Result |
|---|---|
| Employee lifecycle | Safe Apply preserves conversations, accepted work and paused state; Reset and explicit interruption are documented separately. |
| Runtime and models | Bounded delegation, actual snake_case fields, Pi dispatch, fallback prerequisites, activity-specific retry/timeout behavior and disabled rollout flags are explicit. |
| Security | REST placeholder ownership and JWT deactivation limitations are described accurately; per-call 1Password capability is separated from gateway integration limitations. |
| Shipped skills | Removed Memory tools/transcript assumptions, unsupported Python imports/state/exception names, process input, scraper/search/proxy/HTTP arguments, Apify inputs, Twitter reply IDs and Maps callable/schema examples were corrected. |
| Integration behavior | WhatsApp URL media sources, quote/vCard fields and flat send results; trigger waiting/polling versus deployment; current email poll setup; Maps implementation limitations are explicit. |
| UI and desktop | Mixed REST/WebSocket transport and polling, remote close-code reconnect behavior, client commands, AppShell ownership, NodeSpec flags and live theme authority are corrected. |
| Setup and deployment | GCP enables required Temporal; native LLM credentials use supported sources; Docker login/cache/ownership guidance and current CI/Pi build descriptions are reconciled. |
| Status and navigation | Three missing node cards were added, the node index regenerated, active links repaired, and current/planned/dormant/prototype/history boundaries clarified. Retired Docker instructions are preserved verbatim in the archive. |

## Separate code findings

These are implementation observations, not fixes supplied by this documentation change. Source inspection is distinguished from runtime proof in the linked ledgers.

| Finding | Current evidence / follow-up |
|---|---|
| Account deactivation does not reliably revoke unexpired API/WS access | Main admission boundaries verify JWT claims; active-account lookup is stronger on auth/status routes. Add boundary-level HTTP/WS and already-connected-socket tests before changing revocation behavior. |
| New REST workflow ownership uses placeholder owner | Existing owners are preserved; new rows can disagree with numeric authenticated principals. Identity propagation needs a separate product change. |
| Nearby Places schema/service mismatch | Params declares latitude/longitude/place_type, while the helper reads lat/lng/type and can use fallback coordinates/type; keyword also follows an incompatible options path. Documentation warns against presenting those results as the requested location. |
| Maps saved-key propagation | Params drops the executor-injected api_key before the Maps service reads it; the runtime Google Maps setting can mask a saved-credential-only failure. This affects the three Maps nodes and requires a separate credential-boundary fix. |
| Standalone chat-model settings | Several declared tuning fields are not forwarded by AIService.execute_chat; thinking_enabled with a nullable thinking_budget can raise int(None). Provider capability does not establish that this standalone path forwards every schema option. |
| Downloader/chunker service fields differ from Params | Some legacy camelCase lookups ignore configured snake_case values. Existing cards now make those operational limitations explicit; changing behavior needs targeted regression tests. |
| Downloader path and response-size boundary | The service decodes a URL basename before joining it to output_dir without containment checking, and does not cap response bytes. Treat this as a separate security/reliability code finding; the docs do not claim safe bounded downloading. |
| RLM limits are service-only fields | The service reads limit/depth/budget/timeout options that the specialized node's Params discards. The documentation no longer promises that those node settings are configurable. |

Earlier Reset/job cleanup findings from the initial investigation are resolved in this committed baseline; they are not repeated as open defects.

## Drift prevention

Existing CLI/backend test jobs now cover real registry/card completeness, index freshness, tracked Markdown links/images/anchors, implementation source paths, skill metadata and selected executable examples. Unknown example fields are checked before permissive schema validation where the contract forbids them; documented extension fields remain supported.

Checks make no HTTP requests. They preserve historical bodies, validate incoming/current-successor navigation, and use narrow, explained exceptions for proposed paths, templates and optional sibling checkouts. A structural pass does not prove every semantic claim.

## Validation

| Check | Result |
|---|---|
| Core node suites | 540 passed, 2 skipped across the isolated offline core suites, including job-source, Mobile and cron coverage. |
| Integration node suites | 673 passed across 15 offline integration suites: 667 in the isolated full run plus 6 import probes rerun with DEBUG=false after an inherited host value of release caused Settings parsing failures. The first sandbox run was inconclusive after Windows temporary-directory access failures. |
| Documentation/parser/index checks | 884 passed: Markdown navigation/source paths, parser fixtures, package-aware commands and index freshness. |
| Plugin/skill contracts | 162 passed: plugin/NodeSpec/skill contracts, active v1 defaults and user-override preservation. |
| Embedded documentation | 13 passed: temporary-manifest packaging, Agent Builder docs and restricted Python examples (including proxy formatting). |
| Historical Docker extraction | Body preserved verbatim; baseline/extracted body hashes match. |

No paid-provider calls, real OAuth/vault access, browser/mobile sessions, cloud provisioning, visual fidelity benchmarks or live deployment acceptance were performed. External vendor guarantees, arbitrary SDK/MCP operations and real hardware behavior remain explicitly unverified. The audit is a code-backed static review supplemented by offline tests.
