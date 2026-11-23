# System One judgment models (TypeSafe Jev)

> **Status: Planned.** Written 2026-11-23 at commit `cfb06d6c`, from research on 2026-11-13 and a re-read of the vendor documentation on 2026-11-23. The judgment features this study led to are built and documented in the [Judgment Service](../judgment_service.md): the Classify node, triage of app events, review auto-accept and the Mobile element finder. Routing, at the end of this note, is planned and not built. Line numbers are those of `cfb06d6c`; find a symbol by name before relying on one.

## The question

On 2026-11-13 the owner asked how decision-only models, "System One" classifiers such as TypeSafe's Jev, could cut what employees spend on decisions.

**Decisions cost full model runs.** A full model run makes every decision an employee needs: whether an inbound event needs work, whether a teammate's result is good enough, where a button is.

**Under the team model an irrelevant event is expensive.** Every app, webhook, polling and cron event becomes a job. An event that needs nothing still runs the Orchestrator, a specialist, a review and Chat. One delegated task costs five model runs and at least 11 calls, and each step sends the transcript and every tool definition again.

**What Jev is.** TypeSafe's Jev answers typed questions about one input:

- Choice: pick one of up to 255 options.
- Score: up to 10 levels.
- Noul: the probability that a statement is true.
- Box: a bounding box in an image.

It returns probabilities and never writes text. It costs $0.042 per million input tokens, with output free, takes about 100 ms, and answers many questions in one call.

## TypeSafe's API

Read on 2026-11-23 from `docs.typesafe.ai/api/system-one.md` and `images.md`; the 2026-11-13 digest of the same pages was wrong on several of these points.

- **Request.** `POST https://api.typesafe.ai/v1/systemone` with a Bearer key. The body is `{model, state, questions, assets?}`, and the response is `{model, answers, usage}`. Limits are 64k tokens per request and 32k per question.
- **Questions** are a map keyed by a name the caller chooses. Each is `{type, instructions, criteria}`:
  - choice: `criteria` maps each option name to a description or null, 1 to 255 options;
  - score: `criteria` is a list of level descriptions, 1 to 10;
  - noul: optional `criteria` `{"true", "false"}`;
  - box: no criteria.
- **Answers** come back under the same names:
  - noul: `{type, noul}`, the probability of true;
  - choice: `{type, choice, confidence, probabilities}`, the probabilities a map by option;
  - score: `{type, score, confidence, legend, probabilities}`. `score` is the probability-weighted level and can be fractional; `legend` and `probabilities` are keyed by the level's index as a string;
  - box: `{type, box: {top, bottom, left, right}, confidence}`.
- **`usage`** is `{input_tokens, output_tokens}`, with no cost field.
- **Images** go in `assets`, a map of a placeholder string to `{type: "image", format: "png"|"jpg", data_base64}`. The base64 is raw, not a data URL. The placeholder must appear in `state`, `instructions` or `criteria`, or the request gets a 400.
- **Errors:** 400, 401, 413, 415, 422, 429 and 529.
- **Models:** `jev-latest` points to `jev-1.14.0`, and `jev-1.13.0` also exists. No router id is documented there.
- **Box** is a preview limited to Jev 1.14. It needs the header `TypeSafe-Danger-Zone-Images: true` and takes PNG or JPEG images of 280 to 1568 pixels a side. Its fractions are measured from the top-left corner. It always returns a box, so it is asked together with a noul.

## OpenRouter

OpenRouter serves the same shape at `/api/v1/systemone`. Read on 2026-11-23 from `openrouter.ai/docs/api/api-reference/systemone/submit-a-system-one-request.md` and `guides/community/jev.md`.

- **Models:** `typesafe/jev-1.14`, the alias `~typesafe/jev-latest`, and `typesafe/jev-1.13`, which has no box.
- **The reply adds** `id`, `provider`, and `usage.cost` with `is_byok` and `cost_details`. The pages disagree on whether `usage.cost` is in USD or credits. An answer can also be `{"type": "refusal"}`.
- **Images:** no image header is mentioned, and its image limits give 256 pixels for the smallest side, against TypeSafe's 280.
- **Optional request fields:** `provider`, `session_id`, `trace` and `user`.
- Its alpha Decisions API also serves about 40 decision models from other vendors. The owner decided to leave those out.

## Routers and decision models in general

- **Jev Router** has no fee and reports the model it served. On Vals (2026-11-03) no router beat the best single model in its pool.
- **Decision models** score 3 to 4 points below small LLMs at maximum effort, at 15 to 60 times lower cost (Artificial Analysis).

## How an employee spends its model calls

The code at `cfb06d6c`. AA is `server/services/temporal/agent_activities.py`, AW `agent_workflow.py` beside it, AIW `agent_instance_workflow.py`, and SVC `server/services/agent_instances/service.py`.

**One delegated task is five runs:**

