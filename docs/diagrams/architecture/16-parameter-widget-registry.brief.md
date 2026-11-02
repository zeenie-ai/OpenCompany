# 16. Parameter widget-registry proposal

Status: Planned — sketch.

**Status:** sketch. UNIMPLEMENTED SKETCH — DECISION PENDING.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Evidence

- frontend_architecture.md ParameterRenderer/current contract and Renderer registry shape sections explicitly distinguish the shipped switch renderer from the future colocated DIY registry sketch.
- get_node_spec and the backend NodeSpec adapter are already implemented. The client adapter consumes inputs, outputs, parameter schema and uiHints.
- Current ParameterRenderer.tsx dispatches on parameter.type in a large switch (baseline line 1298). The proposed components/inspector layout is absent.
- Current useParameterPanel.ts maintains editBuffer and originalParameters with useState; ParameterPanel passes controlled parameter values and onParameterChange. RHF + zod bindings belong to the future DIY registry sketch, not the current parameter editor.
- The documented sketch uses tester/rank dispatch with RHF + zod, inline scalar widgets, a recursive CollectionWidget and a CodeWidget. It preserves existing production interaction requirements.

## Source files

- [docs-internal/frontend_architecture.md](../../../docs-internal/frontend_architecture.md)
- [client/src/components/ParameterRenderer.tsx](../../../client/src/components/ParameterRenderer.tsx)
- [client/src/lib/nodeSpec.ts](../../../client/src/lib/nodeSpec.ts)
- [client/src/adapters/nodeSpecToDescription.ts](../../../client/src/adapters/nodeSpecToDescription.ts)
- [client/src/hooks/useParameterPanel.ts](../../../client/src/hooks/useParameterPanel.ts)
- [client/src/ParameterPanel.tsx](../../../client/src/ParameterPanel.tsx)

## Exact components

- **backend**: Backend NodeSpec / existing contract — current.
- **adapter**: NodeSpec adapter / existing — current.
- **switch**: Current switch renderer — current.
- **registry**: Proposed widget registry / tester + rank dispatch — proposed.
- **scalar**: Inline scalar widgets — proposed.
- **collection**: CollectionWidget / recursive collections — proposed.
- **code**: CodeWidget / editor wrapper — proposed.
- **buffer**: Local parameter edit buffer / existing — current.
- **form**: Proposed RHF + zod bindings — proposed.
- **save**: Existing save + node configuration — current.

## Directed relationships

- **backend → adapter**: schema + uiHints.
- **adapter → switch**: current path.
- **switch → buffer**: controlled values.
- **buffer → save**: save.
- **adapter → registry**: proposed dispatch.
- **registry → scalar**: match.
- **registry → collection**: match.
- **registry → code**: match.
- **scalar → form**: field binding.
- **collection → form**: field binding.
- **code → form**: field binding.
- **form → save**: save.

## Boundaries and caveats

- The registry is subject to a separate implementation decision. This image records the documented DIY preference without implementing the refactor.
- The current NodeSpec wire contract, backend precedence, adapter and controlled edit buffer are existing components, not future work. Proposed RHF + zod bindings must preserve the existing node configuration/save boundary.
- Credentials, uploads, drag/drop, expression behavior and save semantics are preserved interaction boundaries rather than newly invented widget APIs.
- JSON Forms, an alternate NodeSpec wire shape and VITE_USE_NODESPEC were superseded ideas. They are intentionally excluded.

## Visual review

Passed at native 1672 × 941 and documentation scale. Verified 10 unique components and 12 relationships. Current parameter editing uses the existing controlled useState buffer; RHF + zod bindings are explicitly proposed. Repeated save boxes are aliases of the same existing save/configuration boundary. Runtime-worker source cross-review found and corrected the initial form-state assumption. Two built-in ImageGen edits corrected colors/aliases and current-versus-proposed binding status; original generated artifacts are retained.
