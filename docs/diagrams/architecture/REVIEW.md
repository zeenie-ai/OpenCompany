# Architecture review and validation

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

Three production workers prepared and reviewed separate runtime, execution and proposal sets.
Workers cross-reviewed another set; the coordinator inspected every selected PNG and its boundaries.
Each source brief records exact components, connections, prerequisites and implementation evidence.
All selected images were produced with built-in ImageGen. Rejected variants and targeted edit prompts
are recorded in the per-view prompt histories and metadata; selected PNGs retain their original bytes.

## Final visual checks

Views were inspected at native 1672 × 941 and normal documentation display width. Status labels,
arrow endpoints, process/storage boundaries, text readability and clipping were reviewed explicitly.

| View | Coordinator review |
|---|---|
| 01 | Verified native/Gateway optional alternatives, external ingress vs local capabilities, no required app Redis. |
| 02 | Corrected pilot verified: submission/service and poll/worker origins, separate storage and sidecars. |
| 03 | Verified final simplified topology and explicit ownership, authenticated relay and mobile authorization insets; aliases identify reused components. |
| 04 | Verified explicit WebSocketContext→output fan-out connection, HTTP crossing bridge, separate REST and cache paths. |
| 05 | Verified final Agent/Pi input arrowheads and separate operations output paths; eligible sequential route and dispatch-error semantics retained. |
| 06 | Verified canvas Coding Agent→Pi exception, graph/tool and Workspace task lanes, only declared eligible saved targets. |
| 07 | Verified registry/class flags→docs-only capability index, separate NodeSpec editor contract, declared activity registration. |
| 08 | Verified admission ledger/persistence separated from live hub and selected remote relay; native turn scope. |
| 09 | Verified durable delegated task lifecycle, connected teammate constraint, lead review, opt-in Context vs explicit Memory and generation reset. |
| 10 | Verified push/poll/schedule ingress, generation snapshot/control, cooperative Stop/drain/Resume and separate Reset/new Start. |
| 11 | Verified allowed/restricted execution and refusal separate from held draft, undo/revision claim, one-attempt send and sent/not_sent/unknown; waiting remains dormant. |
| 12 | Verified app DB vs Temporal history and credential ciphertext/reference split, resolved credential to local caller, workspace/browser/package ownership. |
| 13 | Verified commit-before-acknowledge, separate future stream/relay/outbox and atomic cursor/history feedback; proposed status. |
| 14 | Final corrected routing gap verified: workers→turn connector has visible dark separation from future Outbox. |
| 15 | Verified Draft status, schema/stub/retrieval/codegen chain, sandbox alternatives and external broker, captured trace→metric→GEPA→prompt feedback. |
| 16 | Final corrected current controlled edit buffer vs proposed RHF/zod widget bindings verified; existing backend/adapter/save remain current. |

Peer review corrected current parameter-buffer versus proposed RHF bindings, typed activity origins,
manual Coding Agent/Pi admission, restricted execution versus refusal, current versus future event delivery,
and ownership/relay/mobile endpoints. The final distributed view uses explicit same-component detail
insets to avoid crossing ambiguity.

## Automated checks

The [validation record](validation.json) holds the final documentation, registry-contract and Mermaid results.
The [catalogue](catalog.json) records asset hashes, source fingerprints, review notes and ImageGen attempts.
Text fingerprints normalize Git line endings; PNG fingerprints cover original bytes.

Coverage checks include missing assets, missing hashes, omitted nodes, proposal/current separation,
source-path navigation and the live registry's capability declarations. Structural checks supplement
source and visual review; runtime provider/device sessions are outside this documentation validation.

Existing SVG URLs and the product explainer are retained. Runtime APIs, node schemas, production
dependencies and the main checkout's unrelated sidebar/model-registry edits are preserved.
