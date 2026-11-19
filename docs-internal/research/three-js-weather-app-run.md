# three.js weather app run: token cost and root causes (2026-11-17)

On 2026-11-17 the owner asked the AI Assistant employee to put a weather check on a website, then to rebuild the site as a three.js scene. The model calls for that work cost about $10 on OpenRouter. This note answers two questions:

- where those tokens went;
- why the product let a small website cost that much.

Sources: `ui.log` (the backend log of the last 21 model steps), the Temporal history of every run that day, the run and conversation tables in `workflow.db`, the site files, and the code at commit `493d8017`.

Five read-only investigations split the work: the cost ledger, what each request contained, what the agent did, the causes in the code, and what was asked versus built. Their figures agree, and every code reference below was checked again by hand. Nothing was changed while measuring. Measured and written up on 2026-11-17.

## Summary

- **Cost.** The site took 13 chat runs and cost **$10.74** at OpenRouter's list price for Claude Sonnet 5.5: 4,493,525 input tokens at $2 per million and 175,278 output tokens at $10 per million.
  - **$7.60:** the three.js run, the run `ui.log` covers. 64 model calls and 4 compaction calls in 22 minutes.
  - **$3.04:** the weather checks and the three earlier versions of the site.
  - **$0.10:** a first try of the three.js request that ended with a provider error.
  - **Nothing:** four calls refused for lack of credit.
- **Input is the bill.** Input tokens are 84% of the day's cost.
  - In the three.js run, 93% of the input to its 64 steps ($5.70) was content the run had already sent at least once.
  - No request used prompt caching.
- **What filled the requests in the three.js run:**
  - the agent's own file writes, which stay in the conversation as tool-call arguments: $2.40. One write of `app.js` was re-sent in 53 requests, for $1.54;
  - about 11,800 tokens of tool definitions and system prompt on every call: $1.51;
  - tool results: $1.10;
  - earlier reasoning sent back: $0.55.
- **What the steps were spent on.** The three site files were written by step 10. Steps 11 to 63 checked and debugged them.
  - 28 of those steps (33 to 60, **$3.14**) chased a colour problem. It existed only because headless Chrome rendered the scene in software at about 2 frames per second.
  - About 20 of the 64 steps were needed. The other 44 cost about **$4.60**.
