# 08. Chat and agent turns

Status: current implementation.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Source review is static; no provider or device sessions were run.

## Boundary and layout

Left-to-right durable turn path on the upper two thirds, with clearly separate pink durable ledger below admission and cyan live-delivery path below execution. Put the standalone-worker relay as a dashed optional inset joining the backend live hub, never as the persistent store.

## Exact components

- "Chat UI" (client)
- "Authorized chat admission" (service)
- "Chat messages and ChatRun ledger" (state)
- "Workflow event signal" (temporal)
- "Controller and trigger listener" (temporal)
- "MachinaWorkflow" (temporal)
- "AgentWorkflow" (temporal)
- "Model / tool activities" (temporal)
- "Model provider and tools" (external)
- "Conversation persistence" (state)
- "Backend live chat hub" (service)
- "Subscribed UI and resync" (client)
- "Standalone-worker relay" (service)
- "Authenticated /ws/internal" (client)

## Exact directed relationships

- Chat UI → Authorized chat admission [send_chat_message]
- Authorized chat admission → Chat messages and ChatRun ledger [persist admission]
- Authorized chat admission → Workflow event signal [deliver admitted run]
- Workflow event signal → Controller and trigger listener
- Controller and trigger listener → MachinaWorkflow
- MachinaWorkflow → AgentWorkflow [native agent path]
- AgentWorkflow → Model / tool activities
- Model / tool activities → Model provider and tools
- AgentWorkflow → Conversation persistence [save turn]
- Model / tool activities → Backend live chat hub [local run events]
- Chat messages and ChatRun ledger → Backend live chat hub [run lifecycle events]
- Backend live chat hub → Subscribed UI and resync [bounded live stream]
- Model / tool activities → Standalone-worker relay [remote worker only]
- Standalone-worker relay → Authenticated /ws/internal [selected frames]
- Authenticated /ws/internal → Backend live chat hub

## Conditions and caveats

- Durable rows are the record; live hub state is process/epoch scoped.
- Remote relay carries chat events and selected lifecycle frames, not a universal event bus.
- Native AgentWorkflow is shown here; graph dispatch also supports Pi and plugin-specific paths.

## Code evidence

- `server/services/chat/handlers.py` — handle_send_chat_message, authorization/admission and chat subscriptions; lines 260–400, 464.
- `server/services/chat/ledger.py` — admit_message, post_reply, publish_run_event lifecycle and finish_run; lines 258, 340, 406, 565.
- `server/services/chat/events.py` — chat event dispatch to deployed graph.
- `server/services/temporal/agent_workflow.py` — AgentWorkflow turn loop and model/tool activities.
- `server/services/chat/hub.py` — ChatRunHub and publish_run_event; bounded subscriber queues and process epochs.
- `server/services/chat/relay.py` — ChatRelay and handle_chat_run_publish; standalone-worker-only selected event relay.

## Artifacts

- Editable source: [08-chat-agent-turns.mmd](08-chat-agent-turns.mmd).
- Full generation prompt: [08-chat-agent-turns.prompt.txt](08-chat-agent-turns.prompt.txt).
- Image: `08-chat-agent-turns-v1.png` (reviewed built-in ImageGen output).

## Visual review

- Native and documentation-width review: durable message/ChatRun ledger, conversation persistence and live hub are visibly separate.
- Admitted-run dispatch, native agent model/tool path, bounded stream and standalone-worker selected-frame relay match the source brief.
