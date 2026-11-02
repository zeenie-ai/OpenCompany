# Context (`context`)

| Field | Value |
|------|-------|
| **Category** | memory (config node on `input-context`) |
| **Backend handler** | [`server/nodes/context/__init__.py::AgentContextNode`](../../../server/nodes/context/__init__.py) |
| **Tests** | [`server/tests/nodes/test_agent_context_node.py`](../../../server/tests/nodes/test_agent_context_node.py), [`server/tests/nodes/test_context_handlers.py`](../../../server/tests/nodes/test_context_handlers.py), [`server/tests/services/agent_context/test_conversation.py`](../../../server/tests/services/agent_context/test_conversation.py) |
| **Skill (if any)** | - |
| **Dual-purpose tool** | no |

## Purpose

The opt-in switch and viewing panel for durable agent conversation.
Connecting `output-context` to an agent's `input-context` handle opts the
agent into the plain conversation store: one `agent_conversations` row per
`(workflow_id, generation, agent_node_id)`, loaded at run start and saved
per turn, so every firing (chat messages AND taskTrigger reviews) continues
one conversation. The node carries no runtime state and declares **no
parameters** — the connection is the whole configuration. Normative
reference: [agent_context_flow.md](../../agent_context_flow.md).

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `output-context` → agent `input-context` | context | yes | Opts the agent into conversation persistence |

## Parameters

None (`AgentContextParams` is empty, `extra="forbid"`).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-context` | context (role `context`, top) | The only declared handle; connects to an agent's `input-context`. `hide_input_handle = True`, `hide_output_handle = False` |

Class facts: `component_kind = "model"`, `group = ("memory",)` (so
`isConfigNode` auto-derives), `version = 2`. The single
`@Operation("policy")` ignores its inputs and returns `AgentContextOutput`
(`{configured: true}`, `extra="forbid"`); conversations stay in the store.
`ui_hints`: `isContextPanel`, `systemManaged`, `hideInputSection`,
`hideOutputSection`, `hideRunButton` — all `True`. `systemManaged` only
keeps the node out of the component palette; it does not protect it.

## Behavior

- **Lifecycle**: an ordinary canvas node the user deletes like any other.
  `normalize_workflow_graph` never creates, reconnects or deletes one; the
  single exception is rewriting a legacy `simpleMemory -> input-memory`
  edge, which adds a Context for an agent that has none. Save never restores
  a deleted Context, and `workflow_validator` does not require one (no
  `MISSING_CONTEXT`). It still rejects `INVALID_CONTEXT_EDGE`,
  `MULTIPLE_CONTEXTS` and `SHARED_CONTEXT`. Agent Builder's `add_subagent`
  wires a Context to the teammate it spawns.
- **Descriptor** ([`_descriptor.py`](../../../server/nodes/context/_descriptor.py)):
  registered via `register_agent_context_builder`; emits `kind: "context"` +
  workflow/generation identity. Returns `None` for `generation <= 0`
  (manual canvas Runs persist nothing — only Start admits a generation).
- **Persistence** rides the agent pipeline, not this node:
  `agent.prepare_payload` loads (LOUD failures — `ConversationLoadFailed`
  retryable / `ConversationTooLarge` non-retryable at 1 MB),
  `agent.execute_llm_step` saves per turn best-effort; in-process via
  `_AgentContextRuntime` + the loop's `conversation_saver`; specialized
  providers via `SpecializedAgentContextBridge`.
- **Panel** (`isContextPanel` uiHint →
  [`ContextPanel.tsx`](../../../client/src/components/parameterPanel/ContextPanel.tsx)):
  shows the **live generation only** — role-tinted cards with per-message
  `ts` timestamps, themed JSON trees for tool payloads, a Raw tab with the
  stored wires verbatim, and an agent selector when several agents share
  the node. `refetchOnMount: 'always'`.
- **WS handlers** ([`_handlers.py`](../../../server/nodes/context/_handlers.py)):
  `get_agent_context` (live generation, ownership-authorized),
  `clear_agent_context` (narrowable delete + warm-claude fence +
  `context.updated` broadcast).
- **Workflow Reset**: `reset_execution_state` clears ALL of the workflow's
  stored conversations, terminates warm claude subprocesses, and broadcasts
  `context.updated` — the generation bump alone would leave the panel
  rendering the pre-Reset conversation as live.
- **Events**: one identity-only CloudEvent, `context.updated`
  ([`_events.py`](../../../server/nodes/context/_events.py)), fired from
  the store's save listener (`register_conversation_listener`); the
  frontend invalidates `['agentContext']`.

## Edge cases & known limits

- Deleting the node opts its agent out of persistence. The save's archive
  outbox (`Database.save_workflow` enqueues it for every Context id that
  leaves the graph) then clears ALL of the workflow's stored conversations,
  not just that agent's.
- Deleting an agent leaves its Context node on the canvas, unconnected;
  nothing cascades.
- The palette does not offer the node (`systemManaged`), so a deleted
  Context cannot be re-added from there.
- Two agents on one Context node keep separate conversations (the key is
  per agent node); the panel offers a selector. The validator nevertheless
  rejects that wiring as `SHARED_CONTEXT` at save, Run and Start.
- Cooperative Stop/Resume preserves the admitted generation and its conversation. Reset clears conversation state; a newly admitted generation has a new conversation key. Earlier rows, where retained, are not the live panel conversation.
- Attaching a Context node must never change what the agent sends —
  requests always build from `messages`; the key only says where to save
  (the original `context_ref` regression, locked by
  `tests/temporal/test_agent_workflow.py::TestConversationIdentity`).

## Related

- **Sibling**: `simpleMemory` ("Memory") — explicit durable facts, not
  conversation history.
- **Architecture docs**: [agent_context_flow.md](../../agent_context_flow.md),
  [TEMPORAL_ARCHITECTURE.md](../../TEMPORAL_ARCHITECTURE.md).
