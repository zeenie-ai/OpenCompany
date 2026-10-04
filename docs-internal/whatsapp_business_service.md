# WhatsApp Business (Meta Cloud API)

Official Meta WhatsApp Business Platform integration, living entirely in
[`server/nodes/whatsapp_business/`](../server/nodes/whatsapp_business/).

**This is not the same integration as `nodes/whatsapp/`.** That one drives a
*personal* account through an unofficial Go bridge (`edgymeow`) and QR pairing.
Both ship. They share no node type, no credential id, no WebSocket key and no
palette group, because the auth models, capabilities and failure modes have
nothing in common. The Cloud API group is visible in the default palette; the
personal one is `dev`-only.

## Node surface (4 nodes)

| Node | Kind | Operations |
|---|---|---|
| `whatsappBusinessSend` | action, agent tool | `send_text`, `send_media`, `send_template`, `send_buttons`, `send_list`, `send_cta_url`, `send_reaction`, `send_location`, `send_contacts`, `list_templates` |
| `whatsappBusinessMedia` | action, agent tool | `upload`, `get_url`, `download`, `delete` |
| `whatsappBusinessReceive` | trigger | inbound messages |
| `whatsappBusinessStatus` | trigger | delivery receipts |

### Why the node split is where it is

Meta models message type as a **field on one endpoint**
(`POST /{phone-number-id}/messages`; `type` ∈ text, image, video, audio,
document, sticker, location, contacts, reaction, interactive, template), not as
separate endpoints. Splitting by message type would make the canvas imply an API
shape that does not exist, so Send carries them all as operations. Earlier
`whatsappBusinessTemplate` and `whatsappBusinessInteractive` nodes were folded
in for exactly this reason.

Media stays separate because it genuinely is a different endpoint family
(`POST /{phone-number-id}/media`, `GET`/`DELETE /{media-id}`).

The two triggers stay separate for a structural reason: a canary trigger
registers exactly **one** CloudEvents type — `canary_registry` is a `str -> str`
map and the listener carries a single `EventType` keyword Search Attribute — so
one node cannot subscribe to both. Their payloads also share almost no fields,
so merging would mean a union output schema and weaker `{{trigger.field}}`
resolution.

`list_templates` is the one WABA-scoped call kept on Send, because it exists to
answer "what can I send".

## Credential

`WhatsAppBusinessCredential` (`ApiKeyCredential`, `id="whatsapp_business"`,
bearer). One secret plus four operator-metadata fields:

| Field | Required | Purpose |
|---|---|---|
| `apiKey` | yes | System User access token |
| `whatsapp_business_waba_id` | yes | WhatsApp Business Account id — template listing |
| `whatsapp_business_phone_number_id` | yes | The number messages are sent **from** |
| `whatsapp_business_app_secret` | no* | HMAC key for `X-Hub-Signature-256`; *required for inbound* |
| `whatsapp_business_verify_token` | no* | Echoed in the GET subscription handshake |

The probe calls `GET /{waba_id}/phone_numbers` — authenticated, free, and it
validates the WABA id at the same time, returning discovered numbers with their
`quality_rating`.

### The sending number is credential-only, by design

There is deliberately **no `phone_number_id` parameter** on any node. It selects
which business identity a message is sent *from*, which is exactly what a prompt
injection in an inbound message would want to set. Listing a declared field in
`server_controlled_fields` would keep the model's value out on every tool path
(`BaseNode.execute_as_tool` in process, `NodeExecutor` on Temporal), but the
model would still be offered the field: `ToolInput` is a `ToolNode` extension,
so the tool schema of a dual-purpose `ActionNode` is its whole `Params`.
Sourcing the number from the credential keeps it out of the schema, and out of
every parameter merge as defence in depth. Locked by `TestParamsShape` and
`TestModelCannotChooseTheSendingNumber`, which drives the real tool path with a
hostile `phone_number_id`.

## Webhook

Claimed via `register_webhook_source(get_webhook_source())` — an *instance*, not
the class. There is no plugin router; the shared catch-all in
[`routers/webhook.py`](../server/routers/webhook.py) dispatches by path, and a
claimed path never reaches the legacy generic handler (which would otherwise
fire every deployed `webhookTrigger`).

