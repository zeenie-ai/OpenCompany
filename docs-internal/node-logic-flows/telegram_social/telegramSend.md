# Telegram Send (`telegramSend`)

| Field | Value |
|------|-------|
| **Category** | social |
| **Backend handler** | [`server/nodes/telegram/telegram_send.py`](../../../server/nodes/telegram/telegram_send.py) (`TelegramSendNode`); dispatch via `BaseNode.execute()` -> `@Operation("send")` -> [`_send.py`](../../../server/nodes/telegram/_send.py) (`resolve_chat_id` + `perform_send`) |
| **Tests** | [`server/tests/nodes/test_telegram_social.py`](../../../server/tests/nodes/test_telegram_social.py) (node level), [`test_telegram_service.py`](../../../server/tests/nodes/test_telegram_service.py) (service level: split / caption spill) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | yes - `usable_as_tool = True`, tool name `telegram_send`; its `ApprovalSpec` holds each send for the owner's OK while they ask first |

## Purpose

Send text, photo, document, location, or contact messages through a connected
Telegram bot (python-telegram-bot v22.x). The node leans on the `TelegramService`
singleton for the actual Bot API calls; the `send` operation only resolves the
recipient and packages the envelope.

**Validation and message-type dispatch live in [`_send.py`](../../../server/nodes/telegram/_send.py)**,
not in the operation body. The direct WebSocket command
(`_handlers.py::handle_telegram_send`) routes through the same `perform_send`,
so the two entry points cannot diverge. They previously carried independent
copies and had already drifted — the socket path never forwarded `silent` or
`reply_to_message_id`. Dispatch is a table keyed by message type, so adding a
type is one entry plus one `Literal` member rather than a branch in two files.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data - not read directly by the handler; parent nodes typically resolve templates into `text` / `caption` via ParameterResolver before the call. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `recipient_type` | options | `self` | no | - | One of `self` / `user` / `group` |
| `chat_id` | string | `""` | yes when `recipient_type != self` | `recipient_type: ['user','group']` | Numeric chat id or `@username` |
| `message_type` | options | `text` | no | - | `text` / `photo` / `document` / `location` / `contact` |
| `text` | string | `""` | yes when `message_type=text` | `message_type: ['text']` | Message text |
| `media_url` | string | `""` | yes when `message_type` in `photo`/`document` | `message_type: ['photo','document']` | Remote URL or `file_id` |
| `caption` | string | `""` | no | `message_type: ['photo','document']` | Optional caption |
| `latitude` | number (Optional[float], default `None`) | `None` | yes when `message_type=location` | `message_type: ['location']` | Latitude. `None` (not `0`) so deliberate `0.0` (Null Island) is distinguishable from "unset" |
| `longitude` | number (Optional[float], default `None`) | `None` | yes when `message_type=location` | `message_type: ['location']` | Longitude |
| `phone_number` | string | `""` | yes when `message_type=contact` | `message_type: ['contact']` | Contact phone |
| `first_name` | string | `""` | yes when `message_type=contact` | `message_type: ['contact']` | Contact first name |
| `last_name` | string | `""` | no | `message_type: ['contact']` | Contact last name |
| `parse_mode` | options | `Auto` | no | `message_type: ['text','photo','document']` | `Auto` / `""` / `HTML` / `Markdown` / `MarkdownV2` |
| `silent` | boolean | `false` | no | - | Sends without notification (`disable_notification=True`) |
| `reply_to_message_id` | number | `0` | no | - | If truthy, coerced to `int` and passed as `reply_to_message_id` |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope containing Bot API message metadata |

### Output payload

The operation returns this dict (validated against `TelegramSendOutput`,
`extra="allow"`):

```ts
{
  message_id: number;              // first message when the send became several
  chat_id: number;
  message_type: 'text' | 'photo' | 'document' | 'location' | 'contact';
  date: string;                    // ISO timestamp from Telegram
  sent: true;

  // Present only when one logical send became several Telegram messages.
  parts?: number;                  // text over 4096 -> chunk count
  message_ids?: number[];          // every id in the chunk chain
  caption_truncated?: boolean;     // caption over 1024 spilled
  follow_up_message_ids?: number[];// ids of the threaded caption remainder
}
```

`sent` was declared on the model from the start but never populated, and
`_serialize_result` dumps with `exclude_unset=True`, so until it was emitted
the data picker advertised a field that never materialised. `message_type` and
`date` were always returned but undeclared, surviving only through
`extra="allow"`; declaring them changed nothing at runtime and made them
visible in the picker.

`BaseNode.execute()` wraps it as `{ success: true, result: <payload>, execution_time, timestamp, node_id, node_type }`.

## Logic Flow

```mermaid
flowchart TD
  A[TelegramSendNode.send op] --> B{TelegramService<br/>connected?}
  B -- no --> Efail[raise RuntimeError<br/>BaseNode wraps as success=false]
  B -- yes --> C{recipient_type?}
  C -- self --> D[chat_id = service.owner_chat_id]
  D --> D2{owner set?}
  D2 -- no --> D3[Fetch 'telegram_owner_chat_id'<br/>via auth_service.get_api_key]
  D3 -- ok --> D4[service.set_owner + use]
  D3 -- missing --> Efail
  C -- user/group --> E{chat_id truthy?}
  E -- no --> Efail
  E -- yes --> F[_send.perform_send<br/>table lookup on message_type]
  F --> G{known type?}
  G -- no --> Efail
  G -- yes --> H[Per-type validation<br/>NodeUserError when unmet]
  H --> I{captioned media?}
  I -- no --> Tsend[service.send_message / location / contact]
  I -- yes --> Msend[service.send_photo / send_document<br/>-> _send_captioned_media]
  Msend --> J{caption over 1024<br/>UTF-16 units?}
  J -- no --> Ok
  J -- yes --> K[Truncate at boundary<br/>send remainder as threaded reply]
  K --> Ok
  Tsend --> Ok
  Ok[Return success=true<br/>message_id, chat_id, message_type, date, sent]
```

