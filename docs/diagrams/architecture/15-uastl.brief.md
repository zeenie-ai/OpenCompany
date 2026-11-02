# 15. UASTL connector and optimization architecture

Status: Planned — draft.

**Status:** draft. DRAFT RESEARCH PROPOSAL.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Evidence

- RFC-0001 status is Draft. §§3–5 define heterogeneous specification parsing, ConnectorCIR, typed connector stubs, retrieval and DSPy/code-generation modules.
- RFC-0001 §6 defines alternative sandbox runtime implementations (E2B/Firecracker, Modal, Pyodide/WASM) and a host-side credential broker. The sandbox calls the broker; credentials are injected outside the sandbox at proxy time.
- RFC-0001 §7 defines trace capture, decomposed metrics, GEPA reflection and a selected-prompt feedback loop. §§10.2 and 13 retain connector-version re-evaluation and open design questions.
- Targeted searches in current services and nodes found no UASTL/ConnectorCIR/UniversalConnector/DSPy/GEPA implementation. Existing credentials and LLM modules implement current runtime capabilities, not this proposed composition.

## Source files

- [RFC-0001-UASTL-Universal-API-Schema-Translation-Layer.md](../../../RFC-0001-UASTL-Universal-API-Schema-Translation-Layer.md)
- [server/services/credentials/sources.py](../../../server/services/credentials/sources.py)
- [server/services/llm/__init__.py](../../../server/services/llm/__init__.py)

## Exact components

- **specs**: API specifications / OpenAPI, AsyncAPI, GraphQL, YAML — external.
- **compiler**: Schema compiler / ConnectorCIR — proposed.
- **stubs**: Typed connector stubs — proposed.
- **index**: Retrieval index — state.
- **task**: Natural-language task — interface.
- **codegen**: DSPy code generation — proposed.
- **sandbox**: Sandbox alternatives / E2B, Modal or Pyodide — proposed.
- **broker**: Host credential broker / proposed proxy — proposed.
- **apis**: External APIs + browser targets — external.
- **traces**: Execution traces / code, HTTP, screenshots, output — state.
- **metrics**: Decomposed metrics + critique — proposed.
- **gepa**: GEPA reflection + evaluation — proposed.
- **prompts**: Selected prompt versions — state.

## Directed relationships

- **specs → compiler**: parse + normalize.
- **compiler → stubs**: generate.
- **stubs → index**: index.
- **index → codegen**: retrieve relevant stubs.
- **task → codegen**: task.
- **codegen → sandbox**: execute program.
- **sandbox → broker**: proxied API request.
- **broker → apis**: inject auth + forward.
- **sandbox → apis**: browser interaction.
- **sandbox → traces**: capture.
- **traces → metrics**: score.
- **metrics → gepa**: training feedback.
- **gepa → prompts**: select.
- **prompts → codegen**: improved prompt.

## Boundaries and caveats

- All UASTL-specific boxes are draft proposal components. This view does not claim they are deployed or replace the current node/plugin system.
- Sandbox choices remain alternatives, not a selected technology. The host broker is a draft design, not an approved migration away from current local/1Password credential sources.
- The proposed credential broker proxies requests to external APIs; tokens do not enter the sandbox or model context in the draft design.
- The GEPA feedback loop is proposed. The drawing makes no live benchmark, safety, optimization-quality or provider-coverage guarantee.
- Stub granularity, mock-versus-live evaluation, hybrid browser/API behavior and multi-user isolation remain open draft questions.

## Visual review

Passed at native 1672 × 941 and documentation scale. Verified all 13 components and 14 directed relationships, host-side API proxy boundary, sandbox alternatives, trace/metric/GEPA feedback, draft status, proposed datastore outlines, and readable native/documentation scale. One ImageGen edit corrected retrieval-index status and input/state badges. Built-in ImageGen generated the PNG using the accepted local-runtime style reference; the selected PNG is copied into this canonical directory and original generated artifacts are retained.
