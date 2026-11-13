---
name: treg-skill
description: Find and call paid data and generation tools through treg - search results, SEO data, social profiles and posts, people and company data, web pages, image, video and voice generation - by searching its catalog for the job, reading a tool's parameters and price, and calling it within the owner's price ceiling.
allowed-tools: "treg_action"
metadata:
  author: opencompany
  version: "1.0"
  category: web

---

# treg Skill

treg (treg.to) puts thousands of third-party tools behind one sign-in:
Google and social search results, SEO and keyword data, social profiles and
posts, people and company data, scraped web pages, and image, video and
voice generation. Each call is billed to the team's prepaid treg balance,
usually a fraction of a cent. The `treg_action` tool runs treg's own CLI
with the sign-in the owner set up under Credentials -> treg.

Three facts shape how you use it:

- **Calls cost money.** Search and describe are free; a call is billed.
  Find the right tool and parameters before calling, and never call again
  for data you already have.
- **The owner caps each call.** A call that would cost more than the
  ceiling on the node is refused before anything is charged.
- **Answers can be huge.** A large answer is saved to a file in your
  workspace and you get a preview; read the fields you need from the file.

## Tool: treg_action

| Operation | Purpose | Key fields | Billed |
|---|---|---|---|
| `search` | Find tools for a job, with prices | `query` (the job in plain words), `limit` | no |
| `describe` | One tool's parameters, where each goes, its price, an example request | `tool_id` | no |
| `call` | Run one tool | `tool_id`, `params` (the tool's parameters by name), `body` (rarely) | yes |
| `balance` | The team's balance in USD, and money held for running tasks | - | no |

## Find, describe, call

Never guess a tool id or a parameter name.

1. **Search by the job, not by a vendor:**

   ```json
   { "operation": "search", "query": "google search results for a keyword" }
   ```

   Each tool has an `id`, a `summary`, a `provider` and a `price`
   (`usd` per `type`: per call, per result or per success). `routed: true`
   means treg picks the provider for you and the price varies; `task: true`
   means it is a generation that finishes later. When `verdict` is `none`,
   the catalog has no tool for that job; say so instead of guessing.

2. **Describe the tool you intend to call**, unless you already did in this
   conversation:

   ```json
   { "operation": "describe", "tool_id": "serper.web.search" }
   ```

   `parameters` lists every parameter with `in` (path, query, body or
   header), `type`, `required`, allowed values (`enum`) and an `example`.
   `example_request` is a request treg verified, and `example_response`
   shows the shape of the answer.

3. **Call it**, passing the parameters by name in `params`:

   ```json
   { "operation": "call", "tool_id": "serper.web.search", "params": { "q": "matcha cafe brooklyn" } }
   ```

   The node puts each parameter where the tool takes it: in the query
   string, or in the body, nested for a dotted name such as
   `params.domain`. A tool whose example request has a list body gets your
   parameters as a list of one, so this works as written:

   ```json
   { "operation": "call", "tool_id": "dataforseo.google.keywords.volume", "params": { "keywords": ["matcha", "hojicha"], "location_code": 2840 } }
   ```

   Use `body` only when the tool wants a whole body that `params` cannot
   express, such as a list of several items: it is sent exactly as given.
   Do not give `body` and body parameters in `params` together: the call
   is refused.

## Money

- `cost_usd` on a call's answer is what treg charged. When it is missing,
  treg did not bill the call (the team connected its own key for that
  provider).
- The owner sets the most one call may cost on the node. You cannot change
  it: a value you pass is ignored. When a call would cost more, the answer
  says so and nothing is charged; choose a cheaper tool or ask for fewer
  results.
- When a call "did not come back", it may still have gone through and been
  charged. Run `balance` before calling it again, and do not repeat it
  unless the owner asks.
- When the balance is too low, tell the owner; only they can add funds.
- `balance` shows the team balance and the money held for running tasks:

  ```json
  { "operation": "balance" }
  ```

## Large answers

A small answer comes back whole in `data`. A large one is saved to the
workspace: `file.path` is where, `preview` shows its shape with real values
(only the first element of each list, long strings clipped) and `note`
says so. Read the fields you need from that file with a file, shell or
code tool. Do not call the tool again to see more of an answer you already
have. A binary answer (an image, an audio file) is saved as a file the same
way.

## Generation tasks

Video, image and some data tools run as tasks. Their call answers at once
with `reserved_usd` (money held until the task ends; a failed task is
refunded) and `task`:

- `task.check` is the call that reads the task's state. Run it with
  `operation: call`; checking is free.
- `task.status_path` is where the state is in that answer, `done_when`
  and `failed_when` list its final values, and `result_path` is where the
  result is once done. Wait `interval_seconds` between checks.
- Result links are the provider's and expire: save what the owner needs.

## When the owner asks first

While the owner asks to approve everything first, `call` is refused with a
message saying so; search, describe and balance still work. Tell the owner
which tool you would call, with its price, and why.

## Errors

| You see | What to do |
|---|---|
| `query must start with a word` | Rephrase the job without a leading dash |
| `... is not a treg tool id` | Use an `id` from `search` |
| `Did you mean: ...` | Use one of the ids it names |
| `names a job that several tools do` | `describe` it and pick one of its tools |
| `... answered HTTP 400 ...` | A parameter is wrong: `describe` the tool and fix the request; do not try another tool with the same mistake |
| `... answered HTTP 429 ...` or `HTTP 5xx` | The provider is busy or failing: try another tool for the same job and say which |
| `would cost about ..., more than this node's ceiling` | Choose a cheaper tool or ask for fewer results; nothing was charged |
| `The treg balance is too low` | Tell the owner to add funds |
| `HTTP 401` | Tell the owner to sign in again under Credentials -> treg |
| `did not come back` | Check `balance` before anything else; do not repeat the call |
| `Installing the treg CLI` | Try again in a minute |

## Responses

A call's answer, small enough to come back whole:

```json
{
  "operation": "call",
  "tool_id": "serper.web.search",
  "http_status": 200,
  "cost_usd": 0.001,
  "call_id": "7af9b91a05684e42940604eb2d575d88",
  "data": { "organic": [{ "title": "...", "link": "https://..." }] }
}
```