## Decision Logic

- **Connection gate**: If `service.connected` is False the op raises
  `RuntimeError("Telegram bot not connected. Add bot token in Credentials.")`
  (wrapped by `BaseNode.execute()` into a `success=false` envelope). No retry.
- **Self recipient**: Uses `service.owner_chat_id` (auto-captured on the first
  private message). If not set, the op tries
  `get_auth_service().get_api_key("telegram_owner_chat_id")` and restores the owner by
  calling `service.set_owner(int(saved))`. Any exception during restore is
  logged at WARNING and swallowed - the op continues and ultimately raises
  the "Bot owner not detected" `RuntimeError` when the chat_id is still falsy.
- **Parse mode**: Passed through to the service. The service itself implements
  `Auto` (GFM -> Telegram HTML via `markdown_formatter.to_telegram_html`) plus the
  `BadRequest "can't parse entities"` fallback that retries with `parse_mode=None`
  and the original unescaped text.
- **Message type dispatch**: `_send.perform_send` looks the type up in a
  module-level table. An unknown value raises
  `NodeUserError("Unsupported message type: <x>")`. Per-type validation
  (`text` present, `media_url` present, both coordinates set, phone plus first
  name) also raises `NodeUserError`, so the operator log gets one WARN line
  with no traceback — these are user-correctable inputs, not server bugs.
- **Caption spill**: Telegram caps captions at 1024 units and rejects the whole
  send past that. `_send_captioned_media` truncates at the cap and sends the
  remainder as a reply threaded under the media message, reusing the chunking
  `send_message` already performs. The split runs on the **raw** caption before
  `_resolve_body` converts markdown to HTML — splitting after conversion could
  separate a `<b>` from its closing tag, which Telegram rejects with
  "can't find end of the entity". Telegram measures the cap against
  entity-parsed text, so truncating raw markdown is conservative.
- **Reply-to coercion**: `reply_to_message_id` is cast via `int(...)` only when
  truthy. A non-numeric string here raises `ValueError`, which `BaseNode.execute()`
  catches and surfaces as an error envelope.

## Side Effects

- **Database writes**: none from the op directly. The owner is persisted via
  `auth_service.store_api_key("telegram_owner_chat_id", ...)` only inside
  `TelegramService._on_message_received`, not from the send path.
- **Broadcasts**: none from the send op. The service broadcasts the typed
  `telegram.status` CloudEvents envelope (wire key `telegram_status`) on
  connect / disconnect only - see `server/nodes/telegram/_events.py`.
- **External API calls**: Telegram Bot API via `python-telegram-bot` (`bot.send_message`,
  `bot.send_photo`, `bot.send_document`, `bot.send_location`, `bot.send_contact`).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key("telegram_owner_chat_id")` when
  restoring the self-owner. Bot token comes from `auth_service.store_api_key("telegram", ...)`
  and is consumed by `TelegramService.connect()` elsewhere, not by this op.
  Declared on the plugin via `credentials = (TelegramCredential,)`.
- **Services**: `TelegramService` singleton (`server/nodes/telegram/_service.py::get_telegram_service`).
- **Python packages**: `python-telegram-bot` (v22.x), `markdown-it-py` (via
  `markdown_formatter`).
- **Environment variables**: `TELEGRAM_OWNER_CHAT_ID` (read by the service as a
  fallback for `owner_chat_id`).

## Edge cases & known limits

- **Swallowed owner-restore exceptions**: Any error looking up
  `telegram_owner_chat_id` is logged at WARNING and continues; the user only
  sees the downstream "Bot owner not detected" error.
- **Silent ValueError on `reply_to_message_id`**: A non-numeric value raises
  `ValueError`, surfaced by `BaseNode.execute()` as the error message.
- **Long captions arrive as two messages**: A caption over 1024 units produces
  the media message plus one or more threaded follow-ups. The follow-up carries
  `disable_notification=True` so a single logical send still pings once.
  `caption_truncated` and `follow_up_message_ids` report it.
- **Length is counted in UTF-16 units, not characters**: an emoji costs 2. A
  1024-character caption of emoji is 2048 units and will spill. This matches
  how the Bot API measures its own limits; `len()` under-counts.
- **`recipient_type` is a `Literal["self","user","group"]`**: Pydantic rejects
  any other value at param validation. For `user`/`group` an empty `chat_id`
  raises `RuntimeError("chat_id is required")`.
- **Markdown fallback leaks unescaped text**: If `send_message` raises
  `BadRequest("can't parse entities ...")`, the service retries with the
  ORIGINAL (unescaped) text at `parse_mode=None`, not the escaped copy. If the
  original contains markup, it is visible to the recipient as literal characters.
- **`reply_to_message_id=0`**: Falsy, so the handler does NOT pass it through -
  the node silently drops the reply reference.
- **`parse_mode='None'` (string)**: Not treated as "no parse mode"; passed
  through to Telegram, which will reject it. The frontend uses empty string
  `""` for "None" so this is a misconfiguration hazard only for manual JSON
  imports.

## Related

- **Sibling nodes**: [`telegramReceive`](./telegramReceive.md), [`socialSend`](./socialSend.md)
- **Service**: [`server/nodes/telegram/_service.py`](../../../server/nodes/telegram/_service.py)
- **Markdown formatter**: [`server/services/markdown_formatter.py`](../../../server/services/markdown_formatter.py)
- **Architecture docs**: [Credentials Encryption](../../credentials_encryption.md), [Status Broadcaster](../../status_broadcaster.md)