| Route | Purpose |
|---|---|
| `GET /webhook/whatsapp-business` | Subscription handshake — compares `hub.verify_token` with `hmac.compare_digest`, echoes `hub.challenge` as **bare `text/plain`** (Meta rejects the router's JSON envelope). 403 on mismatch or unconfigured token. |
| `POST /webhook/whatsapp-business` | Deliveries — `MetaHubVerifier` (SHA-256 HMAC over the **raw body**, `X-Hub-Signature-256`, `sha256=` prefix). Fails **closed**: 503 + `Retry-After` when the secret is unavailable, 400 on mismatch. |

`iter_events` walks all three nesting levels
(`entry[] -> changes[] -> value.messages[] / statuses[] / errors[]`) and emits
one CloudEvent per item. Account-level `errors[]` are logged only.

Both delivery paths are driven: `dispatch.emit` for deployed Temporal listeners,
and `event_waiter.dispatch` for the canvas-Run path.

**Replay dedup ids.** Meta retries for 7 days. Messages use the bare `wamid`;
statuses use a composite `f"{wamid}:{status}"`, because `sent -> delivered ->
read` all carry the same message id and a bare wamid would collapse them into
one event.

**Known risk, documented in code**: Meta's *Messenger* docs claim the signature
is computed over an escaped-unicode rendering rather than raw bytes. This
implementation uses raw bytes and logs a diagnostic when a verification failure
coincides with non-ASCII bytes in the body.

## Error classification

`classify_error` splits Meta's numeric codes by retryability, which matters
because `NodeUserError` is on the framework's non-retryable list:

| Codes | Mapped to | Effect |
|---|---|---|
| `0`, `190` | `PermissionError` with `.provider` / `.reason` / `.auth` | Reconnect chip in the UI + `credential.*.runtime_failed` event |
| throttle / transient | `RuntimeError` | Retryable — preserves backoff |
| permission / policy / template / account | `NodeUserError` | One WARN line, no traceback |
| `131047` | `NodeUserError` | Bespoke message pointing at `send_template` — the 24-hour window has closed |

## Icons

Each node ships its own `icon_<nodeType>.svg` in the plugin folder: **one bold
purpose-built glyph** — paper plane, inbound arrow into a tray, double tick,
photo — painted in WhatsApp green `#25D366` with `#128C7E` for depth. The
brand comes through the **colour**, not the mark.

Canvas nodes render these at roughly 28px, which is the constraint that
decided the design. Three earlier attempts each failed at that size and are
worth recording, because all three look reasonable in the abstract:

- **One shared `icon.svg`.** A folder icon satisfies every node type in that
  folder, so Media, Receive and Status all rendered the same glyph and only
  Send — which happened to have a per-node file — looked different.
- **`lucide:` references in `meta.json`.** No artwork to vendor, but lucide
  glyphs are monochrome `currentColor` line art and rendered as washed-out
  grey. The reference is also a hard dependency on a third-party export name
  that can be renamed or removed, and the failure mode is a node that renders
  nothing rather than an error.
- **The real WhatsApp logo plus a corner badge.** Unmistakably branded, but
  the composition needs the logo scaled to ~80% to make room, and the badge
  glyph then lands at ~10px where a photo or paper plane is unreadable. Two
  competing shapes in one 28px box is one too many.

`whatsapp_business.svg` is separate and stays: it is the *credential* brand
mark and resolves through `Credential.get_icon_path`, a different chain.

`test_whatsapp_business.py::TestEachNodeShipsItsOwnBrandedIcon` asserts each
node type resolves to a distinct file, that every icon parses as XML and
embeds the authentic mark in brand green, and that no folder-level `icon.svg`
reappears to shadow them. See [plugin_system.md](./plugin_system.md) for the
resolution order.

## Gotchas worth knowing before editing

- **`operation` is load-bearing on Send.** `_pick_operation` reads it off the
  raw parameter dict *before* Pydantic validation, so the model default never
  applies at dispatch; raw-dict callers must pass it explicitly.
- **The kind selector is `media_type`, not `message_type`.**
  `ParameterRenderer.getFileAcceptType` hardcodes a switch on a sibling named
  `message_type` and would override the backend's declared `accept`.
- **Uploads post bytes, not a file handle** — `Connection.request` replays the
  same kwargs on its one auth retry, and a consumed handle replays empty.
- **Media travels as a reference, never bytes** — see
  [media_transport.md](./media_transport.md).
- **`shape_output` is overridden on both triggers** to return `event.data` flat,
  matching the deployed path. The base default dumps the whole CloudEvents
  envelope, which made `{{trigger.text}}` resolve when deployed and break on
  Run.

## Unverified

`cta_url` matches Meta's documented CTA payload shape, but the messages
reference schema does not enumerate it among `interactive.type` values. Not yet
confirmed against a live send.

## Related

- Per-node contracts: [`node-logic-flows/whatsapp_business/`](./node-logic-flows/whatsapp_business/)
- [Event Framework](./event_framework.md) · [Media Transport](./media_transport.md) · [Plugin System](./plugin_system.md)
- Meta docs: <https://developers.facebook.com/docs/whatsapp/cloud-api>
