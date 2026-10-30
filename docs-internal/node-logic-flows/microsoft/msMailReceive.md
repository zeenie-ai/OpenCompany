# Outlook Mail Receive (`msMailReceive`)

| Field | Value |
|------|-------|
| **Category** | microsoft / trigger (class `group = ("microsoft", "trigger")`) |
| **Backend handler** | [`server/nodes/microsoft/mail_receive/__init__.py`](../../../server/nodes/microsoft/mail_receive/__init__.py) (`MailReceiveNode`, on `PollingTriggerNode`); ctx-free Graph helpers in [`_base.py`](../../../server/nodes/microsoft/_base.py) (`graph_get_raw`, `mark_message_read_raw`); base loop in [`services/plugin/polling.py`](../../../server/services/plugin/polling.py); canary registration in [`nodes/microsoft/__init__.py`](../../../server/nodes/microsoft/__init__.py) |
| **Tests** | [`server/tests/nodes/test_microsoft.py`](../../../server/tests/nodes/test_microsoft.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no (trigger) |

## Purpose

Fire when a new message appears in an Outlook mail folder. Microsoft Graph
has no push channel here, so the node polls `mailFolders/{folder}/messages`
on an interval, diffs the visible ids against the previous cycle, fetches
each new message and emits its summary. Three execution paths share the same
four hooks: the Temporal `PollingTriggerWorkflow` (deployed, canary type
`com.opencompany.msmail.message.received`), the legacy asyncio collector, and
an inline `execute()` override for the canvas Run button.

## Inputs (handles)

None - this is a trigger; it is the head of a run.

## Parameters

`MailReceiveParams`, `extra="ignore"`. No `displayOptions` on any field.

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `mailbox` | string | `""` | no | - | Shared mailbox address to watch; empty = `/me` |
| `only_unread` | bool | `true` | no | - | Adds `$filter=isRead eq false` |
| `from_filter` | string | `""` | no | - | Adds `from/emailAddress/address eq '<addr>'` (single quotes doubled) |
| `folder` | string | `inbox` | no | - | Well-known folder name or folder id, used in `mailFolders/{folder}/messages` |
| `mark_as_read` | bool | `false` | no | - | PATCH `isRead: true` after emitting |
| `poll_interval` | int (10..3600) | `60` | no | - | Seconds between cycles; clamped by `poll_interval_clamp` |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | The summary dict from `_summarize` (plus `id` on the deployed paths) |

### Output payload (TypeScript shape)

The declared `MailReceiveOutput` model names the sender field `from_`, but
every path emits the raw `_summarize` dict without passing through
`_serialize_result` (the canvas path returns the dict from the `execute`
override; the deployed paths hand `fetch_detail`'s dict straight to the
queue / activity result). The wire key is therefore `from`:

```ts
{
  message_id: string;
  id?: string;                 // deployed paths only: stable dedup key (= message_id)
  conversation_id: string | null;
  from: string;                // sender address ("" when absent)
  from_name: string;
  to: string[];
  subject: string;
  body_preview: string;
  body: string;                // full body content
  received: string | null;     // receivedDateTime
  is_read: boolean | null;
  has_attachments: boolean | null;
  web_link: string | null;
}
```

## Logic Flow

```mermaid
flowchart TD
  subgraph deployed: poll.msMailReceive.v{version} activity / legacy coroutine
    D0[setup_service returns params] --> D1[fetch_ids: GET base/mailFolders/folder/messages $select $top 25 + $filter or $orderby]
    D1 -- baseline_only --> D2[return events empty, seen_ids = current]
    D1 --> D3[new_ids = current - prior_seen]
    D3 --> D4[per id: fetch_detail GET base/messages/id $select -> _summarize + id]
    D4 --> D5[post_emit: mark read if mark_as_read, failures logged]
    D5 --> D6[return events, seen_ids = current]
  end
  subgraph canvas Run: execute override
    C0[broadcast waiting status] --> C1[baseline fetch_ids, failure treated as empty]
    C1 --> C2[sleep poll_interval]
    C2 --> C3[fetch_ids; new_ids = current - seen]
    C3 -- none --> C4[seen = current] --> C2
    C3 -- some --> C5[pick next iter new_ids; seen = current]
    C5 --> C6[fetch_detail + post_emit + track usage read 1] --> C7[return success envelope with result = detail]
    C2 -- CancelledError --> C8[return success false, Cancelled by user]
  end
  T[every Graph call: ensure_fresh_microsoft_token -> httpx GET with Bearer; status >= 400 raises NodeUserError]
```

## Decision Logic

- **Query construction** (`_query`): `$select` =
  `id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,isRead,
  hasAttachments,webLink,conversationId`, `$top = 25`. With `only_unread`
  and / or `from_filter` a `$filter` is sent and `$orderby` is dropped
  (Graph returns `InefficientFilter` for filter + orderby on different
  properties without the advanced-query header); with no filter,
  `$orderby=receivedDateTime desc`. Because the trigger dedups by id it does
  not depend on ordering.
- **Dedup / window**: `seen_ids` is rebased to the provider's current window
  every cycle (both the activity and the coroutine), so a message that
  leaves the 25-item window and re-enters it later fires again. On the
  baseline pass nothing is emitted.
- **Temporal activity** (`as_poll_activity`): a `setup_service` or
  `fetch_ids` failure re-raises (Temporal retry policy owns it), except the
  "not connected" `PermissionError` annotated with `provider` (see Auth),
  which is raised as `NodeUserError` and not retried; a `fetch_detail` failure skips
  that id with a warning; `post_emit` failures are swallowed; the result is
  `{"events": [...], "seen_ids": [...]}`: the events of at most 512,000 bytes
  (the first always included, fetched within two thirds of the attempt's
  deadline), and the current ids minus the new ones the cycle deferred, which
  the next cycle takes.
- **Legacy coroutine** (`_build_poll_coroutine`): setup failure ends the
  poller; baseline failure logs a warning and treats everything as new on the
  next cycle; per-cycle exceptions are logged and retried next interval.
- **Canvas Run** (`execute` override): registers no `event_waiter`; instead
  it polls inline until the first cycle with new ids, then returns ONE
  message - `next(iter(new_ids))`, an arbitrary member of the set (the
  variable is named `newest_id` but set iteration is unordered). The
  remaining new ids are folded into `seen` and never emitted on that run.
  Cancellation returns `{success: False, error: "Cancelled by user"}`; any
  other exception outside the cycle returns `{success: False, error}`.
- **Auth**: `graph_get_raw` / `mark_message_read_raw` call
  `ensure_fresh_microsoft_token("owner")` (default user) and issue plain
  `httpx` requests with a 30 s timeout; no stored tokens ->
  `PermissionError(provider="microsoft", reason="missing", auth="oauth2")`.
- **`_clamp_interval`**: non-numeric -> `default_poll_interval` (60);
  result clamped to 10..3600.

## Side Effects

- **Database writes**: canvas Run records one `api_usage_metrics` row
  (`service="microsoft_graph"`, action `read`, cost 0.0) when a message
  fires; the deployed paths record none. Token refresh persists new tokens
  via `auth_service.store_oauth_tokens`.
- **Broadcasts**: canvas Run sends `update_node_status(node_id, "waiting",
  {"message": "Waiting for Outlook mail (polling every Ns)...",
  "event_type": "ms_mail_received"}, workflow_id=...)`. Deployed status
  broadcasts come from the trigger manager / `PollingTriggerWorkflow`.
- **External API calls**: `GET {base}/mailFolders/{folder}/messages`,
  `GET {base}/messages/{id}`, `PATCH {base}/messages/{id}` (`isRead: true`)
  on `https://graph.microsoft.com/v1.0` with `Authorization: Bearer`.
- **Mutation**: `mark_as_read` flips `isRead` on the source mailbox (the
  only write; best-effort, failure logged at WARNING).

## External Dependencies

- **Credentials**: `MicrosoftCredential` (`id = "microsoft"`, OAuth2,
  `Mail.ReadWrite` / `Mail.ReadWrite.Shared` scopes; `microsoft_client_id`
  and `microsoft_client_secret` api-key rows for refresh). Shared-mailbox
  polling needs Full Access on that mailbox.
- **Services**: Microsoft Graph v1.0; Temporal when deployed
  (`TaskQueue.TRIGGERS_POLL`; `register_canary_trigger_type("msMailReceive",
  "com.opencompany.msmail.message.received")` opts it into
  `PollingTriggerWorkflow`).
- **Python packages**: `httpx`, `pydantic`, `temporalio` (activity
  decorator on the deployed path).
- **Environment variables**: none read directly.

## Edge cases & known limits

- `$top` is fixed at 25: with `only_unread=true` (default) the window is
  the 25 most relevant unread messages in Graph's order; a burst of more
  than 25 new messages between cycles emits only the ones inside the window.
- Output key is `from`, not the `from_` declared on `MailReceiveOutput` -
  the model documents the shape but is not enforced on any path (contrast
  [`msMail`](./msMail.md), whose `read` op emits `from_`).
- `mark_as_read=true` combined with `only_unread=true` removes fired
  messages from the next window, which is the stable configuration; with
  `only_unread=false` and no `from_filter`, the window is the 25 newest
  messages and old ids churn out as mail arrives.
- The trigger never downloads attachments; it carries `has_attachments`
  and `message_id` so a downstream [`msMail`](./msMail.md)
  `download_attachments` can fetch them.
- Canvas Run returns one message per run and swallows the rest of that
  cycle's new ids.
- `event_type = "ms_mail_received"` is the legacy event-waiter key; the
  canvas path does not use the event waiter at all.

## Related

- **Consumers**: [`msMail`](./msMail.md) (`reply`, `download_attachments` on the emitted `message_id`)
- **Sibling pattern**: `googleGmailReceive` (same `PollingTriggerNode` hooks)
- **Architecture docs**: [Event Framework](../../event_framework.md), [Temporal Architecture](../../TEMPORAL_ARCHITECTURE.md), [Plugin System](../../plugin_system.md)
