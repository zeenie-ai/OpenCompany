---
name: stripe-skill
description: Process payments, manage customers, issue refunds, and handle subscriptions via the Stripe CLI. Pass any Stripe command (customers, charges, payment_intents, refunds, invoices, products, prices, subscriptions) and the tool runs it for you and returns parsed JSON.
allowed-tools: "stripe_action"
metadata:
  author: opencompany
  version: "1.0"
  category: payments

---

# Stripe Skill

Pass-through over the official [Stripe CLI](https://stripe.com/docs/cli).
The tool runs `stripe <command> --api-key <stored-key>` for you and
returns parsed JSON. Every Stripe resource the CLI supports works
without code changes — products and prices created tomorrow work the
same way.

## Tool: stripe_action

Single field: `command` — a string identical to what you would type
after `stripe ` on the terminal.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| command | string | Yes | Stripe CLI command (e.g. `"customers create --email a@b.com"`). Quote arguments that contain spaces. |

### Response

```json
{
  "command": "customers create --email a@b.com",
  "success": true,
  "result": {
    "id": "cus_NkXz123",
    "email": "a@b.com",
    "object": "customer",
    "created": 1730000000,
    ...
  },
  "stdout": "<raw CLI stdout>"
}
```

On CLI failure the tool raises an error with Stripe's own message
(e.g. `"No such customer: cus_invalid"`).

## Common commands

### Customers

| Command | Purpose |
|---|---|
| `customers create --email <addr>` | Create a customer. Optional: `--name`, `--description`, `--phone`, `--metadata key=val`. |
| `customers retrieve <cus_id>` | Fetch a customer by id. |
| `customers list --limit 10` | List recent customers. Optional: `--email <addr>` filter. |
| `customers update <cus_id> --email <new>` | Update fields on a customer. |
| `customers delete <cus_id>` | Permanently delete a customer. |

Search:

| Command | Purpose |
|---|---|
| `customers search --query "email:'a@b.com'"` | Server-side search across customers. Stripe Search query syntax. |

### PaymentIntents (recommended for charges)

| Command | Purpose |
|---|---|
| `payment_intents create --amount 2000 --currency usd` | Create a PaymentIntent for $20.00. Amount is in the smallest currency unit (cents for USD). |
| `payment_intents create --amount 2000 --currency usd --customer cus_123` | Create attached to a saved customer. |
| `payment_intents create --amount 2000 --currency usd --confirm --payment-method pm_card_visa` | Create + confirm in one shot (most common test path). |
| `payment_intents retrieve pi_123` | Fetch by id. |
| `payment_intents list --limit 20` | List recent intents. |
| `payment_intents cancel pi_123` | Cancel an unconfirmed or processing intent. |

### Charges (legacy direct-charge path)

| Command | Purpose |
|---|---|
| `charges retrieve ch_123` | Fetch a charge by id. |
| `charges list --limit 10` | List recent charges. |
| `charges list --customer cus_123` | List charges for a specific customer. |

For new integrations prefer **PaymentIntents** over direct
`charges create` — Stripe deprecated raw card-token charge creation.

### Refunds

| Command | Purpose |
|---|---|
| `refunds create --payment-intent pi_123` | Refund a PaymentIntent in full. |
| `refunds create --payment-intent pi_123 --amount 500` | Partial refund (in smallest currency unit). |
| `refunds create --charge ch_123` | Refund by charge id (older flow). |
| `refunds retrieve re_123` | Fetch a refund by id. |
| `refunds list --limit 10` | List recent refunds. |

### Invoices & Subscriptions

| Command | Purpose |
|---|---|
| `invoices create --customer cus_123` | Draft an invoice. |
| `invoices finalize_invoice in_123` | Finalize a draft (locks for sending/charging). |
| `invoices pay in_123` | Charge the customer for a finalized invoice. |
| `invoices list --customer cus_123` | List invoices for a customer. |
| `subscriptions create --customer cus_123 --items='[{"price":"price_xxx"}]'` | Create a subscription. |
| `subscriptions retrieve sub_123` | Fetch a subscription. |
| `subscriptions cancel sub_123` | Cancel immediately. |
| `subscriptions update sub_123 --cancel-at-period-end true` | Cancel at end of current billing period. |

### Products & Prices

| Command | Purpose |
|---|---|
| `products create --name "Pro Plan"` | Create a product. |
| `prices create --product prod_123 --unit-amount 2000 --currency usd --recurring='{"interval":"month"}'` | Create a recurring price tied to a product. |
| `prices list --product prod_123` | List prices for a product. |

### Trigger (synthetic test events)

| Command | Purpose |
|---|---|
| `trigger payment_intent.succeeded` | Fire a synthetic PaymentIntent succeeded event. |
| `trigger charge.refunded` | Fire a synthetic refund event. |
| `trigger customer.subscription.created` | Fire a synthetic subscription event. |

`stripe trigger` is invaluable for testing webhook flows — it sends
the event to Stripe, which sends it back through the same `stripe
listen` daemon and into your `stripeReceive` trigger node.

## Common workflows

### Create a customer and charge them

```json
{ "command": "customers create --email rosy@sparrow.com --name 'Rosy Sparrow'" }
```
returns `cus_…`. Then:
```json
{ "command": "payment_intents create --amount 2000 --currency usd --customer cus_xxx --payment-method pm_card_visa --confirm" }
```

### Refund the most recent payment for a customer

```json
{ "command": "payment_intents list --customer cus_123 --limit 1" }
```
read `data[0].id`, then:
```json
{ "command": "refunds create --payment-intent pi_xxx" }
```

### Set up a recurring subscription

```json
{ "command": "products create --name 'Pro Plan'" }     -> prod_xxx
{ "command": "prices create --product prod_xxx --unit-amount 2000 --currency usd --recurring='{\"interval\":\"month\"}'" }   -> price_xxx
{ "command": "subscriptions create --customer cus_xxx --items='[{\"price\":\"price_xxx\"}]'" }
```

## Quoting and escaping

The `command` string is parsed with `shlex.split` before being handed
to `stripe`. Use single quotes for arguments that contain spaces:

| You want to send | Type this in `command` |
|---|---|
| `--name=Acme Inc` | `customers create --name 'Acme Inc'` |
| metadata with spaces | `customers update cus_x --metadata 'plan=Acme Pro'` |
| a JSON array as a flag value | `subscriptions create --items='[{"price":"price_xxx"}]'` |

JSON inside `--items` / `--recurring` etc. should be a single quoted
JSON string with no internal spaces — the CLI parses it on the
server side.

## Idempotency

For any create/charge that you don't want to duplicate on retry, add
an idempotency key:

```
customers create --email a@b.com -H "Idempotency-Key: order_42_user_7"
```

The CLI accepts custom headers via `-H`. Stripe deduplicates by the
key for 24 hours.

## Test mode vs live mode

The stored API key (set in the Credentials Modal) determines mode:

| Key prefix | Mode | Effect |
|---|---|---|
| `sk_test_…` | Test | Fake money, fake events, no real charges. Always use this for development. |
| `sk_live_…` | Live | Real money. Real customers. Use only after thorough testing. |
| `rk_test_…` / `rk_live_…` | Restricted | Scoped permissions; recommended for production agents that don't need full account access. |

The CLI reports `livemode: true/false` on every response object so
you can verify which environment a result is from.

## Errors

Stripe returns structured errors that surface in the tool's error
field. Common ones:

| Error code | Meaning | Recovery |
|---|---|---|
| `resource_missing` | The id you passed doesn't exist | Verify the id; list to find the right one |
| `card_declined` | Card payment failed | Use a different `--payment-method`; in test mode try `pm_card_visa` |
| `invalid_request_error` | Wrong / missing argument | Check `stripe <resource> create --help` |
| `authentication_required` | 3DS challenge needed | Use a setup_intent or off-session payment_method |
| `rate_limit_error` | Too many requests | Pause and retry; Stripe rate limit is 100/sec in live, 25/sec in test |

The CLI's stderr always contains the Stripe request id (`req_…`) —
include it when reporting issues.

## Webhooks (`stripeReceive` trigger)

When the Stripe daemon is connected (Credentials Modal → Stripe →
Connect), every event Stripe sends is forwarded to OpenCompany and
delivered to any active `stripeReceive` trigger nodes after
HMAC-SHA256 signature verification (`Stripe-Signature` header).

To test event delivery without making real payments, use the
`trigger` command above. Events fire through the same path as real
ones.

Filters on the trigger node (`event_type_filter`):

| Filter | Matches |
|---|---|
| `all` | every event |
| `charge.succeeded` | exact match |
| `charge.*` | every charge.* event (succeeded, refunded, failed, …) |
| `payment_intent.*` | every PaymentIntent event |

The trigger output mirrors Stripe's event shape: `event_id`,
`event_type`, `created`, `livemode`, `api_version`, `request_id`,
`account`, `data`.

## Authentication — `stripe login` (browser OAuth)

Authentication is delegated entirely to the Stripe CLI. There is no
API key to paste into OpenCompany. The Credentials Modal's **Login
with Stripe** button drives the CLI's two-step machine-friendly
login:

1. **Browser-side**: the modal opens
   `https://dashboard.stripe.com/stripecli/auth/...` in a new tab and
   shows a verification code. The user signs in to Stripe and
   confirms the code.
2. **CLI-side**: the CLI polls Stripe until the user authorises,
   then writes credentials (one restricted key per mode, valid for
   90 days) to `~/.config/stripe/config.toml` (or
   `$XDG_CONFIG_HOME/stripe/config.toml`).
3. **OpenCompany-side**: when login completes, the listen daemon
   starts automatically and captures the webhook signing secret.

After login, every `stripe …` command run via this tool reads its
credentials from the CLI's config file — no `--api-key` flag in any
command you'd type.

**Logout** (Credentials Modal → Disconnect) stops the listen daemon
and runs `stripe logout --all` to clear the config file.

## Best practices

1. **Restricted keys are auto-issued.** When you `stripe login`, the
   CLI generates restricted keys with CLI-appropriate scopes (one
   per test/live mode) — you don't pick scopes manually.
2. **Default to test mode** for development. The CLI distinguishes
   modes; `livemode: true/false` is on every response.
3. **Add idempotency keys to all create operations.** Especially for
   charges and refunds — accidental retry without a key duplicates
   the charge.
4. **Quote string arguments with spaces** (`--name 'Acme Inc'`).
5. **Use PaymentIntents, not raw charges** for new integrations.
6. **Surface Stripe error messages verbatim to the user.** They are
   precise and actionable; don't paraphrase them.
7. **Don't include `--api-key` in your `command` string.** The CLI
   already has stored credentials; an inline `--api-key` overrides
   them and risks key leakage in process listings and logs.

## Setup checklist

1. **Stripe CLI binary** — auto-downloaded on first **Login with
   Stripe** click if not already on PATH. Resolution order:
   - System install (`brew install stripe/stripe-cli/stripe`,
     `scoop install stripe`, `apt install stripe`, or a direct
     binary from <https://stripe.com/docs/stripe-cli#install>).
   - OpenCompany package cache at
     `<DATA_DIR>/packages/stripe/bin/stripe[.exe]` (populated
     automatically from GitHub releases — pinned to a known-good
     CLI version).
2. Credentials Modal → Stripe → **Login with Stripe** → a browser
   tab opens to the Stripe Dashboard with a pairing code. Authorise.
   The modal flips to "Connected" when the CLI's `login --complete`
   subprocess returns and the listen daemon spins up
   (`webhook_secret_captured: true`).
3. The `Stripe` action node is connected to your agent's
   `input-tools` handle.
4. (For webhook flows) A `stripeReceive` trigger node is wired into
   your workflow with the event-type filter you care about.
