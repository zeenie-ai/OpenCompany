# Discord Send (`discordSend`)

| Field | Value |
|------|-------|
| **Category** | discord |
| **Backend handler** | [`server/nodes/discord/discord_send.py`](../../../server/nodes/discord/discord_send.py) (`DiscordSendNode`, on `AccountScopedNode`); HTTP in [`_base.py`](../../../server/nodes/discord/_base.py) |
| **Tests** | [`server/tests/nodes/test_discord.py`](../../../server/tests/nodes/test_discord.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | yes — tool name `discord_send` |

## Purpose

Post a message to a channel or open a DM with a user. Text, rich embeds and a
file attachment. Kept separate from `discordAction` because it is the
high-frequency agent-facing operation and a twenty-operation union makes a poor
LLM tool schema — the same split telegram and whatsapp_business make.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data for template resolution |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `account_id` | string | `""` | no | – | Which bot sends. Blank = default credential. **Stripped from model arguments.** |
| `target_type` | `channel \| user` | `channel` | no | – | Post in a channel or DM a user |
| `channel_id` | string | `""` | yes* | `target_type=channel` | Channel snowflake |
| `user_id` | string | `""` | yes* | `target_type=user` | User snowflake; a DM channel is opened |
| `message` | string | `""` | no | – | Body; split past 2000 characters |
| `embeds` | list[dict] | `[]` | no | – | Up to 10 embed objects |
| `attachment` | file | `None` | no | – | One file, sent as multipart |
| `reply_to_message_id` | string | `""` | no | – | Quote a message |
| `suppress_embeds` | bool | `false` | no | – | Message flag `1 << 2` |
| `tts` | bool | `false` | no | – | Text-to-speech |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | `DiscordSendOutput` |
| `output-tool` | – | Auto-appended for `usable_as_tool` |

### Output payload (TypeScript shape)

```ts
{
  message_id: string;      // the FIRST message when split
  channel_id: string;
  parts?: number;          // present only when split
  message_ids: string[];   // every message id, in order
}
```

## Logic Flow

```mermaid
flowchart TD
  A[Validate params] --> B{target_type}
  B -- user --> C[POST users/@me/channels -> dm channel id]
  B -- channel --> D[channel_id]
  C --> E{content, embeds or attachment present}
  D --> E
  E -- none --> ERR[NodeUserError]
  E -- ok --> F{embeds > 10}
  F -- yes --> ERR
  F -- no --> G[split_content at 2000]
  G --> H[for each chunk]
  H --> I{last chunk and attachment}
  I -- yes --> J[multipart: files[0] + payload_json]
  I -- no --> K[POST channels/id/messages]
  J --> L[collect message id]
  K --> L
  L --> M[Return first id + parts + message_ids]
```

## Decision Logic

- **Validation**: empty send (no text, embed or attachment) → `NodeUserError`;
  >10 embeds → `NodeUserError` (Discord rejects the whole message, so failing
  locally is clearer); missing channel/user id → `NodeUserError`.
- **Branches**: DM opens a channel first — Discord has no send-to-user route.
- **Splitting**: `split_content` prefers a paragraph → line → sentence → space
  boundary past the halfway mark, else hard-cuts. Length is `len()`: Discord
  counts code points, unlike Telegram's UTF-16 units.
- **Ordering**: the reply reference rides only the first chunk; embeds and the
  attachment ride the last, so they render after the text they belong to.

## Side Effects

- **Broadcasts**: standard node status via `BaseNode.execute`.
- **External API calls**: `POST /channels/{id}/messages`, `POST /users/@me/channels`,
  `Authorization: Bot <token>`.
- **Cost tracking**: `{"service": "discord", "action": "send", "count": 1}`.

## External Dependencies

- **Credentials**: `DiscordBotCredential` (`discord`), resolved per account via
  `_accounts.resolve_secrets`.
- **Python packages**: `httpx`.

## Edge cases & known limits

- One attachment per message. Discord allows 10; multiple would need a list
  param and multi-file panel support.
- `account_id` is in `server_controlled_fields`, which the framework enforces on
  every tool path (`BaseNode.execute_as_tool` in process, `NodeExecutor` on
  Temporal); `AccountScopedNode` strips it again as defence in depth.
- A 429 over ~30s raises a retryable `RuntimeError` so Temporal re-dispatches
  rather than pinning a worker slot.

## Related

- **Consumes**: `discordReceive` output (channel/author ids).
- **Architecture docs**: [discord_service.md](../../discord_service.md)
