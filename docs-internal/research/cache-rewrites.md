# Cache rewrites: what writes the same prompt to the cache twice (2026-11-20)

> **Status: Historical analysis.** It describes two working sessions on a development instance on 2026-11-20 and the code of that week. Nothing it proposes has been built. Current behaviour is in [Native LLM SDK](../native_llm_sdk.md#prompt-caching), [Agent Teams](../agent_teams.md) and [Pricing Service](../pricing_service.md).

Prompt caching for Claude has been on since 2026-11-18. A cached prompt is cheap to read and dear to write, so the question that is left is how often an agent writes a prompt it has already written. This note measures that in two sessions on the same day: one on the older agent lifecycle and one on the reusable agents.

Code was read at commit `127df4a9`.

## Summary

- **Older lifecycle.** In one 53-minute session, 75% of the tokens written to the cache were full rewrites of a prompt the agent had already sent. They cost about $3.28 of a $6.32 session.
- **Reusable agents.** In the first team session that ran on them, no step rewrote its own prompt. The causes that produced the rewrites above do not occur there.
- **What is left.** Every new instance writes its whole fixed prefix (system prompt and tool definitions) on its first step. Two instances of one template send a byte-identical prefix, so the second write repeats the first. That was 17% of the tokens written and about 11% of the session's cost.
- **Waiting.** A conversation the owner returns to after more than five minutes is written again in full, because the cache entry has expired.
- **Not built.** A cache marker at the end of the fixed prefix would turn the repeated prefix writes into reads. Whether to build it is the owner's decision; the numbers are under [Options](#options).

## Prices

Claude Sonnet 5.5 through OpenRouter, per million tokens, from the model registry: input $2.00, output $10.00, cache read $0.10, cache write $2.50. Writing a token costs 25 times what reading it costs. All costs below are token counts at these prices.

## Session A: older lifecycle

111 model calls between 10:54 and 11:48 UTC on one employee: 45 by its lead agent (chat turns and review turns), 42 by a coding teammate and 24 by a browser teammate.

| | Tokens | Cost |
|---|---|---|
| Cache writes | 1,756,827 | $4.39 |
| Output | 164,707 | $1.65 |
| Cache reads | 2,817,316 | $0.28 |
| Total | | $6.32 |

1,313,506 of the written tokens (75%) were full rewrites: a step that read nothing from the cache, by an agent that had already run in the session. One summarizer call wrote another 54,761 that nothing would ever read. The table accounts for about 1.30 million of the rewrites; its figures are rounded to the nearest thousand.

| Cause | Tokens rewritten | Cost | How it was established |
|---|---|---|---|
| Trimming cleared earlier tool results in the middle of a run (with the summarizer call's 55,000) | 526,000 | $1.32 | Recorded step inputs compared |
| A teammate's task text sat in its system prompt, ahead of the conversation it carried over | 345,000 | $0.86 | Recorded step inputs compared |
| The chat guide was in the answering agent's system prompt on chat turns and absent on its review turns | 283,000 | $0.71 | Recorded step inputs compared |
| More than five minutes passed between two turns | 154,000 | $0.39 | Inferred from the gaps |
| A miss between byte-identical requests 72 seconds apart | 47,000 | $0.12 | Recorded step inputs compared; cause not known |

Each of the first three changes text near the start of the prompt. The cache matches a prompt from its first byte, so everything after the changed text is written again.

A later run of the same employee that afternoon shows the first cause in the rows alone. A browser teammate's prompt had grown to 71,967 tokens. The next step's prompt was 36,451 tokens with no summarizer call in between, and that step read nothing and wrote 36,449 tokens ($0.09). The cut saved about 35,500 tokens of reading on each later step ($0.0036 a step), so it would have paid for itself after 25 more steps. The run made two.

## Session B: reusable agents

33 model calls between 16:41 and 16:52 UTC on one employee: a Talk agent that answers the owner, and two jobs it handed to its team. Each job ran one lead instance and one teammate instance. Six earlier team runs that day had failed before making a model call, so this is the first that produced rows.

| Agent | Steps | Written | Read | Output | Cost | Written on the first step |
|---|---|---|---|---|---|---|
| Talk | 10 | 14,740 | 91,562 | 1,331 | $0.059 | 4,729 |
| Lead, job 1 | 8 | 25,633 | 92,174 | 2,077 | $0.094 | 8,360 |
| Teammate, job 1 | 4 | 13,025 | 27,682 | 1,154 | $0.047 | 7,564 |
| Lead, job 2 | 7 | 27,063 | 82,741 | 2,031 | $0.096 | 8,400 |
| Teammate, job 2 | 4 | 12,246 | 29,036 | 1,701 | $0.051 | 7,723 |
| All | 33 | 92,707 | 323,195 | 8,294 | $0.347 | 36,776 |

Cache writes were $0.232 of the $0.347 (67%), output $0.083 (24%) and cache reads $0.032 (9%).

What the recorded step inputs show:

- **No rewrites inside a run.** In all four instance runs, every step's system prompt and tool list equal the step before, and its messages extend the step before. The rows agree: every step after an instance's first read its earlier prompt from the cache.
- **The task arrives as a message.** An instance's first request is its system prompt, its tools and one task message. Nothing specific to the job sits in the system prompt.
- **Two instances of one template share their prefix.** The two leads sent the same 3,245-character system prompt and the same 26 tools. The two teammates sent the same 1,772-character system prompt and the same 15 tools. Only the task message differed (399 and 541 characters for the leads, 735 and 1,131 for the teammates).
- **The second instance still wrote everything.** The second lead started 2 minutes 9 seconds after the first lead's last step, read nothing and wrote 8,400 tokens. The second teammate did the same with 7,723. A cache entry is written only where a request ends, so no entry existed for the shared prefix alone.

So 16,123 of the 92,707 written tokens (17%) were a prefix another instance had written minutes before, less the few hundred tokens of the task messages.

The Talk agent read its conversation from the cache on each of its four chat runs, which were at most 4 minutes 33 seconds apart. Earlier that afternoon three other employees' Talk agents were left for 13 to 29 minutes between steps. Each then wrote its whole prompt again: 5,630, 12,965, 13,883 and 14,794 tokens, $0.12 together.

## The two sessions compared

| | Session A | Session B |
|---|---|---|
| Model calls | 111 | 33 |
| Cache writes as a share of cost | 69% | 67% |
| Rewrites as a share of written tokens | 75% | 17% |
| Trimming in the middle of a run, and the summarizer call | 526,000 tokens | none; no prompt passed 28,000 tokens |
| Task text in the system prompt | 345,000 | does not occur |
| Chat guide present on some turns only | 283,000 | does not occur |
| Expiry after five minutes | 154,000 | not in this session; see the Talk gaps above |
| A new instance repeating its template's prefix | did not exist | 16,123 |

The share of cost spent on writes is the same in both, for different reasons. In session A most writes were repeats. In session B the runs were short, so each instance's first write is a large part of its cost.

Session B's prompts stayed small. It says nothing about trimming on the reusable agents, which applies the same rules once a run grows.

## Options

None of these is built.

- **A cache marker at the end of the fixed prefix.** A second marker after the system prompt would write an entry for the tools and system prompt alone, and a later instance of the same template would read it.
  - It saves about $0.02 for each instance that starts within five minutes of another instance of its template. In session B that is $0.038 of $0.347 (11%), or 26% of the second job.
  - It saves nothing for jobs more than five minutes apart, or for an employee that runs on a schedule.
  - Open: whether OpenRouter accepts a marker on a content part together with the top-level field used today. One pair of live requests would answer it.
- **A one-hour lifetime.** A write that lasts an hour costs $4.00 a million tokens on this model instead of $2.50. It would cover the owner's pauses, but it raises the price of every write, including the many that are never read again.
- **Smaller fixed prefixes.** Tool definitions are most of an instance's first write: the lead's 26 tools and the teammate's 15 come to roughly 7,000 tokens each, estimated from the size of the first prompt less the system prompt and the task message. A shorter tool list lowers the first write of every instance, with or without a marker.

## For the trimming redesign

The trimming rules are to be redesigned with RFC-0005 Phase 4. Three things from session A apply to it:

- Clearing text in the middle of a prompt has a price: everything after it is written again at 25 times the reading price. A cut that removes half the prompt pays for itself after about 25 further steps.
- A cut costs nothing extra when the cache is cold anyway, which is the start of a turn that follows a pause of more than five minutes.
- The summarizer's prompt is used once. Sent with the cache flag, it pays the write price for an entry nothing reads.

## Data and method

- **Rows.** One row per model call in `token_usage_metrics`: prompt, output, cache-read and cache-write tokens, the agent node, the chat run and the employee job.
- **Step inputs.** Each `agent.execute_llm_step` activity's input is recorded in the Temporal history of its workflow. Two inputs were compared field by field (`system_message`, `tools`, `messages`), ignoring the `ts` stamp the conversation store adds to a stored message.
- **Reading.** Both databases were opened read-only.
- **Costs.** Computed from token counts at the registry's prices. For 35 rows (session B and two calls just before it) the stored `total_cost` is twice the charge: a reader of OpenRouter's reported cost added a figure that repeats it. That reader was corrected in `127df4a9`; the stored rows were left as they are.
- **Scope.** Two sessions on one day, one model, one provider. Session B is two short jobs.