1. Chat: `submit_job`, then an acknowledgement.
2. The handoff, with no model call.
3. The Orchestrator assigns.
4. The specialist works.
5. The Orchestrator reviews: `get_task`, `accept_task`, end.
6. Chat reviews.

Every step sends the transcript, the inbox and every visible tool (AW:1475-1493). Each job's answer is appended to Chat's conversation (`server/services/chat/branches.py:354-368`).

- **Non-chat events** become jobs at AA:1504-1506 with no model call. The Orchestrator is the first agent to call a model.
- **The model** is frozen per generation and instance, and resolved once per run at AA:1401-1408. A behaviour change in prepare for generations already admitted goes behind a manifest flag.
- **Reviews.** Only `accept_task` accepts (SVC:829-843), and a parent submits when the reviewer's run ends (SVC:1189-1232).

## Events

- **Deployed events** take this path: `dispatch.emit`, then `WorkflowControlWorkflow`, then `_spawn_push_run`, then `TriggerListenerWorkflow._spawn_child_run`, where the node's filter runs (`trigger_listener_workflow.py:342-350`). The filter is skipped when none is set.
- **Cron** starts `MachinaWorkflow` directly (`server/nodes/scheduler/cron_scheduler/_workflow.py:108-235`).
- **Conditional edges** branch on `result.<field>` with numeric comparison (`server/services/execution/conditions.py:81-123, 318-342`). There is no If or Switch node.

## LLM layer and cost

**Fixed since 2026-11-13:**

- Every step books a priced row (AA:693-709), and OpenRouter's own charge is the booked total.
- OpenRouter models are priced from the registry.
- Cache writes are priced correctly, and cached tokens are no longer charged twice.
- Prompt caching is on for Claude on Anthropic and OpenRouter, with the 5-minute cache only.

**Still open:**

- `ChatUnifier` has no structured output.
- The served model is never recorded.
- Routers in the model registry keep the -1000000 price placeholder (`typesafe/jev-router` now lists a maximum output of 128000).
- The hooks before each model call are `spend_limit_notice` (AA:584-586) and `monthly_budget_reached` (AA:594-608).

## What was built

The owner chose these on 2026-11-13 and 2026-11-23. The [Judgment Service](../judgment_service.md) describes each.

- **Providers:** Jev only, through the owner's TypeSafe key or an OpenRouter key. When neither is connected, the owner's chat model answers choice, score and noul as JSON. Box has no fallback.
- **The Classify node:** one node, one input, a named map of typed questions answered in one call. An agent can call it as a tool.
- **Triage** at job admission: the "Wake me when" rule is drafted at hire, edited from the employee's Wake button, and frozen per generation. An empty rule keeps the earlier behaviour.
- **Review auto-accept:** above an owner-set confidence, off until set, a teammate's submitted task is accepted without waking its reviewer. Chat's review of the final answer is never skipped.
- **Box:** only the Mobile element finder, used when the phone's UI tree has no match.

## Routing (planned, not built)

The owner planned all three for a later phase, to be decided from the per-step usage rows.

- **Route work, not models.** Before an agent's turn, a judgment picks the skills, tools or teammate a request needs, and the rest are hidden by setting `llm_hidden` in prepare (AA:1785-1788; AW:1475). The regex team-change router (`server/services/employees/talk_team_change.py:8-48`) becomes a choice.
- **Cheap or strong tiers per run.** One judgment right after AA:1401-1408 picks the model for the whole run, and an explicit model choice always wins. The values derived from the model (AA:1410-1465, 1925, 2072) are recomputed. It goes behind a manifest flag for new generations.
- **The hosted Jev Router as a model.** First record the model that served each call (AA:700 books the requested id), base the compaction threshold on the served model, and fix the 4096 clamp for routers whose registry maximum is 0 (`server/services/model_registry.py:406-413`).

## Seen along the way

Not part of this work:

- A deployed Email Receive ignores its Filter Query (`server/nodes/email/email_receive/__init__.py:85-87`; email records carry no dict `data`).
- Review notices carry no `task_ref`, which forces a `get_task` call on every review (SVC:52-55, 540-541).
- Translate's LLM path books no tokens (`server/nodes/translate/_providers/llm.py:13-16`).
- Docs drift: `docs-internal/agent_delegation.md:43-49` still shows the v2 layout.
- Open cost fixes from the treg and three.js run analyses: clearing carried-over results (`context_pressure_version` is still 1), a cache marker after the system prompt, the 1-hour cache, and smaller fixed prefixes.

## Sources

- TypeSafe: https://docs.typesafe.ai/llms.txt, https://docs.typesafe.ai/api/system-one.md and the images page beside it.
- OpenRouter: https://openrouter.ai/docs/api/api-reference/systemone/submit-a-system-one-request.md and https://openrouter.ai/docs/guides/community/jev.md
- Router and decision-model comparisons: Vals (2026-11-03) and Artificial Analysis.