- **Root causes in the product:**
  1. Prompt caching is never requested.
  2. Context pruning is driven by the stored transcript's size in bytes, not by tokens. The stored format keeps several copies of everything, so a byte is a poor proxy for a token. The 800,000-token threshold can never be reached.
  3. Pruning cannot remove the largest content. Clearing removes only tool results, and compaction keeps the latest turn word for word, which is often a whole-file write.
  4. Reasoning runs at the model's default effort and is billed again in full each time it is re-sent.
  5. Every call carries tools this employee never used, and schema keys meant only for the UI.
  6. Nothing caps a run's spend, and nothing records it. The app's own records show $0.19 of the $10.74.
  7. Broken or confusing tools (the browser tool, the process manager's working folder, Nushell) pushed the agent into building its own test harness.
- **What it should have cost:**
  - with prompt caching alone: about **$2.51** for the three.js run and **$3.70** for the day;
  - a lean run of the same build (write the files once, one check, one deploy): $1.47 without caching, $0.83 with it.
- **Fixes.** Ten, ranked by saving; none is built yet. Three of them were already open in the [treg reels run](treg-reels-run.md) note of 2026-11-13: prompt caching, recording agent token use, and trimming carried-over results.

## The job

| | |
|---|---|
| Employee | AI Assistant (workflow `2`, workspace `AI_Assistant_1`), generation 8, on `company dev` |
| Model | `anthropic/claude-sonnet-5.5` through OpenRouter: $2 per million input tokens, $10 per million output tokens, a 1,000,000-token context window |
| Tools | 11: write_todos, fs_search, shell_execute, file_read, file_modify, process_manager, browser, vercel, memory, treg_action, Skill |
| Context | A Context node, so every chat message continues one stored conversation |
| Settings | No thinking setting, no output limit, `max_iterations` 200, compaction ratio 0.8, tool results capped at 100,000 characters |

| # | UTC | Owner's message | Model calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---:|---:|---:|
| 1 | 10:19 | *check again* (weather) | 4 | 57,129 | 418 | $0.12 |
| 2 | 12:03 | *check sf weather again* | 6 | 95,404 | 489 | $0.20 |
| 3 | 12:09 | *check for bangalore* | 3 | 52,278 | 327 | $0.11 |
| 4 | 12:10 | *can you out that in a simple website and show it on a website* | 7 | 146,431 | 2,408 | $0.32 |
| 5 | 12:13 | *the ui is bland also add some other interesting info too* | 5 | 151,688 | 8,493 | $0.39 |
| 6 | 12:25 | *the ui components still have bad scroll ui, fix it* | 6 | 229,351 | 2,530 | $0.48 |
| 7 | 12:31 | *no static website , need a dynamic alive website* | 6 and 1 compaction; a 7th refused for credit | 248,873 | 31,220 | $0.81 |
| 8-10 | 12:43-12:52 | *continue*, and two retries | 1 each, all refused for credit | 0 | 0 | $0.00 |
| 11 | 12:53 | the retry that ran: checked version 3 and replied | 8 | 301,502 | 1,447 | $0.62 |
| 12 | 12:57 | *i want a 3js based high physics simulation based modern elegant ui* | 1, ended with a provider error | 39,605 | 1,885 | $0.10 |
| 13 | 12:59 | the same message again: the three.js run | 64 and 4 compactions | 3,171,264 | 126,061 | $7.60 |
| | | **Day** | | **4,493,525** | **175,278** | **$10.74** |

The owner works in IST (UTC+5:30), so the three.js run went from 18:29 to 18:50 local time. Each run starts with the stored conversation of the runs before it, so the first request grew from 13,475 tokens in run 1 to 39,633 in run 13.

## Where the money went

### Input and output

| | Input | Output | Reasoning, inside the output |
|---|---:|---:|---:|
| three.js run | $6.34 (83%) | $1.26 (17%) | 57,023 tokens, $0.57 |
| Day | $8.99 (84%) | $1.75 (16%) | 69,451 tokens, $0.69 |

Reasoning tokens are part of the output count, not billed on top of it: in every usage record, reasoning is at most the output and the total equals input plus output. No call was retried, so nothing was billed twice. The refused calls report no usage and cost nothing.

### What the three.js run's input paid for

Each request's billed input was split into its parts. The split reproduces every step's billed input to within 0.61%, and the total to within 0.03%.

| Content of the requests | Tokens | Cost | Share |
|---|---:|---:|---:|
| Arguments of the agent's own tool calls (file_modify writes alone: 1,031,105 tokens, $2.06) | 1,201,718 | $2.40 | 37.9% |
| Tool definitions, on every call | 710,406 | $1.42 | 22.4% |
| Tool results | 548,321 | $1.10 | 17.3% |
| Earlier reasoning, sent back | 273,161 | $0.55 | 8.6% |
| Compaction summaries | 196,455 | $0.39 | 6.2% |
| Input of the 4 compaction calls | 102,449 | $0.20 | 3.2% |
| Placeholders for cleared results | 63,740 | $0.13 | 2.0% |
| System prompt | 44,858 | $0.09 | 1.4% |
| History carried over from earlier chat runs | 27,704 | $0.06 | 0.9% |
| The owner's message, assistant text, rounding | 2,451 | $0.01 | 0.1% |
| **Total** | **3,171,264** | **$6.34** | |

Content sent for the first time cost $0.44. The other $5.70 of step input repeated what earlier requests in the same run had already sent.

### The most expensive messages

A message's cost is its tokens times the number of requests that carried it, plus its share of the compaction calls that re-read it.

| # | Message | Tokens per send | Sent in | Cost |
|---:|---|---:|---|---:|
| 1 | Step 10's write of `weather-site/app.js` (28,690 characters) | 14,294 | 53 requests (steps 11-63) | $1.54 |
| 2 | Step 4's write of `weather-site/scene.js` (44,949 characters) | 26,414 | 6 requests (steps 5-10) | $0.36 |
| 3 | The summary from compaction 10, with the request | 3,225 | 53 requests | $0.35 |
| 4 | Step 1's turn: 42,417 reasoning tokens, a to-do list, an npm install | 42,737 | 3 requests (steps 2-4) | $0.26 |
| 5 | A read of `scene.js` lines 505-704 (step 35) | 6,069 | 28 requests, 18 of them as a placeholder | $0.13 |
| 6 | Step 29's write of `_tmp/cdp.cjs`, a headless-Chrome driver | 1,577 | 34 requests | $0.11 |
| 7 | A read of `scene.js` lines 520-669 (step 47) | 4,454 | 16 requests | $0.10 |
| 8 | A read of the old `index.html` (step 7) | 14,821 | 3 requests | $0.09 |

The 20 most expensive messages cost $3.53, 56% of the input. The first four cost $2.51.

### What the output paid for

| Output | Tokens | Share |
|---|---:|---:|
| Reasoning | 55,501 | 44.0% |
| Site code (scene.js, index.html, app.js, five small edits) | 45,531 | 36.1% |
| Compaction summaries | 11,720 | 9.3% |
| Tool-call bookkeeping (shell, search, read, deploy, process and to-do arguments) | 8,996 | 7.1% |
| Helper and test scripts | 3,769 | 3.0% |
| Prose to the owner | 544 | 0.4% |

Reasoning and compaction counts are exact. The rest is split by fitted per-category rates, so it is an estimate.

Step 1 alone reasoned for 6 minutes and 42,417 tokens. Its visible reasoning summary shows the model drafting the code: "Still writing ..." opens 78 of its 170 paragraphs. The step ended with only a to-do list and an npm install, and steps 4, 9 and 10 then wrote the code into the files. That one step cost $0.42 as output and $0.26 more when it was re-sent in steps 2 to 4.

Scope growth was not the cost driver. The features the owner did not ask for are 13.9% of the code and cost about $0.06 to write. They are:
- a simulation lab panel;
- buoys and distant headlands;
- pointer effects;
- bloom post-processing.

### Request size, step by step

The average request carried 47,950 tokens. What set its size changed with each compaction and clearing:

| Steps | Requests | Input per request | Input cost | What set the size |
|---|---:|---|---:|---|
| 1 | 1 | 39,633 | $0.08 | history carried from earlier runs, plus the fixed prefix |
| 2-4 | 3 | 57,593 to 59,601 | $0.35 | step 1's 42,417 reasoning tokens, kept by compaction 1 |
| 5-10 | 6 | 41,240 to 69,773 | $0.65 | the scene.js write, kept by compaction 4 |
| 11-45 | 35 | 29,366 to 65,812 | $3.16 | the app.js write, kept by compaction 10, plus tool results growing about 1,000 tokens a step |
| 46-58 | 13 | 44,693 to 56,642 | $1.36 | after clearing 39 old results |
| 59-63 | 5 | 49,678 to 52,863 | $0.51 | after two more clearings |
| 64 | 1 | 14,377 | $0.03 | after compaction 63 |

From step 11 on, no request could be smaller than about 29,300 tokens. That floor is the fixed prefix (11,800), the compaction 10 summary (3,200) and the app.js write (14,300). It was paid on every one of the 53 checking steps before they added anything of their own.

## What the agent did

### Phases

| Phase | Steps | IST | What happened | Cost | Share |
|---|---|---|---|---:|---:|
| Plan, copy three.js into the site | 1-3 | 18:29-18:35 | A 6-minute plan; `npm install three@0.160.0`, then a script to copy it into `weather-site/vendor` | $0.83 | 10.8% |
| Write scene.js | 4 | 18:35 | One write of 44,037 characters | $0.42 | 5.5% |
| Read the old site | 5-7 | 18:38 | A review edit, a repeated grep, a read of the old page's data code | $0.29 | 3.8% |
| Write index.html and app.js | 8-10 | 18:38-18:39 | One edit, two whole-file writes | $0.74 | 9.8% |
| Syntax and markup checks | 11-13 | 18:41 | One failed check on a missing file, greps of its own new page | $0.19 | 2.5% |
| Get a local server to answer | 14-25 | 18:41-18:42 | Three servers, three rounds of 14 x 404, then its own static server script | $0.90 | 11.8% |
| Build its own headless-Chrome tools | 26-32 | 18:42-18:43 | The browser tool failed, so it wrote a screenshot script, a DevTools-protocol driver and a PNG decoder | $0.65 | 8.6% |
| Night-sky debugging loop | 33-60 | 18:43-18:49 | 28 steps chasing a sky that looked like day at night | $3.14 | 41.3% |
| Deploy and check the live site | 61-63 | 18:49-18:50 | Stop the server, deploy to Vercel, fetch 6 live files (all 200) | $0.32 | 4.2% |
| Compaction and answer | 64 | 18:50 | Compaction 63, then the final reply | $0.12 | 1.6% |

Each phase includes the compaction call that followed it. The phases sum to $7.60. Every step is listed in the appendix.

### The checking phase in detail

**Local server, steps 14 to 25 ($0.90).**
- The process manager refused `cwd: weather-site`. It resolved the relative path against the backend's own folder ("got '<repository>\server\weather-site'").
- The agent then started `http.server --directory weather-site` without a working folder. The process ran in the process manager's per-node folder, `2_processManager_1`, so every request returned 404. That happened three times: 14 files, 14 x 404 each.
- `where msedge chrome` failed because the shell is Nushell.
- The agent wrote `_tmp/serve.cjs`, its own static server with a hard-coded root, which finally returned 14 x 200.

**Browser and screenshots, steps 26 to 32 ($0.65).**
- The browser tool failed twice with "Chrome exited while starting. Another Chrome may have this profile open." It had failed the same way twice in earlier runs that day.
- The agent wrote a screenshot script, then `cdp.cjs`, a DevTools-protocol driver. The first clean headless load came at step 30: one favicon 404, live weather shown.
- The model cannot look at images, so it wrote `png.cjs`. That script prints a grid of average colours from a screenshot.

**Night-sky loop, steps 33 to 60 ($3.14).** The colour grid suggested a daytime sky at 5 AM in San Francisco. The agent then:
- ran 10 greps and 4 reads of its own `scene.js`;
- ran the headless driver 6 more times, each run taking 27 to 98 seconds;
- decoded 6 more screenshots;
- added a debug hook, `window.__atmos.live`, to `app.js` at step 42. It shipped to production.

The cause was mostly its own frame-time clamp. The clamp hid that headless software rendering ran at about 2 frames per second, so the day-to-night fade took about ten times longer than intended. Steps 51 and 52 fixed it so adaptive quality reacts to slow frames. That is a real but small fix for slow devices. The agent then concluded "on real GPUs this should be fine anyway". It left the storm look as it was: "I'd be changing it blind since I can't verify the render".

### Waste

An item's cost is its own requests, plus what its output cost when later requests carried it again. The items overlap, so their union is given last.

| Item | Steps | Cost | Share of run |
|---|---|---:|---:|
| Night-sky debugging loop | 33-60 | $3.21 | 42.2% |
| ... of which headless driver runs | 30, 36, 40, 43, 49, 53, 58 | $0.93 | 12.2% |
| ... of which PNG decodes | 32, 37, 41, 54, 59, 60 | $0.72 | 9.4% |
| app.js riding along in later requests (structural) | from step 10 | $1.52 | 20.0% |
| Local-server loop (wrong folder, 404s) | 14-23 | $1.21 | 15.9% |
| Own headless tooling | 27-32 | $0.84 | 11.1% |
| Steps where every call failed | 11, 14, 16, 20, 26, 33, 55 | $0.81 | 10.6% |
| Step 1's reasoning: as output and 3 re-sends | 1 | $0.68 | 8.9% |
| Repeated lookups after clearing (whole steps) | 34, 46, 47 | $0.41 | 5.4% |
| Greps of its own new page after compaction 10 | 11-12 | $0.32 | 4.2% |
| **The 44 avoidable steps together** | | **$4.60** | **60.5%** |

**Needed steps.** About 20 of the 64 were needed, on a generous count: steps 1-5, 7-10, 13, 24-25, 29-30, 51-52 and 61-64. That covers:
- the plan;
- the three writes and two self-review edits;
- one read of the old site;
- one syntax check;
- one working server;
- one real render check (the browser tool was broken, so a small driver was defensible);
- the frame-time fix;
- the deploy, the live check and the answer.

Those steps cost $2.95 as billed. A run of only those steps would have cost about $3.00 without caching. That estimate is first-order: it keeps clearing and compaction timing as they were.

### What ui.log shows directly

`ui.log` holds 506 lines from 18:45:45 to 18:58:32 IST: model steps 45 to 64, the tool calls between them, and the chat reply. In the log, "iteration N" comes just before the request `llm-step-(N+1)`.

- **Clearing**, at iterations 45, 58, 61 and 63:
  - line 33 cleared 39 earlier tool results: 394,686 to 309,520 bytes, against a budget of 393,216;
  - lines 321, 411 and 453 cleared 10, 4 and 3 results. Each time the transcript ended just under or just over the budget again.
- **Compaction**, at iteration 63 (lines 455-467):
  - the log reads "next request about 51145 tokens (threshold 800000), transcript 393669 bytes (budget 393216)", so it fired 453 bytes over a byte budget while the request was 6% of the token threshold;
  - it summarized 125 messages into 4;
  - the summarizer used 33,352 input and 2,337 output tokens;
  - the final answer followed at step 64.
- **Compaction sizes go unrecorded.** Line 461 is the compaction service's "Session 2:execution:8: 0 -> 0 tokens": it records no sizes for agents on Temporal.
- **Rollovers.** Lines 76, 224 and 413 are "Blob data size exceeds the warning limit" for three of the run's 8 continue-as-new rollovers, at 913,790, 962,407 and 974,443 bytes.
  - The whole transcript rides in every model step's input and in each rollover.
  - The 9 agent runs wrote 45 MB of Temporal history for one 22-minute chat run.
  - The rollovers themselves cost no tokens.
- **History reads.** Lines 306-312 and 502-505 are "GetHistory page size is larger than threshold": history reads by a client, which cost no tokens.
- **Event-loop stalls.** Lines 123, 195, 233 and 314 report "The event loop is blocked", with lags of 0.31 to 0.92 seconds, during the long headless-Chrome runs.
- **A display quirk.** The console shows the node id `2:shell:1` with an emoji in place of `:shell:`, because it reads `:shell:` as an emoji shortcode. This is cosmetic.

## Root causes

### 1. Prompt caching is never requested

**What happens.** Each request pays $2 per million for its whole prompt. Anthropic models can read an unchanged prompt prefix from cache at $0.10 per million, but only when the request asks for it.

**Evidence.**
- `cache_read_tokens` and `cache_creation_tokens` are 0 in all 115 usage records of the day.
- In the three.js run, 93% of the step input repeated the previous request.

**Code.** No `cache_control` appears anywhere in `server/` outside the virtual environments. The request path for an agent step is:
1. `_execute_native_llm_step` in `server/services/temporal/agent_activities.py`;
2. `ChatUnifier.chat` (`server/services/llm/unifier.py`);
3. `OpenRouterProvider.chat` (`server/services/llm/providers/openrouter.py`);
4. `OpenAIProvider.chat`, whose request holds only the model, messages, tools, temperature and an optional `max_tokens` (`server/services/llm/providers/openai.py:222-264`).

The native Anthropic provider sets no breakpoint either.

**What OpenRouter requires** (its prompt-caching documentation, read on 2026-11-17):
- Anthropic models need `cache_control`: either one top-level field, which caches automatically and moves the breakpoint forward as the conversation grows, or up to four breakpoints on content blocks.
- The cache lives 5 minutes by default, or an hour with `"ttl": "1h"`.
- A write costs 1.25 times the input price for 5 minutes, or 2 times for an hour. A read costs $0.10 per million for Sonnet 5.5.

**Two things to fix before, or with, caching:**
- **The tool definitions are not byte-stable.** The day saw three versions, changing at 12:03 and 12:31 UTC, each at the start of a chat run. Each change reordered the same two lists inside the browser tool's schema (`ref.displayOptions.show.operation` and `selector.displayOptions.show.operation`), which looks like a set iterated in a new process. Tool definitions come first in the prompt, so any change misses every cache entry after it.
- **The usage mapping is incomplete** (`server/services/llm/providers/openai.py:651-658`):
  - it reads cache reads (`prompt_tokens_details.cached_tokens`);
  - it never reads cache writes (`cache_write_tokens`);
  - it drops OpenRouter's own charged amount (`usage.cost`).

**Cost.** About $5.09 of the three.js run's $7.60, and about $7.04 of the day's $10.74 (5-minute cache).

### 2. Pruning is driven by stored bytes, not tokens

**The token limit never fires.** The compaction threshold is the compaction ratio times the model's context window (`server/services/compaction.py:114-117`), read when the agent starts (`server/services/temporal/agent_activities.py:1709-1721`). For a 1,000,000-token model that is 800,000 tokens. The largest request in the run was 69,773.

**The limit that does fire** is a byte budget on the stored transcript:
- it is 393,216 bytes, three quarters of Temporal's 512 KiB payload warning (`server/services/temporal/agent_context_pressure.py:59-70`);
- it is measured as the JSON length of the stored messages (`agent_context_pressure.py:73-75`);
- it exists to keep each activity's payload under Temporal's limits, not to control cost.

**The stored format repeats everything** (`message_to_wire` in `server/services/llm/protocol.py`):
- every text is stored twice: in `content` and in `blocks`;
- every tool call is stored four times: `args` and `raw_arguments`, in `tool_calls` and again in `blocks[].tool_call`. For the app.js write, the 27,908 characters of file content appear four times, making one 119,225-character message;
- every reasoning summary is stored three times, plus a signature. Step 1's signature alone is 124,739 characters.

**At step 62**, 410,600 bytes of input held 50,354 tokens. The bytes broke down as:
- text the provider actually reads: 20%;
- duplicate copies: 50%;
- reasoning signatures: 16%;
- keys and nulls: 13%;
- JSON escaping: 1%.

**Consequences.**
- The 393,216-byte budget means anywhere from 39,000 to 68,000 real transcript tokens, depending on what the transcript holds (5.8 to 10.1 bytes per token).
- Compaction 1 fired straight after step 1. Step 1's single message, carrying 42,417 reasoning tokens, was 244,349 bytes: 62% of the budget on its own.
- Compaction 63 fired 453 bytes over budget.
- The projection of the next request's size also assumes 4 characters per token (`CHARS_PER_TOKEN` in `agent_context_pressure.py`). Tool output measured 1.6 to 2.2 characters per token, so the projection undercounts its growth about 2.3 times. This had no effect here, because the token threshold was far away.

### 3. Pruning cannot reach the biggest content

**Clearing only touches tool results.** It replaces earlier turns' results with a placeholder, down to half the budget (196,608 bytes) (`clear_old_tool_results`, `server/services/temporal/agent_context_pressure.py:90`). Assistant messages are never touched, and that is where whole-file writes and reasoning live.

**Compaction keeps the latest turn.** It summarizes the turns before the latest one and keeps the latest turn word for word (`server/services/temporal/agent_workflow.py:2993-2994`). `ui.log` line 455 says "keeping this turn's 2".

**What that did in this run:**
- Compactions 1, 4 and 10 each fired right after the largest turn of their stretch, and each kept that turn: step 1's reasoning for 3 more requests, the scene.js write for 6, and the app.js write for 53. Carrying file writes as tool-call arguments cost $2.40 in all.
- After the clearing at iteration 45, 309,520 bytes remained, 83% of them assistant messages. Half the budget could not be reached, so the rule cleared a few results again at 58, 61 and 63. Each of those clears changes the prompt, which would also defeat a prompt cache. The clearing function's own comment says it clears down to half "because every clear changes the prompt the provider has cached", but nothing is cached.
- Clearing runs before summarizing. Compaction 63's summarizer therefore saw placeholders for 56 of 70 results, while 68% of the summarizers' input was whole files that were already on disk.
- The placeholder text says "Call the tool again if you still need it". Steps 46 to 48 did exactly that, re-reading code the clearing had just removed.
- Compaction 63 cost $0.090 and saved about $0.073 on the final step: a net loss of $0.017, one step before the answer.
- Compaction 10's summary hit the summarizer's 4,096-token output limit (`server/services/compaction.py:360`), so it may have been cut short.

### 4. Reasoning runs at the default effort and is replayed in full

**Nothing sets reasoning for OpenRouter.**
- A thinking setting is sent only when the agent node enables thinking, and this one does not.
- Even when it is enabled, the openrouter block of `server/config/llm_defaults.json` has no `thinking_type`, so `_model_policy` treats it as "none" and sends nothing (`server/services/llm/providers/openai.py:238-259`).
- The owner's chat effort choice reaches only models listed in a provider's `effort_models`, and the openrouter block lists none.

**So Sonnet 5.5 used its default adaptive thinking.** Anthropic's documentation gives the default effort as `high`.

**Effect in this run:**
- 57,023 reasoning tokens, 45% of the output. Step 1 alone produced 42,417 of them, in 6 minutes.
- Re-sent reasoning is billed at its full original count, not at the size of the summary text that comes back. The fitted coefficient is 1.01. That added 273,161 input tokens ($0.55).
- Reasoning from before the newest owner message is not billed.
- A 6-minute step would also let a 5-minute cache entry expire.

**No output limit either.** Because of `omit_default_max_tokens`, no `max_tokens` is sent to OpenRouter. One step could emit up to the model's 128,000-token limit, about $1.28.

### 5. A large fixed prefix on every call

- **Size.** About 11,800 tokens per call: the 11 tool definitions (27.6 KB) and a 2.3 KB system prompt. That is $1.51 in the three.js run and about $2.4 to $2.6 over the day.
- **Largest definitions** (estimated tokens): browser 3,200, Skill 1,560, vercel 1,520, memory 1,340, treg_action 1,200.
- **Unused tools.** Skill, memory and treg_action were never called all day. Together they are about 4,100 tokens per call, $0.52 in the three.js run.
- **UI-only keys.** `displayOptions`, `placeholder` and generated `title` fields reach every provider except Gemini (`server/services/llm/schema.py:118-123`). They are 22% of the tool bytes, $0.22 to $0.36 in the run. These are also the keys whose order changes between processes (root cause 1).
- **Skill catalogue.** It lists skills whose tools are not connected; for example, `code-mode-skill` names the Python Executor.
- **Origin.** The tool set comes from the shipped seed `.opencompany/workflows/AI_Assistant.json`.

### 6. No spend limit and no record of spend

**Limits.**
- The only limit on a run is `max_iterations` (200).
- There is no token or dollar cap per run, and no output limit for OpenRouter (root cause 4). At this run's $0.10 to $0.20 per step, 200 steps would cost $20 to $40.

**Records.**
- **No usage rows for agents on Temporal.** The agent's model step returns its usage but writes no row. The token_usage_metrics table is written through `CompactionService.track`, which the Temporal path never calls. On 2026-11-17 the table holds 6 rows (5 compaction calls and 1 hire-screen call), worth $0.19.
- **Those rows are priced at half the real rate.** The openrouter block of `server/config/pricing.json` has only a `_default` of $1 and $5 per million, so every OpenRouter model is recorded at that price.
- **The cache arithmetic in `server/services/pricing.py` is wrong**, which will matter once caching is on:
  - line 215 prices a cache write at 1.25 times the output rate instead of the input rate, five times too high for this model;
  - cache reads are added on top of `input_tokens`, which on this path already includes them.
- **Nobody else sees it either.** The agent is told nothing about cost, and Settings > Billing shows only a count of successful runs.

### 7. Tool problems that turned checking into a loop

| Problem | Steps | Cost | Cause |
|---|---|---:|---|
| The process manager refused a relative `cwd`, then served from the wrong folder (3 x 14 x 404) | 14-23 | $1.21 | Explained below |
| The browser tool could not start Chrome: "Another Chrome may have this profile open". It also failed twice in earlier runs | 16, 26 | $0.19, and the own-harness detour ($0.84) | Not traced |
| The shell is Nushell. `where msedge chrome`, and a `node -e` with nested quotes, failed | 20, 55 | $0.29 | The description names Nushell (`server/nodes/filesystem/shell/__init__.py`), but the model still wrote cmd and bash forms |
| A grep with `\|` alternation returned 0 matches | 11, 33 | $0.25 | `fs_search` grep does not read `\|` as alternation; the model's habit from GNU grep |
| The model cannot see screenshots | 31-60 | inside the $3.14 loop | It decoded PNGs into colour grids instead. No rule tells it to stop when a visual check cannot be verified |

**Why the process manager failed.** The fix already exists, but only for newer agent nodes.
- With `runtime_behavior_version` 1, a relative `cwd` resolves against the workspace (`server/services/runtime_tool_context.py:67-70`).
- That version is set as a creation parameter, so only agent nodes created after it existed carry it (`server/services/plugin/base.py:609`). This employee's agent node has no such parameter in generation 8's snapshot.
- So its tool calls run the old rule: the relative path reaches `Path(cwd).resolve()` in the process service, which resolves it against the backend's folder (`server/services/process_service.py:169`).
- Without a `cwd`, a process runs in the node's own subfolder of the workspace.

## What it would have cost

| Scenario | three.js run | Day |
|---|---:|---:|
| As billed | $7.60 | $10.74 |
| Prompt caching, 5-minute cache | $2.51 | $3.70 |
| Prompt caching, 1-hour cache | $2.92 | $4.10 |
| Prompt caching, 1 hour on tools and system prompt, 5 minutes on the rest | $2.48 | $3.64 |
| Each chat run starting without the earlier runs' turns | $7.44 | $9.37 |
| Both: caching and no carried history | $2.39 | $3.21 |
| Only the ~20 needed steps, no caching | about $3.00 | |
| Lean run (write the files once, one check, one deploy), no caching | $1.47 | about $4.61 |
| Lean run with a 5-minute cache | $0.83 | about $3.97 |

**Assumptions.**
- **The caching rows** replay all 119 recorded calls. Each request reads the previous request from cache when it only appends to it and starts within the cache's lifetime. They count a miss at each of the run's 4 compactions and 3 clearings, and at each change of the tool definitions.
- **The lean rows** use token counts measured in this run. Across eight variants (history carried or not, lean or actual planning, 4 or 6 steps) the lean run costs $0.75 to $2.16.

**Carried history** cost the three.js run little, about $0.06 to $0.16, because compaction 1 replaced it after step 1. Over the day it cost about $1.27.

**What would remain with caching on** ($2.48):

| Part | Cost |
|---|---:|
| Output (visible and reasoning) | $1.15 |
| Rewrites after each compaction or clearing | $0.42 |
| The compaction calls | $0.32 |
| Cache reads | $0.28 |
| Cache writes of new content | $0.21 |
| One expiry, after step 1's 6-minute think | $0.12 |

So once caching is on, the next levers are reasoning and the compactions and clearings that keep rewriting the prompt.

## Fixes

Savings are for the three.js run unless a row says otherwise. "Alone" means applied to today's code; "after caching" means applied on top of fix 1. None of these is built yet.

| # | Fix | Saving | Effort | Notes |
|---|---|---|---|---|
| 1 | **Prompt caching** for Claude models through OpenRouter and the native Anthropic API: a top-level `cache_control` plus a breakpoint after the system prompt; read `cache_write_tokens` into usage | $7.60 to about $2.5 (-67%); day $10.74 to about $3.7 | Small | Make the tool definitions byte-stable first (sort or strip the browser schema's `displayOptions` lists). Runs inside the model activity, so Temporal replay is unaffected. Check one live request pair for `cached_tokens` above 0 |
| 2 | **Record and show the cost:** a usage row per agent step on Temporal with OpenRouter's `usage.cost`; real OpenRouter prices in `server/config/pricing.json`; correct cache arithmetic in `server/services/pricing.py`; the cost of each chat run on the run or in the chat | None directly. The owner currently sees $0.19 of $10.74 | Small | Activity-side, replay-safe |
| 3 | **Guard rails:** a per-run token or dollar cap that ends the run with a message; a generous `max_tokens` (32,000 to 64,000) for agent steps; an instruction to stop and report when a check cannot be verified or keeps failing | Up to the $3.14 loop, with a cap or rule set accordingly | Small to medium | A cap that stops the loop is a workflow decision: gate it with a value recorded at prepare time, as `context_pressure_version` is |
| 4 | **Prune by tokens on what is sent,** not stored bytes. At a compaction, replace older whole-file write contents with a stub (path and size; the file is on disk). Summarize once instead of clearing a few results every few turns | About $1.8 alone; $0.1 to $0.4 after caching | Medium | Needs `context_pressure_version` 2. Editing a past assistant turn invalidates later reasoning blocks on Sonnet 5.5, so make the edit only at a compaction, which rewrites the prompt anyway |
| 5 | **Reasoning effort for agent steps,** for example `medium`, which Anthropic suggests for agentic coding; let the owner's Quick and Thorough choice reach OpenRouter models | $0.33 to $0.67 alone, assuming 30 to 60% less reasoning (not measured); $0.20 to $0.45 after caching | Small | The owner's decision. Check how OpenRouter maps effort for Sonnet 5.5 before relying on it |
| 6 | **Trim the fixed prefix:** strip UI-only schema keys for every provider (this also fixes the reordering in fix 1); list only skills whose tools are connected; detach memory, treg and Skill from this employee if the owner agrees | About $0.76 alone; under $0.05 after caching | Small | Gemini already gets the stripped schema |
| 7 | **Fix the tools that caused detours:** the browser profile lock; relative `cwd` for agent nodes without `runtime_behavior_version` 1 (or upgrade old nodes); document the fs_search grep pattern syntax | The $1.2 to $2.0 of detours in this run | Medium | The browser failure is not traced yet |
| 8 | **Skip a compaction that cannot pay for itself** (one step before an answer), or run the summarizer at low effort or on a cheaper model | $0.02 to $0.25 | Small | The summary is the agent's only memory of the summarized turns, so quality matters |
| 9 | **Trim earlier runs' tool results** when a new chat run starts | About $1.29 a day alone; about nothing after caching | Small to medium | Needs a recorded version; breaks a cached prefix once per run |
| 10 | **Clean up this workspace:** remove the debug hook from `weather-site/app.js` (line 377); delete `_tmp/` (216 MB: two Chrome profiles of 180 MB, 31.6 MB of node_modules, 10 screenshots) | None | Small | Owner's files; not changed by this analysis |

If caching cannot ship first, the order by saving is 4, 9, 6, then 5. Fixes 2 and 3 are needed either way.

Stacked estimate for the three.js run: caching takes it to about $2.5, effort to about $2.1, and fixes 4 and 5 to about $1.6 to $1.7. Most of what remains is the output that writing a three.js app needs.

## Data and method

Every source was only read. No request was sent to OpenRouter or Vercel for this analysis, and the site was not run.

| Source | What it gave |
|---|---|
| `ui.log` at the repository root (ignored by git) | the backend log of model steps 45 to 64, the clearings, compaction 63 and the rollovers |
| The Temporal history in `.opencompany/temporal.db` | 34 runs, 804 activities, 114 model steps and 5 compaction calls, with each step's full request and response and its usage |
| `chat_messages` and `chat_runs` in `.opencompany/workflow.db` | the owner's messages, the replies and the run times |
| `workflow_control_executions` in `.opencompany/workflow.db` | generation 8's graph snapshot: the agent's saved parameters and the tool set |
| `token_usage_metrics` in `.opencompany/workflow.db` | what the app recorded: 6 rows, $0.19 |
| `.opencompany/workspaces/AI_Assistant_1/` | the site files, the helper scripts and the 216 MB `_tmp` folder |
| The code at commit `493d8017` | every code reference above |

**Method.**
- **The ledger is exact.** Each model step's usage comes from its decoded activity result.
  - Checks found no step counted twice, no retries, and reasoning always inside output.
  - The ledger uses OpenRouter's list price.
- **The real charge may be up to 10% higher.** Five of the model's ten OpenRouter endpoints charge 10% more, and nothing recorded which endpoint served each call. So the day cost $10.74 to $11.81. OpenRouter's Activity page is the only record of the exact charge.
- **The request split is a measured attribution.** Re-sent messages are priced at the billed tokens of the step that produced them, and tool results at the growth of the next request. The fixed prefix was measured on the requests right after a compaction; regression fits put it lower, at 10,600 to 10,700 tokens.
- **The waste figures are first-order.** They hold clearing and compaction timing fixed.
- **The caching and lean-run figures are simulations** from the recorded steps, priced at OpenRouter's rates for this model: reads $0.10, 5-minute writes $2.50 and 1-hour writes $4.00 per million.

**How to repeat it.**
1. Open `.opencompany/temporal.db` with SQLite in read-only mode (`mode=ro`).
2. List each agent run in `executions_visibility`.
3. Read its rows in `history_node`, where `tree_id` is the run id's 16 bytes, and keep the latest `txn_id` per `node_id`.
4. Decode each row with the temporalio `History` message. Payloads are `json/plain`.
5. For each `agent.execute_llm_step` event, `input[0]` holds `system_message`, `tools` and `messages`, and `result[0].usage` holds the token counts.

The five investigation reports and their scripts are in a temporary folder of the session that wrote this note, and may be gone. The Temporal history may also be removed later, as happened to the 2026-11-13 run's history.

Related: [Agent Context Flow](../agent_context_flow.md) describes the clearing and compaction rules, and [treg reels run](treg-reels-run.md) is the earlier analysis of the same employee.

## Appendix: all 64 steps

"In" and "Out" are billed tokens; Out includes reasoning, shown in brackets. Results give the characters returned and their status. Cost is In x $2/M plus Out x $10/M. Rows marked C are compaction calls. Times are IST.

| Step | IST | Calls | Results (chars, status) | In | Out (reasoning) | Cost |
|---:|---|---|---|---:|---:|---:|
| 1 | 18:29:03 | write_todos: 5-item plan; shell: npm install three@0.160.0 into _tmp | 749: ok; 151: ok | 39,633 | 42,775 (42,417) | $0.507 |
| C1 | 18:35:15 | compaction after step 1 | summary | 27,541 | 2,447 (0) | $0.080 |
| 2 | 18:35:33 | file_modify: write copy.cjs (copies three and addons into weather-site/vendor) | 48: ok | 57,593 | 503 (0) | $0.120 |
| 3 | 18:35:38 | shell: node copy.cjs; fs_search: grep "from '" in vendor/addons | 82: ok; 2,599: 19 matches | 58,124 | 157 (0) | $0.118 |
| 4 | 18:35:42 | file_modify: write weather-site/scene.js (44,037 chars) | 61: ok | 59,601 | 26,452 (1,836) | $0.384 |
| C4 | 18:38:03 | compaction after step 4 | summary | 4,748 | 2,840 (0) | $0.038 |
| 5 | 18:38:21 | file_modify: edit scene.js, widen the water plane; fs_search: the same vendor grep as step 3; shell: length of the old index.html | 78: ok; 2,599: 19 matches; 174: ok | 41,240 | 2,135 (1,728) | $0.104 |
| 6 | 18:38:41 | fs_search: grep "<script" in the old index.html | 154: 1 match | 44,792 | 211 (108) | $0.092 |
| 7 | 18:38:44 | file_read: old index.html lines 60-389 | 26,286: ok | 45,075 | 100 (0) | $0.091 |
| 8 | 18:38:46 | file_modify: edit scene.js, sun position | 78: ok | 59,958 | 3,978 (3,418) | $0.160 |
| 9 | 18:39:22 | file_modify: write index.html (11,998 chars) | 63: ok | 63,977 | 5,766 (83) | $0.186 |
| 10 | 18:39:55 | file_modify: write app.js (27,908 chars) | 59: ok | 69,773 | 14,332 (34) | $0.283 |
| C10 | 18:41:04 | compaction after step 10 | summary | 36,808 | 4,096 (1,011) | $0.115 |
| 11 | 18:41:32 | shell: node --check app.mjs (file not there); fs_search: grep with "\|" alternation in index.html | 551: exit 1, module not found; 143: 0 matches | 29,366 | 270 (58) | $0.061 |
| 12 | 18:41:36 | shell: copy app.js and scene.js to _tmp/*.mjs; fs_search x3: greps in index.html | 212: ok; 816, 223, 1,029: matches | 30,062 | 467 (48) | $0.065 |
| 13 | 18:41:40 | shell x2: node --check _tmp/app.mjs and _tmp/scene.mjs; fs_search: grep id="dot | 101: ok; 103: ok; 248: 1 match | 31,686 | 221 (0) | $0.066 |
| 14 | 18:41:43 | process_manager: start python http.server 8123, cwd weather-site | 517: error, cwd outside workspace | 32,206 | 187 (36) | $0.066 |
| 15 | 18:41:45 | process_manager: start http.server 8123 --directory weather-site | 538: ok | 32,619 | 132 (0) | $0.067 |
| 16 | 18:41:47 | browser: navigate localhost:8123 | 310: error, Chrome exited | 33,001 | 119 (37) | $0.067 |
| 17 | 18:41:50 | shell: node fetch check of 14 paths on :8123; fs_search: glob chrome.exe | 1,538: 14 x 404; 104: 0 matches | 33,249 | 518 (93) | $0.072 |
| 18 | 18:41:55 | process_manager: stop the server; shell: print cwd | 561: ok; 181: ok | 34,612 | 194 (62) | $0.071 |
| 19 | 18:41:57 | process_manager: start http.server 8124 with an absolute --directory | 618: ok | 35,216 | 168 (0) | $0.072 |
| 20 | 18:41:59 | shell: fetch check on :8124; shell: where msedge chrome | 1,538: 14 x 404; 471: exit 1, Nushell parse error | 35,678 | 416 (52) | $0.076 |
| 21 | 18:42:03 | shell: list the workspace; fs_search: ls . | 373: ok; 1,205: 12 entries | 37,134 | 229 (60) | $0.077 |
| 22 | 18:42:06 | shell: fetch check on 127.0.0.1:8124 | 1,538: 14 x 404 | 38,240 | 401 (85) | $0.080 |
| 23 | 18:42:09 | process_manager: stop the server; file_modify: write _tmp/serve.cjs (own static server) | 641: ok; 54: ok | 39,388 | 468 (49) | $0.083 |
| 24 | 18:42:13 | process_manager: start node ../_tmp/serve.cjs on 8125 | 503: ok | 40,249 | 130 (0) | $0.082 |
| 25 | 18:42:16 | shell: fetch check on :8125; browser: diagnose | 1,174: 14 x 200; 1,095: ok | 40,628 | 395 (42) | $0.085 |
| 26 | 18:42:19 | browser: navigate 127.0.0.1:8125 | 310: error, Chrome exited | 42,152 | 102 (0) | $0.085 |
| 27 | 18:42:21 | file_modify: write _tmp/shot.cjs (headless screenshot) | 53: ok | 42,383 | 606 (79) | $0.091 |
| 28 | 18:42:26 | shell: node _tmp/shot.cjs | 141: ok | 43,022 | 104 (0) | $0.087 |
| 29 | 18:42:44 | file_modify: write _tmp/cdp.cjs (DevTools-protocol driver) | 52: ok | 43,207 | 1,615 (132) | $0.103 |
| 30 | 18:42:54 | shell: cdp.cjs run | 632: ok, 39 fps | 44,856 | 101 (0) | $0.091 |
| 31 | 18:43:19 | file_modify: write _tmp/png.cjs (PNG decoder, colour grid) | 52: ok | 45,323 | 1,194 (234) | $0.103 |
| 32 | 18:43:28 | shell: png.cjs on the screenshot | 1,465: ok | 46,549 | 69 (0) | $0.094 |
| 33 | 18:43:30 | fs_search: grep with "\|" alternation in scene.js | 120: 0 matches | 47,462 | 420 (291) | $0.099 |
| 34 | 18:43:35 | fs_search: grep "night" in scene.js | 346: 2 matches | 47,950 | 124 (21) | $0.097 |
| 35 | 18:43:37 | file_read: scene.js lines 505-704 | 10,664: ok | 48,251 | 101 (0) | $0.098 |
| 36 | 18:43:39 | shell: cdp.cjs run, 22 s wait | 1,284: ok, 32 fps | 54,383 | 668 (566) | $0.115 |
| 37 | 18:44:20 | shell: png.cjs | 1,465: ok | 55,767 | 69 (0) | $0.112 |
| 38 | 18:44:22 | fs_search: grep "const SKY" | 234: 2 matches | 56,688 | 322 (216) | $0.117 |
| 39 | 18:44:27 | file_read: scene.js lines 1-135 | 7,237: ok | 57,129 | 101 (0) | $0.115 |
| 40 | 18:44:28 | shell: cdp.cjs run with the UI hidden | 1,530: ok, 46 fps | 61,332 | 463 (296) | $0.127 |
| 41 | 18:45:02 | shell: png.cjs | 1,467: ok | 62,670 | 69 (0) | $0.126 |
| 42 | 18:45:06 | file_modify: edit app.js, expose window.__atmos.live (debug hook) | 76: ok | 63,576 | 458 (295) | $0.132 |
| 43 | 18:45:11 | shell: cdp.cjs run printing __atmos.live() | 1,650: ok, 50 fps | 64,074 | 140 (0) | $0.130 |
| 44 | 18:45:41 | fs_search x2: grep setTarget, "first = false" | 281: 2 matches; 166: 1 match | 65,156 | 396 (189) | $0.134 |
| 45 | 18:45:46 | file_read: scene.js lines 795-854 | 2,168: ok | 65,812 | 101 (0) | $0.133 |
| 46 | 18:45:47 | fs_search x2: grep "function setTarget", "function frame" | 201: 1 match; 172: 1 match | 44,693 | 263 (55) | $0.092 |
| 47 | 18:45:50 | file_read: scene.js lines 520-669 | 7,813: ok | 45,179 | 101 (0) | $0.091 |
| 48 | 18:45:52 | fs_search x2: grep "const T ", "function damp" | 263: 1 match; 89: 0 matches | 49,696 | 514 (309) | $0.105 |
| 49 | 18:45:58 | shell: cdp.cjs run forcing a night target | 1,298: ok, 48 fps | 50,442 | 453 (220) | $0.105 |
| 50 | 18:46:39 | fs_search: grep "damp =" | 208: 1 match | 51,662 | 536 (431) | $0.109 |
| 51 | 18:46:47 | file_modify: edit scene.js, raw frame time for quality scaling | 78: ok | 52,309 | 811 (576) | $0.113 |
| 52 | 18:46:54 | file_modify: edit scene.js, raw frame time in the slow-frame check | 78: ok | 53,161 | 260 (56) | $0.109 |
| 53 | 18:46:57 | shell: cdp.cjs run, 45 s wait | 637: ok, 2 fps | 53,462 | 185 (83) | $0.109 |
| 54 | 18:47:51 | shell: png.cjs | 1,465: ok | 54,017 | 111 (41) | $0.109 |
| 55 | 18:47:55 | shell: node -e to patch cdp.cjs | 1,098: exit 1, quoting | 55,121 | 403 (229) | $0.114 |
| 56 | 18:48:00 | file_modify: edit cdp.cjs, window size from env | 69: ok | 56,057 | 173 (0) | $0.114 |
| 57 | 18:48:02 | file_modify: edit cdp.cjs, sleep from env | 69: ok | 56,274 | 324 (157) | $0.116 |
| 58 | 18:48:08 | shell: cdp.cjs night, day, storm and snow sequence, 50 s wait | 1,579: ok, 2 fps | 56,642 | 475 (169) | $0.118 |
| 59 | 18:49:50 | shell: png.cjs on night.png | 1,467: ok | 49,678 | 111 (43) | $0.100 |
| 60 | 18:49:54 | shell x2: png.cjs on day.png and storm.png | 1,465: ok; 1,467: ok | 50,802 | 243 (110) | $0.104 |
| 61 | 18:49:59 | process_manager: stop the server; fs_search: ls weather-site | 525: ok; 496: 4 entries | 52,863 | 462 (318) | $0.110 |
| 62 | 18:50:06 | vercel: deploy weather-site --prod | 1,405: ok | 50,354 | 126 (0) | $0.102 |
| 63 | 18:50:20 | shell: fetch 6 paths from the live site | 750: 6 x 200 | 51,184 | 290 (97) | $0.105 |
| C63 | 18:50:27 | compaction after step 63 | summary | 33,352 | 2,337 (511) | $0.090 |
| 64 | 18:50:44 | final answer, no tool call | reply, 1,418 chars | 14,377 | 553 (42) | $0.034 |
