# Chat protocol (v1)

The wire contract between the server's chat runtime (`server/services/chat/`) and the shared chat UI
(`client/src/features/chat/`). Home's employee page and Dev's console Chat pane both speak it. It follows AG-UI's
event model and ordering ([events](https://docs.ag-ui.com/concepts/events),
[interrupts](https://docs.ag-ui.com/concepts/interrupts.md),
[activity events](https://docs.ag-ui.com/spec/1.0/events/activity.md)), carried on the existing WebSocket with
snake_case fields in the repo's CloudEvents envelope (`services/events/envelope.py`), never as literal camelCase
AG-UI JSON.

Status: being built in phases on `main`; the design and the owner's decisions are in the plan recorded at
`~/.claude/plans/analyze-the-new-generative-zazzy-cosmos.md`. Change this document in the same commit as any change
to the shapes below, and bump `protocol_version` (returned by `get_chat_messages`) when an existing field changes
meaning.

Built so far: runs and their storage, the lifecycle events (`started`, `finished`, `failed`), subscriptions,
`get_chat_messages` v2 (with each message's `run`), `get_chat_run`, the watchdog and the MachinaWorkflow patch (see
[Runs](#runs)); the answer streaming as text events, working steps and Stop (see [Streaming, steps and
Stop](#streaming-steps-and-stop)); generated UI in replies with its state and button presses (see [Generated
UI](#generated-ui)); and the shared chat UI on both hosts (see [Client](#client)). Every other event, handler and
part below is the contract the later phases build to.

## Concepts

| Term | Meaning |
|---|---|
| Session | One conversation. Its id is the workflow id (`"default"` with no workflow open). |
| Run | One answer the employee works on: an owner message, an edit, a regenerate, or a button press in generated UI (kinds `message`, `edit`, `regenerate`, `action`). Approved sends execute as runs of kind `resume`. |
| Lane | At most one non-terminal run per session, `resume` runs excepted. A second send is refused with `run_in_progress`; the composer shows Stop instead of Send while a run is live. |
| Message tree | Messages link to their parent. Owner messages that share a parent are branches; replies under the same owner message are versions. The session's active leaf picks the path shown. |
| Part | Structured content attached to a reply: steps, generated UI, artifacts, approvals, sources, follow-ups. |
| Generation | The deployment generation a message was written in (`run_key`). A Reset clears the thread. |

## Delivery

Run events never ride `StatusBroadcaster.broadcast` (which reaches every socket). A socket receives a session's run
events only after `chat_subscribe` succeeds. That handler applies the same checks as the canvas handlers: refuse the
internal socket, load the workflow, and compare its owner with the socket's execution principal.

```json
{"type": "chat_run_event",
 "data": {"specversion": "1.0", "id": "r_7f3c…:12", "source": "opencompany://services/chat",
          "type": "com.opencompany.chat.run.text.content", "subject": "r_7f3c…",
          "time": "2026-10-03T21:40:12.512Z", "datacontenttype": "application/json",
          "data": {"workflow_id": "…", "session_id": "…", "run_id": "r_7f3c…", "seq": 12,
                   "hub_epoch": "e_91ab…", "message_id": "r_7f3c….0.1", "delta": "Saturday is "}}}
```

- Scope fields (`workflow_id`, `session_id`, `run_id`) live inside `data`, never as top-level CloudEvents extension
  attributes.
- `seq` increases by one per run. The client drops duplicates `(run_id, seq)` and asks for a snapshot on a gap.
- `hub_epoch` changes when the server process restarts. A changed epoch means the client must resync.
- `chat_subscribe` answers with the session's live runs as snapshots, each with the `seq` it reflects. The hub is
  read before the database, so an event published meanwhile carries a higher `seq` than its snapshot: drop events
  with `seq` at or below the snapshot's, and buffer events that arrive before the response.
- Each subscriber has a bounded queue (`hub.subscriber_queue_size` in `server/config/chat_defaults.json`). A socket
  that falls that far behind has its queue replaced by one `custom` `opencompany.resync` per session it follows
  (`data: {session_id, hub_epoch, name, value}`, no `run_id`); the client then takes fresh snapshots. Text deltas
  are batched before they are published (see [Streaming, steps and Stop](#streaming-steps-and-stop)), not merged in
  the queue.
- Publishers call `publish_run_event` (`services/chat/hub.py`), which delivers in-process. A standalone worker
  (`python -m services.temporal.worker`) has no sockets, so it relays instead; see
  [Standalone workers](#standalone-workers).
- `chat.updated` remains an identity-only broadcast (`{workflow_id, session_id, role}`) that tells every open thread
  to refetch `get_chat_messages`.

## Events

Every event's `data` carries `{workflow_id, session_id, run_id, seq, hub_epoch}` plus the fields below. The CloudEvents
`type` is `com.opencompany.chat.run.<suffix>`; `subject` is the run id; `id` is `<run_id>:<seq>`.

| AG-UI event | Suffix | Fields |
|---|---|---|
| RUN_STARTED | `started` | `kind`, `parent_run_id?`, `user_message_id?`, `reply_message_id`, `started_at` |
| RUN_FINISHED | `finished` | `outcome` (below), `result {reply_message_id?, no_reply?}`, `duration_ms`, `step_count` |
| RUN_ERROR | `failed` | `message` (safe to show, at most 500 chars), `code`, `hint?`, `requires_user_action?` |
| STEP_STARTED | `step.started` | `step_id` (the tool call's id), `step_name`, `icon?` |
| STEP_FINISHED | `step.finished` | `step_id`, `step_name`, `state` (`done`, `failed`, `skipped`), `detail?` (at most 200 chars), `duration_ms`, `narration?` |
| TEXT_MESSAGE_START | `text.started` | `message_id` (segment id `{run_id}.{iteration}.{attempt}`), `role: "assistant"` |
| TEXT_MESSAGE_CONTENT | `text.content` | `message_id`, `delta` (never empty) |
| TEXT_MESSAGE_END | `text.ended` | `message_id`, `final` (bool), `reply_message_id?` (with `final: true`) |
| TOOL_CALL_START | `tool_call.started` | `tool_call_id`, `tool_call_name`, `parent_message_id?`, `label` (sending tools only) |
| TOOL_CALL_ARGS | `tool_call.args` | `tool_call_id`, `delta` |
| TOOL_CALL_END | `tool_call.ended` | `tool_call_id` |
| TOOL_CALL_RESULT | `tool_call.result` | `message_id`, `tool_call_id`, `content` (a JSON string), `role: "tool"` |
| ACTIVITY_SNAPSHOT | `activity.snapshot` | `message_id` (the part id), `activity_type`, `content` (an object), `replace` |
| ACTIVITY_DELTA | `activity.delta` | `message_id`, `activity_type`, `patch` (RFC 6902 operations on `content`) |
| REASONING_START / END | `reasoning.started` / `reasoning.ended` | `message_id`; lifecycle only, no reasoning text |
| CUSTOM | `custom` | `name`, `value` |

`activity_type` values: `json_render`, `approval`, `sources`, `followups`, `artifact`.

`custom` names: `opencompany.segment_discarded` (`{message_id}`: a retried LLM attempt replaces this segment),
`opencompany.retrying` (`{retry_after?, attempt}`), `opencompany.stopping`, `opencompany.resync`.

**Text segments.** Only the agent that answers the owner streams text. A segment that ends with `final: false` was
written beside tool calls (narration, "Let me check the calendar."); the segment with `final: true` is the reply.
Text that could still turn out to be `NO_REPLY` or a `<followups>` block is held back on the server and never
streamed.

**Outcomes** (`run.finished`):

| `outcome.type` | Meaning |
|---|---|
| `success` | The run ended. `result.reply_message_id` names the saved reply, or `result.no_reply` is true. |
| `interrupt` | Drafts wait for the owner. `outcome.interrupts: [{id, reason: "tool_call", message, tool_call_id, response_schema, expires_at}]`; `id` is the approval id. |
| `stopped` | The owner pressed Stop. OpenCompany extension; AG-UI defines only `success` and `interrupt`. |

Runs nothing will finish end with `run.failed`: code `not_delivered` (never picked up, or the employee stopped
first), `timed_out` (running longer than `runs.max_running_s`), `interrupted` (its workflow closed without finishing
it and no reply was saved; with a saved reply it finishes instead). A Reset ends live runs with code `reset`, the
owner's Clear with `cleared`, and both delete them.

## Handlers

All are WebSocket request/response handlers with snake_case payloads. Failures answer
`{success: false, error: "<code>", …}`.

### Chat (`server/services/chat/handlers.py`)

| Handler | Request | Response |
|---|---|---|
| `send_chat_message` | `{session_id, message, role: "user", timestamp?, client_message_id?, attachments?: [{path}], options?: {web?: bool}, ui_event?: {part_id, element_id, action, params}}` | `{success, message_id, run_id, delivery: "now" \| "queued", timestamp}`; `run_id` is null when the message starts no run; `attachment_rejected` for files that cannot go (see [Attachments](#attachments)) |
| `get_chat_messages` | `{session_id, limit?, all_generations?}` | `{success, protocol_version, session_id, messages, thread: {active_leaf_id, revision}, active_runs: [RunSnapshot]}` |
| `chat_subscribe` | `{session_id}` | `{success, session_id, hub_epoch, active_runs: [RunSnapshot]}` |
| `chat_unsubscribe` | `{session_id}` | `{success, session_id, hub_epoch}` |
| `get_chat_run` | `{run_id}` | `{success, run: RunSnapshot}`, or `not_found` |
| `stop_chat_run` | `{run_id}` | `{success, run_id, state: "stopping" \| "stopped"}`; `not_stoppable` (with `state`) for a run that ended otherwise |
| `chat_ui_state` | `{session_id, part_id, changes: [{path, value}]}` (at most 32; the last value per path wins) | `{success, part_id, state_revision}`; `not_found`, `invalid_request` |
| `edit_chat_message` | `{session_id, message_id, message, expected_revision?, client_message_id?}` | `{success, message_id, run_id, delivery}` (the edit's id; a resent `client_message_id` answers the first edit) |
| `regenerate_chat_reply` | `{session_id, message_id, expected_revision?}` (the latest answer, or the owner's last message when its run gave none) | `{success, message_id, run_id, delivery}` (`message_id`: the owner's message answered again) |
| `switch_chat_branch` | `{session_id, message_id, expected_revision?}` (a message beside one on the path) | `{success, leaf_id}` |
| `set_chat_feedback` | `{session_id, message_id, value: "up" \| "down" \| null}` | `{success, message_id, value, reaches: ["next_turn", "memory"?]}` (`[]` when taken back) |
| `get_chat_context` | `{session_id}` | `{success, session_id, commands: [{command, description, fill, suggest}], capabilities: {attachments, web}, limits: {max_attachments, max_upload_bytes}}` |
| `clear_chat_messages` | `{session_id}` | `{success}`; also clears runs, parts, snapshots, notes and feedback, and makes the employee forget the conversation |

`send_chat_message` with the same `client_message_id` (1 to 100 letters, digits or `_.:-`) returns the message
and run the first call created, and dispatches nothing again.

**RunSnapshot** is the state the client reducer would hold after replaying the run's events
(`server/services/chat/reducer.py` folds them the same way):
`{run_id, session_id, workflow_id, kind, state, seq, hub_epoch, user_message_id, reply_message_id, parent_run_id,
created_at, started_at, finished_at, steps, segments: [{message_id, text, final}], activities: [{message_id,
activity_type, content, patches}], interrupts, outcome, result, error, error_code}`. `state` is `queued`, `pending`,
`running`, `stopping`, `finished`, `error` or `stopped`. A snapshot read after a server restart has its steps but no
text segments or activities (those are stored with the reply).

### Approvals (`server/services/approvals/handlers.py`)

| Handler | Request | Response |
|---|---|---|
| `list_approvals` | `{workflow_id?, status?: <row status> \| "open" \| "recent", run_id?, kind?: "gate" \| "tool_call", limit? (at most 100)}`; `status` defaults to `pending` | `{success, approvals, counts: {workflow_id: pending}, server_time}` |
| `get_approvals` | `{approval_ids}` (at most 100) | `{success, approvals, server_time}` |
| `decide_approval` | `{approval_id, decision: "send" \| "undo" \| "discard" \| "restore" \| "retry", decision_key, text?, subject?, confirm?}` | `{success, approval, idempotent?, will_send_on_resume}` |
| `get_ask_first` | `{workflow_id}` | `{success, workflow_id, ask_first: bool \| null, revision}` (null: the workflow has no rule) |
| `set_ask_first` | `{workflow_id, ask_first, expected_revision?}` | `{success, workflow_id, ask_first, revision, replies_gated, needs_apply}`; `rule_conflict` (with the current value) when it moved |

`open` lists what the owner may still act on, or is on its way: pending, approved and not handed on, sending,
failed, and discarded while it can be restored. `recent` lists everything, newest first. Only the owner's own drafts
and workflows answer; any other id reads as `not_found`. `decide_approval` errors: `already_decided`, `expired`,
`cancelled`, `too_late` (Undo after it went, Restore after its window), `confirm_required` (a retry of a send that
broke off), `approval_conflict`, `send_unavailable` (nothing could start the send; the draft is back as it was),
`not_found`, `invalid_request`. The same `decision_key` again answers `idempotent` with the row as it is.

**ApprovalSummary**: `{approval_id, workflow_id, node_id, kind: "gate" | "tool_call", status, channel,
channel_label, action?, recipient, recipient_label, body, subject?, context_excerpt?, details: [{label, value}],
created_at, expires_at, revision, max_length, editable, approved_by?: "owner" | "auto", edited?, undo_until?,
restore_until?, consumed_at?, outcome?: {certainty: "sent" | "not_sent" | "unknown", error?, at?}, run_id?,
tool_call_id?, ui_part_id?, agent_node_id?, deployment_state?}`. `status` is `pending`, `approved`, `sending`,
`sent`, `failed`, `discarded`, `expired` or `cancelled`; see [Approvals](#approvals).

### Plugins

| Handler | Owner | Request | Response |
|---|---|---|---|
| `canvas_versions` | `nodes/tool/canvas` | `{workflow_id, node_id, item_id}` | `{success, versions: [{version, title, created_at, source, size_bytes}], latest}` |
| `canvas_version` | `nodes/tool/canvas` | `{workflow_id, node_id, item_id, version}` | `{success, item: CanvasItem-at-that-version & {latest}}` |
| `dictation_status` | `nodes/speech` | `{session_id}` | `{success, available, provider}` |
| `transcribe_audio` | `nodes/speech` | `{session_id, path, language?}` (`path` under `uploads/`) | `{success, text, language, provider}`; `speech_unavailable` |

### Worker relay (`server/services/chat/relay.py`)

Internal: the backend accepts it on `/ws/internal` only (the worker token, `services/authz/ws_surface.py`
`INTERNAL_SOCKET_HANDLERS`), and a client socket gets `access_denied`. The worker sends it without a `request_id`.

| Handler | Request | Response |
|---|---|---|
| `chat_run_publish` | `{items}` (at most 500): `{kind: "run_event", run_id, session_id, workflow_id, suffix, fields, event_key}`, `{kind: "broadcast", type: "chat.updated" \| "approval_lifecycle", data: <CloudEvent>, session_id?}`, `{kind: "resync", session_ids}` | `{success, published, refused}`; `invalid_items` |

## Standalone workers

The hub delivers to the sockets of the process it runs in. The backend's embedded workers share that process, but
a standalone worker (`run_standalone_worker`, for horizontal scaling) does not. It starts a relay at boot
(`start_relay`), and from then on `publish_run_event`, `announce_chat_updated` (`services/chat_thread.py`) and
`broadcast_approval_change` (`services/approvals/events.py`) queue what they would have published. One writer sends
the queue in order over the worker's own `/ws/internal` connection as `chat_run_publish` frames, at most
`hub.relay_batch_size` items to a frame (`server/config/chat_defaults.json`), reconnecting with backoff when the
backend restarts. The backend publishes each run event into its own hub, so `seq`, `hub_epoch` and the `event_key`
dedupe are the backend's, exactly as for its own events. It sends each broadcast to its sockets only when the
CloudEvent type matches the wire key.

Nothing waits on the relay, and nothing is resent. Past `hub.relay_queue_size` queued items an event is dropped, and
so is a frame the connection lost. The next frame that gets through ends with a `resync` item for the sessions
those belonged to, and the backend sends their subscribers `opencompany.resync`. Their clients take fresh
snapshots, in which a stored terminal state wins. The rows are the record: text deltas a resync cannot restore are in
the saved reply. Other broadcasts from a standalone worker's activities (node status, agent progress, Canvas and
Memory updates) still reach only that process.

## Messages

`get_chat_messages` returns the active path, oldest first:

```
{id, legacy_id, role, kind, text, message, timestamp, run_key, run_id, parent_id, status, attachments,
 parts, feedback, siblings: {index, count, ids}, editable, client_message_id?,
 run?: {run_id, state, outcome, error?: {message, code, hint?}, steps?: [{step_id, name, state, detail?,
        duration_ms?}], duration_ms?}}
```

- `id` is the message's stable id (`m_…` for the owner's, `a_<run id>` for a run's reply; rows saved before chat
  runs are `m<number>`). `legacy_id` is the database row number.
- `message` equals `text`; it is kept for older readers.
- `client_message_id` is echoed on the owner's messages that carried one, so an optimistic row and its saved row
  keep one key.
- `run` says how the run a message started (or answers) stood when the thread was read: its `state`, `outcome`, for
  a failed run the error with the hint the run recorded, the steps it saved, and once it has ended how long it
  worked (`duration_ms`). It is how a reload still shows that a message went unanswered and what the employee did;
  while the session is subscribed, the run's events are fresher (see [Client](#client)).
- `siblings` are the messages beside it (the same parent and role), oldest first, itself included: the owner's
  edits of a message, or the answers tried for one. `index` is its own place.
- `editable` says the owner may change it now (the server decides): their text message a run answered, in the live
  generation (Edit), and the answer the path ends at once its run has ended (Try again).
- `run_id` on the owner's message names the run answering it on this path: the run of the answer after it, else its
  newest run (an answer being tried again), else the run it started.
- `feedback` is the owner's rating of an answer: `up`, `down` or null.
- `kind`: `text`, `report` (Post to Talk: not editable), `action` (a button press), `notice`.
- `status`: `complete`, `stopped`, `error`.

Parts render in this order, whatever order they were produced in: steps, text, generated UI, artifacts, approvals,
sources, follow-ups.

```
parts: {
  steps?:     {duration_ms, items: [{step_id, name, state, detail?, duration_ms?}]},
  ui?:        [{part_id, spec, state, state_revision, elements}],
  artifacts?: [{workflow_id, canvas_node_id, item_id, version, title, format}],
  approvals?: [{approval_id, tool_call_id?}],
  sources?:   [{n, title, url, detail?}],
  followups?: [string],
  stopped?:   true
}
```

Approvals are joined live from the approvals store; the part only names them (`{approval_id, tool_call_id}`).

Artifacts name the documents (Canvas notes) the run wrote or revised, one entry per item at the latest version
the run left it: the canvas tool calls `show_artifact` (`artifact:<item_id>`, so a later version replaces the
earlier one) and publishes an `activity.snapshot` with `activity_type: "artifact"` and `message_id:
"artifact_<item_id>"`. The card (`features/chat/turns/ArtifactCard.tsx`) shows the title, Document or Code, the
version, and Open, which asks the host to show the item at that version (Home: the Workspace's Canvas tab; the
editor: the Canvas dock; see [canvas_node.md](./canvas_node.md)). The client shows each saved document at the newer
of its saved and live versions.

A run's tools record parts on the run as they go (`chat_run_parts`, keyed so a retried activity writes the same
row; `services/chat/parts.py`). Reply in Chat saves its reply with the parts recorded so far, and the run's end seals
any later ones into it, creating an empty-text reply when the run showed an interface, made a draft or wrote a
document but wrote no text (sources alone make none: they show only where a reply cites them); the end is published
after the seal, so its `result.reply_message_id` names a reply that holds them. A sealed item the reply already holds
stays as it is there (the owner may have changed an interface's state since), except a document's, where the run's
newer version wins in place.

## Generated UI

- **Source.** The employee calls the `show_ui` tool (`chatUi` plugin, `nodes/chat/chat_ui`) with a json-render flat
  spec `{root, state, elements}`. `services/genui/spec.py` checks it against `server/config/chat_genui_catalog.json`
  (JSON Schema per component, which prop each input binds, what each layout may hold, the limits): a spec that
  breaks a rule fails the call with every reason, so the model writes it again; unknown types, unreachable elements,
  stray fields and props are left out and reported back. The tool's description is generated from the same manifest,
  byte for byte the same for the same file, and a test on each side reads the other's catalogue. The checked spec is
  saved as a `ui` part and streamed as `activity.snapshot` (empty spec) followed by one `activity.delta` per patch:
  `add /root`, `add /state`, then `add /elements/<id>` in depth-first order (`services/genui/patches.py`, matching
  the handoff's `*.patches.jsonl` line for line). Only the agent answering the owner's chat message can show UI; a
  call anywhere else says nothing was shown.
- **Owner edits.** `$bindState` writes go to the interface's own state store and to `chat_ui_state`; the last write
  per path wins, and the client sends after 300 ms idle, when the page hides, when the chat closes and before a
  button press goes (`features/chat/data/uiState.ts`). The server keeps them on the part (`state`,
  `state_revision`), on the reply once it is saved, so a reload shows them.
- **Buttons.** A press resolves `$state` params at click time; the client tags each press with its element and label
  (json-render hands a handler the params only). `ask` sends its text as the owner's message only when it is what
  the button says (case and spacing aside); otherwise the text goes into the composer for review. Any other action
  becomes `send_chat_message{ui_event: {part_id, element_id, action, params}}`: the server checks it against the
  saved spec (a Button whose press runs that action, params it declares), saves the owner's message as the button's
  label (`kind: "action"`, the press in `meta.ui_event`), starts a run of kind `action`, and sends the employee the
  line `[ui-event]{"ui_id", "element", "label", "action", "params"}[/ui-event]`. A press while the employee is still
  answering is held back with a notice. `ui_event_rejected` says why one does not fit.
- **Sanitising.** json-render 0.21 does not guard state paths (`setByPath` descends into `__proto__`), does not
  validate props against the catalog, and supports `watch` (actions fired by state changes), `repeat`, `$computed`
  and an action binding's `confirm`, `onSuccess` and `onError` (the last two set state or run further actions).
  Both sides therefore (the client in `client/src/lib/jsonRender/`):
  - refuse `__proto__`, `constructor` and `prototype` path segments everywhere (`$state`, `$bindState`, `$template`,
    `visible`, action params, patch paths); an expression that reads such a path is dropped, and a `visible`
    condition that reads one is false;
  - keep only an element's `type`, `props`, `children`, `visible` and `on`, and only an action binding's `action`
    and `params`: `watch`, `repeat`, `slots`, `confirm`, `onSuccess`, `onError` and `preventDefault` go;
  - drop `$computed`, and `$item`, `$index` and `$bindItem`, which mean nothing without `repeat`;
  - validate every element's props (the client degrades one bad prop at a time with forgiving zod schemas);
  - drop unknown component types before rendering.

  json-render renders each element inside its own error boundary, so one failing element renders nothing instead of
  breaking the reply.

## Runs

`server/services/chat/` (never imports `nodes/`):

- **Admission** (`ledger.admit_message`). `send_chat_message` writes the owner's message and its run in one write
  transaction reserved before its first read (`Database.reserved_session`, SQLite `BEGIN IMMEDIATE`), after checking
  the lane; a partial unique index on `chat_runs` enforces it too. A message starts a run only when a chat trigger in
  the deployed graph (the control generation's `graph_snapshot`) accepts its session, judged by that trigger's own
  filter (`event_waiter.build_filter`). Otherwise it is saved and dispatched without a run, like the editor's
  `"default"` session, which keeps its unscoped delivery.
- **Dispatch.** The `chat_message_received` event (`services/chat/events.py`, source `opencompany://services/chat`)
  has the run id as its CloudEvent id and carries `message_id` and `run_id` in `data`, so the listener's child run
  id is `<slug>-<trigger label>-<run id>`. It is never broadcast.
- **Start and finish.** MachinaWorkflow, behind the `machina-chat-run-v1` patch, reads the run id only from an event
  with that source and type, claims the run (`chat_run.start`: `pending` or `queued` to `running`, recording the
  Temporal workflow and run ids) once the firing trigger's output is stored, passes `run_scope {run_id, session_id}`
  to every node context, and finishes it at its single exit (`chat_run.finish`). With several chat triggers in one
  graph, the first to claim tracks the run and the others run untracked. Only the claimant may finish.
- **The reply.** `chatReply` with a `run_scope` saves the answer through `ledger.post_reply`: the first reply takes
  the run's reply id `a_<run id>`, another reply node in the same run `a_<run id>.<n>`, and a retry from the same node
  saves nothing new. A run whose conversation was reset or cleared posts nothing.
- **One chain.** Every message is appended after the session's active leaf (`chat_threads`) inside the same reserved
  transaction, so concurrent writes never fork the thread.
- **The watchdog** (`services/chat/watchdog.py`, started by `main.py`) sweeps every `runs.watchdog_interval_s` and
  ends the runs nothing will finish (codes above). A pending run's wait (`runs.pickup_timeout_s`) counts from the
  later of its creation and the server's start.

## Streaming, steps and Stop

Settings are in `server/config/chat_defaults.json` (`stream`, `steps`, `runs`).

- **Who streams.** `agent.prepare_payload` gives an agent a `chat_stream` (`services/chat/stream.py`
  `chat_stream_for`) when the run's `run_scope` reached it, it is not working for another agent (no
  `parent_node_id`), and its output goes straight to a node whose plugin declares `answers_chat_run` (Reply in Chat).
  The payload also carries `chat_run_id` for every agent of the run. Both ride the AgentWorkflow's activity inputs
  only (LLM steps and tool calls), so no workflow patch was needed and recorded histories replay unchanged.
- **Text.** `agent.execute_llm_step` passes a `ChatStreamEmitter` as the provider's `on_event` sink (providers that
  declare `streaming` in `llm_defaults.json` stream; for the others the unifier replays the finished response as
  events: see [Native LLM SDK](./native_llm_sdk.md)). Deltas go out every `stream.flush_ms` or `stream.flush_chars`.
  A retried attempt first sends `opencompany.segment_discarded` for each earlier attempt's segment. Streaming never
  changes the step's result.
- **Steps.** `BaseNode.as_activity` wraps every tool call that carries a `chat_stream` and a `tool_call_id`
  (`services/chat/steps.py`): `step.started` when it begins, `step.finished` when it ends, saved on the run as it
  finishes (`ledger.record_step`, at most `steps.max_per_run`). The label is the plugin's `chat_step`
  ("Searched the web"), else "Used <display name>"; plugins with `chat_step_hidden` (the clock, the checklist) show
  none, and skill loads (`agent.skill.invoke`) never pass through it. A tool may put a short line in its result as
  `_step_detail` ("3 events on Saturday"): it is shown under the step and taken out before the model reads the
  result.
- **Stop** (`stop_chat_run`, `ledger.request_stop`). A run nothing has picked up (pending, or queued until Resume)
  ends `stopped` at once and frees the lane; a workflow that picks it up later claims it still `stopped`
  (`chat_run.start`) and its agent answers nothing. A running run moves to `stopping` (`custom`
  `opencompany.stopping`) and stops itself:
  - its agent's model step polls the run every `runs.stop_poll_s` while the provider writes (an activity heartbeat
    arrives too late to stop a stream mid-sentence), drops the call, and returns what the owner saw so far as the
    final answer; a step about to start returns at once, before paying for a request;
  - a tool call not started yet is answered "Not run: the owner stopped this answer." without running;
  - Reply in Chat saves the partial answer with `status: "stopped"`, and the run finishes with outcome `stopped`.
  
  A run still stopping `runs.stop_grace_s` after Stop (a long tool, a lost workflow) is ended by the watchdog, which
  cancels its Temporal workflow. The conversation keeps the stopped turn: the request and the partial answer (its
  text block marked `stopped`), or the request alone when nothing was written; a tool call a cancelled run left
  without a result is answered when the conversation is next loaded (see
  [Agent Context Flow](./agent_context_flow.md)).

## Client

`client/src/features/chat/` is the shared chat; only its `index.ts` is public (an ESLint rule keeps the rest
private, tests excepted). Hosts give it a `ChatHost` (`host.ts`): the session and scope, who answers, whether the
message box sends now, waits for Resume or is closed, and what sits around the conversation (notices, a top slot, a
slot after the thread, a footnote, how to tell the owner something). Home's employee page (`EmployeeChat`) and the
editor's console pane (`ConsoleChat`, compact, scope `live`) are the two hosts.

- **Run events** reach `stores/chatRunStore.ts` through one `case 'chat_run_event'` in `WebSocketContext.tsx`.
  `lib/agui/events.ts` checks each frame (source, type prefix, `subject`, `id` = `<run id>:<seq>`, scope fields) and
  `lib/agui/reduceRun.ts` folds it, the same way `services/chat/reducer.py` does. Frames are folded once per
  animation frame. Per run: a duplicate `seq` changes nothing; a gap, an unknown run already past `seq` 1, a new
  `hub_epoch` or the hub's resync frame mark the session `syncing`, hold what arrives meanwhile, and ask for a fresh
  snapshot, after which the held frames fold in and the ones the snapshot covers drop out.
- **Subscribing** (`data/runs.ts`): a mounted chat subscribes its session whenever the socket is ready (so again
  after every reconnect) and whenever the store asks for a snapshot, retrying a failed subscribe after 1, 3, then
  every 10 seconds; the last chat following a session unsubscribes it. Runs the store held as live that a snapshot
  no longer lists ended unseen and are read once with `get_chat_run`. Until the first snapshot the thread's own
  `active_runs` stand in; after it the store alone says which runs are live, and a message whose `run` still reads
  live is read with `get_chat_run` (`useThreadRunReconcile`).
- **Turns** (`thread/model.ts`): one per message with a divider where `run_key` changes. A run that is going,
  failed or was stopped (or finished, while its answer is on the way) shows on its last answer, or on a turn of its
  own right after its last message before it has answered (a live run whose messages are out of view goes last). Failures whose code only says no answer came
  (`not_delivered`, `timed_out`, `interrupted`) disappear once an answer lands; `reset` and `cleared` never show. A
  message sent from this tab and its saved row share a key (its `client_message_id`), and a run's first answer takes
  the key its run's turn had, so neither remounts.
- **Sending** (`data/send.ts`): the message shows at once; the server's answer admits its run into the store
  (`queued` or `pending`), so the employee shows working before the run's first event. While the lane is held, Send
  is Stop. A refused or failed send takes the message out of the thread and puts its text back in the box
  (`state/composerStore.ts`, a draft per session that survives switching conversations); after a failure in transit
  the draft keeps its `client_message_id`, so sending it again is the same message.
- **A working run** (`turns/AssistantTurn.tsx`): skeleton lines until text comes; then the latest segment, muted
  while it is narration, with a caret while it streams (`ReplyMarkdown` renders each finished block once,
  `markdown/blocks.ts`); a status line saying "Thinking", "Writing · N tok/s" or "Stopping…", with an Esc hint. The
  steps disclosure (`turns/StepsDisclosure.tsx`) sits on the run's first turn: "Working…" and open while the run
  works, "Worked for 12s · 3 steps" after; a run read back later starts it closed. A finished run keeps its turn
  until its saved reply lands in the thread, so the streamed answer and the reply stay one element.
- **Stop** (`data/stop.ts`): the Stop button, or Esc anywhere in the pane, sends `stop_chat_run` for the lane's run
  and applies the answer to the store at once; the run's events take it from there.
- **The box's extras** (`composer/`): Attach, a paste and a drop on the chat (`DropOverlay`) all call
  `addAttachments` (`composer/attachments.ts`), which uploads each file at once into the box
  (`state/attachmentStore.ts`, chips with progress and Remove); Send waits while one uploads and sends the finished
  ones' paths. The microphone (`VoiceRecorder`, MediaRecorder with live levels) shows when `dictation_status` says a
  provider can transcribe. A draft that is one word starting with `/` opens `SlashMenu` (Popover over cmdk; focus
  stays in the box, which carries `aria-controls` and `aria-activedescendant`). The Web chip keeps the employee off
  web search for the next messages (`composerStore.web`). In an empty chat, commands marked `suggest` show as cards
  that fill the box. Cmd/Ctrl+K focuses the box on Home; the editor's palette has Focus Chat.
- **Changing the conversation** (`data/branches.ts`, `thread/turnActions.ts`): under the owner's message a hover bar
  (`turns/UserTurn.tsx`: time, ‹ 1 / 2 › between versions, Edit, Copy; Edit opens `turns/UserEditBox.tsx` in place,
  Enter sends, Esc cancels, ArrowUp in an empty box edits the last message); under a finished answer
  (`turns/ReplyActions.tsx`) Copy, Good and Bad (pressing again takes the rating back), Try again on the latest
  answer, ‹ 1 / 2 › between answers, and the time. A run that stopped or failed without an answer has Try again in
  its note. Each command sends the thread's revision as read; a refusal is told in the host's words
  (`turns/runCopy.ts` `branchRefusalText`), `not_running` through the host's own refusal. A rating shows at once and
  goes back when it does not save; its toast says where it goes (`feedbackThanks`). Suggested questions hide while
  the box holds text, and under a stopped answer.
- **Generated UI** (`features/chat/genui/`, `turns/GeneratedUiBlock.tsx`): a reply's interfaces come from its saved
  `parts.ui`, or while the run streams them from its `json_render` activities (`data/parts.ts`), the run's first turn
  keeping them until the saved reply carries them, so one element shows throughout and keeps what the owner set. The
  renderer (`genui/ChatUi.tsx`, with json-render, in its own chunk) sanitizes again (`genui/prepare.ts`), draws the
  twelve components (`genui/views.tsx`, through `lib/jsonRender/guard.tsx`), reveals a live one element by element
  (forward only, so patches arriving in bursts never restart it), and routes any button action (`genui/actions.ts`,
  a Proxy over action names). Development builds show the element and patch counts and an Inspect view.

## Approvals

What an employee sends to someone waits for the owner's OK while its workflow asks first
(`server/services/approvals/`, models in `server/models/approvals.py`).

- **The rule.** `workflow_rules` holds a workflow's Ask first, read every time something would send, so the chat's
  Ask first chip (`set_ask_first`) changes it with no restart. An employee's row is seeded from its ground rules the
  first time anything asks (a seeder the employees package registers), and a change is mirrored back into those
  rules and the employee's card. A workflow with no row (one built in the editor) has no rule: its tool calls run
  as before, and its approval gates wait for the owner as they always did.
- **Two kinds of draft.** A `gate` row is an approvalGate node holding its run (an app reply). A `tool_call` row is
  a call to a tool whose plugin declares an `approval` spec (`services/plugin/approval.py`: which operations send,
  who it goes to, the body and subject the owner may edit, the card's other lines). `BaseNode.as_activity` checks
  every agent tool call (`services/approvals/tool_calls.py`): with Ask first on, a call that sends does not run; its
  row keeps what would run (the node's settings with the call's arguments over them, never the identity the node
  sends as) and the model reads that it waits for the owner. A tool that cannot wait is refused (Stripe) or runs
  restricted (the browser, read-only). With Ask first off the call runs; one made answering the owner in the chat
  leaves a row (`approved_by: auto`) with how it went. An approval gate with the rule off lets its draft through at
  once, recorded the same way.
- **Decisions** (`services/approvals/decisions.py`), each a compare-and-swap on the row's revision recorded in
  `approval_decisions`: `send` (pending -> approved; it goes after `UNDO_SECONDS`, 5, and Undo works until then),
  `undo` (approved -> pending), `discard` (pending -> discarded; Restore works for `RESTORE_SECONDS` on a gate's draft,
  whose run waits that long, and until expiry for a held call), `restore` and `retry` (failed -> approved; when the
  send broke off it may have gone, so the owner confirms). A gate waits through the Undo and Restore windows before
  it lets the draft through or not.
- **Sending a held call.** Send starts `ApprovedToolCallWorkflow` (`approval-send-<id>-r<revision>`): it sleeps until
  the Undo window closes, claims the row at that revision (`approvals.claim_send`: approved -> sending; an Undo, a
  newer Send or a cancel moved it, and nothing runs), runs the node's own activity once with no retry, and records
  the outcome (`approvals.record_outcome`): `sent`, `not_sent`, or `unknown` when the activity broke off. The node's
  activity runs only under that claim. The first time the drafts are listed after a start, sends that never started
  are started again and sends that never reported end `failed` with an unknown outcome.
- **At most once.** Temporal runs a tool activity again only when an attempt broke off (the worker stopped, a
  timeout); a failure the node reports comes back as a result and is not retried. A later attempt of an agent's call
  that sends (Ask first off, or no rule) therefore does not send again (`tool_calls.resend_refusal`, checked in
  `BaseNode.as_activity` after the gate): the model reads `SendOutcomeUnknown` (it may have gone out), and a call
  answering the owner in the chat is recorded with outcome `unknown`, so the card offers Try again behind a
  confirmation. Held calls are drafts, made once per call, and still retry.
- **The employee hears how it went**: an `[update]{...}[/update]` note (`approval:<id>`) at the start of its next
  turn in the chat, for a send that went, failed or was discarded.
- **In the chat.** A draft made answering the owner is recorded on the run (`parts.approvals`) and shown at once (an
  `activity.snapshot` with `activity_type: "approval"`); its card sits on that reply. A draft made in a run a button
  press started (its owner message carries `meta.ui_event`) records that interface (`ui_part_id`,
  `ledger.ui_part_of_run`), and the card says "Linked to the form above". Drafts no reply in view made (a
  gate's, from the employee's own work) show after the conversation. The card (`features/chat/approval/`): Discard /
  Edit / Send while it waits, "Sends in Ns" with Undo, Sending, Sent (and when, and whether Ask first was off),
  Restore while it can, Try again (asking first when it may have gone), and "Sends when you resume" for a gate's
  draft whose employee is paused. Countdowns use the server's clock (`server_time`). Ctrl/Cmd+Enter sends the newest
  waiting draft; in the edit box it sends that one.
- **Ends.** Clearing or resetting the chat cancels the drafts its runs made that still wait; a Reset cancels the
  waiting gates too (`approvalGate.reset_execution_state`); deleting the workflow deletes its drafts and its rule.
- **Broadcast.** `approval_lifecycle` (source `opencompany://services/approvals`, type
  `com.opencompany.approval.<stage>`: requested, decided, undone, restored, sending, sent, failed, expired,
  cancelled), identity only, with the chat run when there is one.

## Attachments

`services/chat/attachments.py`. The chat uploads each file first (`POST /api/workspace/{workflow_id}/uploads`, which
stores it under `uploads/`) and sends only the paths with the message. The server rebuilds each reference from the
file itself (name, type, size, address), refusing a path outside `uploads/`, a file no longer there, or more than six
(`attachment_rejected`); only a workflow's chat takes files, and a message may be files alone. The message keeps the
references (`attachments` on the wire), the chat trigger's output carries them, and an edit keeps the original's.

The agent answering the run reads them after the owner's words as
`[attachments]{"files": [{path, name, type, bytes}]}[/attachments]` (`services/chat/guide.py` `chat_turn`), so its
file tools can open them. Images also travel on the opening user message as ref-only image blocks
(`agent.prepare_payload` `user_images` -> `agent_workflow._owner_message`), hydrated per provider call for a provider
that declares `vision.user_images` (`llm_defaults.json`: Anthropic, OpenAI, Gemini; each encoder's own shape) and named
with their workspace path for any other.

**Web off** (`options.web: false`, kept on the run) leaves the tools in the `search` group out of that run
(`agent_activities._without_web_tools`). **Dictation** (`nodes/speech/_handlers.py`): the first provider in
`speech_defaults.json` `dictation.providers` with a stored key transcribes a recording the chat uploaded, and the
recording is deleted. **Commands** come from `chat_defaults.json` `commands.generic` and the employee's apps
(`employee_apps.json` `commands`, found through the saved graph's node types); `{name}` is the workflow's name, and
`suggest` puts a command in an empty chat. `capabilities.web` is whether the saved graph has a search tool.

## Branches

`services/chat/branches.py`. A session's messages form a tree: each row names the one before it (`parent_uid`) and
`chat_threads.active_leaf_uid` is where the path shown ends; `get_chat_messages` returns that path only.

- **Edit** (`edit_chat_message`) adds the new text beside the message it edits (same parent; `meta.edit_of` names
  the original) and starts a run of kind `edit` answering it (`parent_run_id`: the original's run).
- **Try again** (`regenerate_chat_reply`) moves the leaf back to the owner's message and starts a run of kind
  `regenerate` for it (`user_message_id`: that message), whose answer goes beside the old one. Only the answer the
  path ends at, or the owner's last message when its run ended without an answer.
- **Switch** (`switch_chat_branch`) moves the leaf to the newest message in the chosen version's branch.

**Memory follows the path.** `agent.prepare_payload` records, on the chat run an agent with a stored conversation
works for, that conversation as the run began (`chat_runs.context_cursors[agent] = {generation, length, digest}`,
the digest leaving out the stored `ts` stamps; the first preparation in a run wins). A move takes every agent the
runs on the part it leaves touched back to the cursor of the first such run, after checking the stored conversation
still starts that way, and keeps their conversations under the left branch's leaf (`chat_branch_snapshots`, at most
`branches.max_snapshots_per_session` per session and `branches.max_snapshot_bytes` each, `config/chat_defaults.json`).
A switch restores the snapshots of the branch it moves to. It all runs in one reserved write transaction under the
conversation store's locks (`conversation_lock`), and is refused with:

- `revision_conflict`: `expected_revision` is not the thread's revision (something was added meanwhile);
- `run_in_progress`: a run holds the lane;
- `cannot_rewind`: a stored conversation no longer starts the way it did (summarized or cleared since); going back to
  an empty conversation always works;
- `branch_unavailable`: the branch switched to has no kept conversation for an agent that worked on it;
- `older_generation`: a message or run from before the live generation;
- `not_editable` / `not_found`: not the owner's text message a run answered, not the latest answer, or not on the
  path.

After it commits, the Context listeners hear of each conversation changed, the drafts the runs on the part left made
that still wait are cancelled (`approval_lifecycle` `cancelled`), and an `[update]` note (`branch:<run id>`) tells the
employee what those runs sent anyway. `chat.updated` follows (role `user` for an edit, null for a retry or a switch).

## Feedback

`set_chat_feedback` keeps the owner's rating of an answer (`chat_feedback`, one per answer; `services/chat/
feedback.py`) and leaves a `[feedback]{"rating": "good" | "bad", "answer": "<excerpt>"}[/feedback]` note
(`feedback:<message id>`; taking the rating back drops it while untold). `reaches` lists where the rating goes:
`next_turn`, plus what a listener registered with `register_feedback_listener` adds (a plugin that keeps it too
answers `memory`; none is registered yet).

## Notes to the employee

Things the employee should learn on its next turn are kept in `chat_notes` (`services/chat/notes.py`) and put ahead
of that turn's user message, one bracketed line each: `[ui-state]{"ui_id", "state"}[/ui-state]`, written by
`chat_ui_state` for what the owner set without pressing anything; `[update]{...}[/update]` for what became of a draft
(`approval:<id>`) or what a branch left behind still sent (`branch:<run id>`); and `[feedback]{...}[/feedback]` for a
rating. A note is keyed per session (`ui-state:<part id>`), so a newer one replaces an older one not yet
told. `agent.prepare_payload` claims the session's untold notes for the agent answering a run (the one with a
`chat_stream`); the run's end marks them told when it answered (it finished, or it stopped having written
something), so a run that failed or stopped before writing offers them again, and a note changed after its claim stays
untold. Clearing the chat forgets them.

The answering agent's system prompt ends with a fixed guide (`services/chat/guide.py` `CHAT_REPLY_GUIDE`, byte for
byte the same on every turn): bracketed lines come from OpenCompany, not from the owner; a search result numbered
n is cited as `[n]`; and how to suggest follow-ups.

## Follow-ups

The answering agent may end its reply with `<followups>["…", "…"]</followups>` (a list of lines works too). The
stream holds the block back; Reply in Chat takes it off the reply (`guide.split_followups`: the last block, at most
3 suggestions, 160 characters each, repeats dropped) and saves the suggestions as `parts.followups`. Outside a chat
run the block is dropped, and a message that is only the block posts nothing. The chat shows them as buttons under
the latest answer only, once it is done; one sends its text as the owner's message, or waits in the box while an
answer is still coming.

## Sources

A plugin that declares `chat_sources` (the web searches) returns `results` with addresses. When the answering
agent calls it, `BaseNode.as_activity` numbers them for the conversation (`services/chat/sources.py`: the session's
counter `chat_threads.next_source`, so a number never repeats in a conversation and an older `[n]` keeps meaning
what it did), writes `n` on each result the model reads, and saves `{n, title, url, detail?}` (at most 10 per call)
as a `sources` part of the run; the reply carries them as `parts.sources`. The guide tells the agent to cite what it
relies on as `[n]`. The client numbers a reply's sources 1, 2, ... in the order its text first cites them
(`markdown/citations.ts`): each `[n]` outside code reads as a chip with that number and a tooltip naming the
source, and the cited sources are listed under the answer (`turns/SourceChips.tsx`); sources it did not cite are
not shown. A reply may cite a source an earlier search found: each answer resolves `[n]` against the conversation's
sources up to it (`thread/model.ts`, `ChatTurn.sources`).

## Fixtures

`client/src/features/chat/__fixtures__/` holds the design handoff's examples in this protocol's shapes, for client
and server tests alike:

- `<name>.spec.json` and `<name>.patches.jsonl` (`saturday-booking`, `reply-insights`, `reminders`): json-render
  specs and the depth-first patch stream for each. SlotPicker options use the catalog's generic keys (`time`,
  `detail`, `recommended`, `unavailable`, `note`) instead of the handoff's salon ones.
- `saturday-booking.events.json`: the handoff's AG-UI run as `chat_run_event` frames, as the server sends them:
  steps, text, a generated UI streamed as `activity.delta` patches, the card of a held WhatsApp send
  (`activity_type: "approval"`, as `parts.show_approval` publishes it), and `finished` with outcome `success`.

## Error codes

| Code | Where | Meaning |
|---|---|---|
| `run_in_progress` | send, edit, regenerate, switch | A run is live in this session; `run_id` names it (send only). |
| `save_failed` | send, edit, regenerate, switch | The change could not be saved; nothing was dispatched. |
| `not_running` | send, edit, regenerate, switch | The employee is not running and cannot queue messages (a switch: nothing was started since the last Reset). |
| `invalid_request` | send, save | A malformed field (`detail` says which): an empty message, a role other than the owner's, or a bad `client_message_id`. |
| `read_failed` | get_chat_messages, chat_subscribe | The thread could not be read. Never answered as an empty thread. |
| `not_found` | get_chat_run, stop_chat_run | No such run. |
| `attachment_rejected` | send | A file outside `uploads/`, gone, or more than six; `detail` says which. |
| `not_stoppable` | stop_chat_run | The run ended before Stop reached it; `state` says how. |
| `revision_conflict` | edit, regenerate, switch | `expected_revision` is not the thread's revision. |
| `conflict` | set_ask_first | `expected_revision` is stale. |
| `not_editable` | edit, regenerate, decide | Not the owner's text message a run answered, not the latest answer, the editor's `default` chat, or an edited argument that is not editable. |
| `older_generation` | edit, regenerate, switch | A message or run from before the employee restarted. |
| `cannot_rewind` | edit, regenerate, switch | An agent's stored conversation no longer starts the way it did when the run being undone began. |
| `branch_unavailable` | switch | The branch's kept conversations are gone (too many branches, or too large to keep). |
| `ui_event_rejected` | send | The element or action does not match the saved spec. |
| `access_denied` | all | The socket's principal does not own the workflow, or it is the internal worker socket. |
| `too_late` | decide | The Undo or Restore window has passed. |
| `speech_unavailable` | transcribe_audio | No dictation provider has a stored key. |
