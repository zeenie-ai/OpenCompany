# treg reels run: token cost and coverage (2026-11-13)

On 2026-11-13 an agent searched Instagram through treg for viral reels. This note covers two questions about that run:

- why three chat messages cost about $2.41 of model time for $0.05 of data;
- why the agent reported 9 reels when the owner counted 11.

The figures were measured on 2026-11-13 and written up on 2026-11-17. Some of the sources have been cleared since then; [Data and method](#data-and-method) says which.

## Summary

- **Cost.** Three messages took 24 model steps: 1,148,503 input tokens and 10,849 output tokens of Claude Sonnet 5.5 through OpenRouter, about $2.41.
  - treg's 24 calls cost $0.047, so the model cost about 50 times as much as the data.
  - No step used prompt caching. Caching alone would have cut the model cost by about 73%.
- **Waste.** Most of the input was waste the product can remove (the amounts overlap):
  - tool answers kept in the conversation untrimmed: about 370,000 tokens;
  - earlier messages' search results carried into later runs: about 355,000;
  - tool and skill definitions sent on every step: about 270,000;
  - failed shell commands: 142,013.
- **Coverage.** The agent chose correctly among the reels it fetched, but it fetched too few.
  - It stopped paging queries that were still returning this week's posts.
  - Every search paired matcha with a place or venue name.
  - The two reels the owner counted and the agent missed were most likely two reels tagged Brooklyn, New York, whose captions never name New York.
- **Fixes.** One is done (the shell tool's description, `d2d3f525`, not pushed); four are open.

## The job

| | |
|---|---|
| Workflow | AI Assistant (workspace `AI_Assistant_1`), on `company dev` |
| Model | `anthropic/claude-sonnet-5.5` through OpenRouter, $2 per million input tokens and $10 per million output tokens |
| Tools | the treg node and nine others, plus a catalogue of 19 skills |
| Context | a Context node, so each message continued the same conversation |
| Question | Instagram reels about matcha in New York with more than 100k views this week |

| Run | Owner's message | treg calls | Model steps | Input tokens | Output tokens | Model cost | Answer |
|---|---|---|---|---|---|---|---|
| 1 | *can you check recent instagram reels with more than 100k views this week related to "matcha in new york"* | 1 | 7 | 104,311 | 1,505 | $0.22 | 2 reels |
| 2 | *check properly* | 8 | 12 | 642,500 | 3,857 | $1.32 | 5 reels |
| 3 | *i want all the ones this week with more than 100k views* | 15 | 5 | 401,692 | 5,487 | $0.86 | 9 reels |
| Total | | 24 | 24 | 1,148,503 | 10,849 | $2.41 | |

Costs are rounded to the cent.

Each step's request was larger than the one before it:

| Run | First step (input tokens) | Last step (input tokens) |
|---|---|---|
| 1 | 11,450 | 18,015 |
| 2 | 18,613 | 65,251 |
| 3 | 66,210 | 95,582 |

## Where the tokens went

The causes overlap: an untrimmed answer from run 2 is also part of what run 3 carried. The shares therefore do not add up to 100%.

| Cause | Input tokens | Share of all input |
|---|---|---|
| Untrimmed treg answers kept in the conversation | about 370,000 | 32% |
| Earlier messages' results carried into later runs | about 355,000 | 31% |
| Tool and skill definitions on every step | about 270,000 | 24% |
| Failed shell commands | 142,013 | 12% |

Missing prompt caching affects every one of these, since each step paid full price again for everything already sent.

### No prompt caching

- `cache_read` was 0 on all 24 steps. Nothing in `server/` marks a cache breakpoint (`cache_control`), either for OpenRouter or for the native Anthropic API.
- Estimate with caching, at Anthropic's prices for Sonnet 5.5 (a five-minute cache write at 1.25 times the input price, a cache read at $0.10 per million tokens):
  - the 24 requests would have cost $0.65 instead of $2.41, 73% less;
  - the messages were less than five minutes apart, so a cache kept from one message to the next would have brought the cost to $0.45, 81% less.
- The estimate assumes each request begins with the previous request unchanged.

### Untrimmed answers stayed in the conversation: about 370,000 tokens

- **Reel size.** With no `fields` list, the search returned each reel with 30 keys, including signed CDN links for the video, the thumbnail and the avatar. That is about 3,400 characters per reel, at roughly 1.6 characters per token.
- **Which answers stayed.** The treg node saves an answer to the workspace only when it is over 20,000 characters (`INLINE_LIMIT_CHARS` in `server/nodes/treg/_result.py`). Three answers in run 2 were 15,000 to 16,500 characters. They stayed in the conversation at about 9,000 tokens each and were sent again 13 to 14 times.
- **The hint the agent ignored.** The provider's answer said to re-run with `fields`, `max_items`, `summary` or `jq`, which trim the answer without changing the price. The agent passed `fields` on one call in run 2 and on every call in run 3.

### Earlier messages were carried forward: about 355,000 tokens

- The Context node keeps the whole conversation from one chat message to the next, tool results included.
- Run 2 started at 18,613 input tokens and run 3 at 66,210; run 1 had started at 11,450.
- Run 3 never needed the raw search results from runs 1 and 2 again.

### Fixed definitions on every step: about 270,000 tokens

- About 11,400 tokens of every step were the definitions of ten tools and the catalogue of 19 skills. The browser tool's definition alone was 7,193 characters.
- The browser, Vercel, process manager, memory and to-do tools had no use in a search job.
- The agent never opened a skill, so the treg skill's guidance never reached it.

### Failed shell commands: 142,013 tokens

- The shell tool ran Nushell, but its description said only "Execute a shell command", so the model wrote bash.
- Three steps in runs 1 and 2 failed:
  - a multi-line `python -c` whose `\n` Nushell turned into a real line break;
  - a pipe into `sort -u`;
  - `sed -i` with an escape Nushell does not accept.
- Each retry sent the whole conversation again.
- The first report, on 2026-11-13, put this at 286,000 tokens (25%). That figure also counted the steps after each failure, where the agent rewrote the command into a file.

### The cost the owner was told

The agent said the whole check "cost about $0.05", which was only treg's share. OpenCompany does not record the token use of an agent that runs on Temporal: `token_usage_metrics` had no rows for this run, so the $2.41 appears nowhere in the app.

## Why 9 reels, not 11

### What was fetched

- **The searches.** All 24 calls were Instagram reel searches.
  - The first went through treg's routed tool (`treg.instagram.search.reels`).
  - The other 23 went to one provider's keyword search (`anyapi.instagram.search.reels`), which ranks by relevance and has no date filter.
- **The result.** Together the calls returned 111 different reels. Ten of them were posted on or after Nov 6 and had more than 100k views:
  - the agent's nine;
  - a reel with 34M views captioned only "New York" and an emoji, which the agent left out as not about matcha.

The agent chose correctly from what it had.

### What it did not fetch

**Pages it stopped before.** The agent stopped paging these three queries while they were still returning mostly this week's reels:

| Query | Pages fetched | This week's reels / reels returned | Over 100k views |
|---|---|---|---|
| matcha lab new york | page 1 | 19 / 19 | 7 |
| matcha lab nyc | pages 1 and 2 | 21 / 21 | 6 |
| matcha new york this week | page 1 | 9 / 12 | 6 |

In run 3 the agent fetched page 1 of "matcha lab new york" and "matcha new york this week" a second time, with different `fields`. Those two calls returned no new reels, and page 2 of either query was never fetched.

**Other ways in.** Every search paired matcha with a place or a venue. The agent never:
- searched for the topic on its own;
- searched the hashtags in the captions (#matchalab, #matchalabnyc);
- opened the venue's location page;
- tried a second provider.

**Counts near the line.** Two reels from this week had 89,413 and 85,572 views when fetched. One of them had 69,110 the day before, so either could have passed 100,000 by the time the owner counted.

### The two the owner counted

The owner did not say which two reels they meant; the 2026-11-13 report left this open. It was worked out on 2026-11-17, while writing this note.

On 2026-11-12, the day before, the same question had been answered by querying treg directly:
- with three providers (anyapi, ScrapeCreators and TikHub);
- counting a reel as New York if its location tag or caption named the city.

That answer listed five New York reels, and together with the agent's nine they make exactly 11.

The two that were in the 11-12 answer and missing from the agent's are two reels from one café account:
- **Posted:** Nov 6, with 5.0M and 8.0M views on Nov 12.
- **Location tag:** "Brooklyn, New York".
- **Captions:** they mention matcha but not New York.
- **How 11-12 found them:** on pages 3 to 5 of a plain "matcha" search, keeping them for their location tag.
- **The agent's data:** none of its 24 calls returned them, and its five saved answers do not contain them.

So the gap most likely comes from the search plan. The agent looked only for captions that name New York. The provider it used returns `locationName` with each reel, so a broader search filtered on location would have caught both.

## Fixes

| # | Fix | Saving on this run | Status on 2026-11-17 |
|---|---|---|---|
| 1 | Shell tool: the description names the shells and the forms that work under each | the 142,013 tokens of failed steps | Done: `d2d3f525` with documentation bundle `fcbdaae1`, on local `main`, not pushed |
| 2 | Prompt caching for Claude models, through OpenRouter and the native Anthropic API | about 73% of model cost | Open: nothing in `server/` sends `cache_control` |
| 3 | treg node: save every answer to the workspace with a short preview, and have the tool description tell the model to pass `fields` and `max_items` | about 370,000 tokens | Open: answers under 20,000 characters still go inline, and the tool description mentions neither parameter |
| 4 | Clear earlier messages' tool results when a new chat run starts, keeping the answers | about 355,000 tokens | Open: changes the rules that recorded runs replay under, so it needs `context_pressure_version` 2 (`server/services/temporal/agent_context_pressure.py` is at 1) |
| 5 | Record the token use of agents on Temporal, and report model cost next to treg's | the true cost reaches the owner | Open: nothing under `server/services/temporal/` writes token usage |

Notes for fixes 2 and 4:
- **Recommended setup.** The Claude API documentation recommends, for agent loops, one cache breakpoint at the end of the system prompt plus automatic caching for the growing conversation.
- **Rewrites break the cache.** Rewriting an earlier message breaks the cache from that message on.
  - Clearing earlier results once at the start of a chat run (fix 4) costs one cache write per message.
  - The existing clearing over the transcript budget ([Agent Context Flow](../agent_context_flow.md#transcript-size-capped-results-cleared-old-results)) can run after any tool turn, and costs one cache write each time it does.

The node itself is described in [treg Service](../treg_service.md).

**Setup changes that need no code:**
- Give a research employee only treg and one way to process data. Removing the browser, Vercel, process manager, memory and to-do tools and the 19 skills saves about 8,000 tokens a step.
- Start a new conversation for each research question, or leave the Context node off a research worker.
- Put the time window, the view threshold and "all of them" in the first message.

## A search recipe for the treg skill

1. Make every search in one step: the topic on its own, the venue, the hashtags, the neighbourhoods and the location page, each with `fields` and short captions.
2. Use more than one provider for the same search, since they return different reels.
3. Keep paging a query while its last page still has posts inside the window.
4. Keep posts whose location tag is in the city, even when the caption does not name it.
5. Remove duplicates by shortcode across all saved answers, with one tested script.
6. Check the counts near the threshold again at the end, and list those reels as close.
7. Leave dates out of the query text, because the search does not understand them.

Run this way, the same job should take three or four model steps and 30,000 to 100,000 input tokens in total: about $0.10 to $0.25 of model cost at Sonnet 5.5's prices.

## Data and method

Every source was only read, and no request was sent to treg for this analysis.

| Source | What it gave | Still available on 2026-11-17 |
|---|---|---|
| `treg.log` at the repository root (ignored by git) | the backend log from `company dev` during the three runs | yes |
| The Temporal history of the agent runs, in `.opencompany/temporal.db` | input and output tokens for each model step | no, it is no longer in the database |
| `agent_conversations` in `.opencompany/workflow.db` (workflow `2`, generation 7) | the conversation, with every tool call, its arguments and its answer | no, the workflow has since started generation 8 |
| `.opencompany/workspaces/AI_Assistant_1/treg/` | the five answers large enough to be saved as files | yes |
| `api_usage_metrics` | treg's charge for each call: 24 rows, $0.0472 | yes |
| The 2026-11-12 direct queries | the five New York reels, and the raw search pages behind them | the chat answer only; the raw pages were kept in a temporary folder |

The caching estimate replays the per-step input counts from the Temporal history with the Sonnet 5.5 prices given above.
