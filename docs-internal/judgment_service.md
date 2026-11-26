# Judgment service (System One judgments)

`server/services/judgment/` answers typed questions about one input with
probabilities instead of text. It is for decisions where a yes, a no or a
label is enough, so that a full model run is not spent on them. The
Classify node, triage of app events, review auto-accept and the Mobile
element finder use it ([Where it is used](#where-it-is-used)).

## Questions and answers

A judgment asks named questions about one input, the *state*: a string, or
any JSON value. Names are identifiers (letters, digits and underscores, not
starting with a digit), because an edge condition reads an answer as
`result.answers.<name>.<field>` and `services/execution/conditions.py`
splits a field on dots and treats a part made only of digits as a list
index.

| Type | Asks | Answer fields |
|------|------|---------------|
| `noul` | how likely a statement about the input is true | `probability` (0 to 1) |
| `choice` | which of 2 to 255 `options` fits | `choice`, `confidence`, `probabilities` (by option) |
| `score` | where the input sits on 2 to 10 `levels`, lowest first | `score` (the expected level, from 0, may be fractional), `confidence`, `probabilities` (a list, lowest first) |
| `box` | where something is in an image | `top`, `bottom` (fractions of the height), `left`, `right` (fractions of the width), measured from the top-left corner; `confidence` |

A box is always returned, even for something that is not on the image, so a
box question is asked together with a noul asking whether it is there. A box
question needs an image.

The protocol is `services/judgment/protocol.py`: `Question`, `Answer`,
`Judgment`, `JudgmentImage`, `JudgmentRequest`, `JudgmentReply`,
`check_questions` and `check_answers`.

## `judge`

```python
from services.judgment import judge

judgment = await judge(
    state, questions,
    node_id=..., workflow_id=..., session_id=..., principal=...,
    images=(), calibrated_only=False, timeout=None,
)
```

- It tries the providers in the order `server/config/judgment_defaults.json`
  gives, and uses the first one that:
  - is registered;
  - answers every question type asked;
  - is calibrated, when the caller passes `calibrated_only`;
  - takes images, when there are some;
  - has a key.

  A `keyless` provider raises `NotConnected` itself when it has nothing to
  call, and the next one is tried.
- **One attempt.** The chosen provider gets one attempt, under its own
  `timeout_seconds` or the caller's `timeout`. A failure is final: no other
  provider is tried and nothing is repeated. Every caller has a safe way on
  without an answer.
- **Never raises for a provider's failure.** The judgment comes back with
  `available: false` and a `reason`:
  - `not_connected`
  - `timeout`
  - `provider_error`
  - `invalid_reply`

  `detail` gives the reason in words, never the input. Cancellation is let
  through.
- **Raises `ValueError` for questions that cannot be asked**, which is the
  caller's mistake and is found before anything is sent:
  - a bad name;
  - too few or too many options or levels, or duplicate or empty ones;
  - options or levels on the wrong type;
  - a box question with no image;
  - no state.
- **Every answer is checked** against its question:
  - the type matches;
  - a choice is one of the options;
  - a score lies within the levels;
  - probabilities and box edges lie from 0 to 1.

  A missing or unusable answer makes the judgment `invalid_reply`. Answers
  to questions that were not asked are dropped.
- **`calibrated`** says the probabilities were measured on real outcomes
  (Jev) rather than guessed by a chat model.
- **`principal`** is the owner whose keys and chat model are used.
- **`node_id`, `workflow_id` and `session_id`** say where the call's usage
  row goes.

The service never imports a plugin, and never logs the input or the
questions.

## Providers

The providers live in the plugin `server/nodes/judgment/_providers/`, which
registers them with `register_judgment_provider` when it loads.

| Name | What | Key | Calibrated | Types |
|------|------|-----|------------|-------|
| `typesafe` | TypeSafe's Jev, `POST https://api.typesafe.ai/v1/systemone` | the owner's TypeSafe key | yes | choice, score, noul, box |
| `openrouter` | the same API through OpenRouter, `POST https://openrouter.ai/api/v1/systemone` | the owner's OpenRouter key | yes | choice, score, noul, box |
| `llm` | the owner's chat model, answering as JSON | none of its own | no | choice, score, noul |

**The TypeSafe key** is entered on TypeSafe's card in Connectors, under
Developer. Home's Settings and Dev's Connectors dialog show the same list
(`CredentialsBrowser`), and searching "jev" finds the card, since its
description names Jev.
- The card is an ordinary `apiKey` entry in `config/credential_providers.json`,
  drawn by the shared `ApiKeyPanel`. Its field's `help` says where keys are
  made (`console.typesafe.ai/keys`) and what Jev does.
- Its `category` is `ai`, like ElevenLabs, Deepgram and the Claude Code and
  Codex sign-ins. Its `consumer_category` is `developer`, never `ai`,
  because Home counts an `ai` card as a chat model: the Welcome guide's
  Connect step, Get started and the hire composer.
- Checking a key lists its models (`GET https://api.typesafe.ai/v1/models`,
  `TypeSafeCredential` in `nodes/judgment/_credentials.py`). That call is
  free, where a judgment would be billed. A refused key gets 401, and an
  answer without a model list fails the check.

**Jev's request and reply** follow TypeSafe's and OpenRouter's System One
references, read on 2026-11-23. Two functions in
`_providers/systemone.py`, `to_systemone` and `from_systemone`, are the only
places the API's field names appear.
- A question is `{type, instructions, criteria}`.
- An answer is `{type, noul}`, `{type, choice, confidence, probabilities}`,
  `{type, score, confidence, legend, probabilities}` or
  `{type, box, confidence}`.
- A score's probabilities come keyed by the level's index as a string. They
  are stored as a list.
- OpenRouter can answer `{type: "refusal"}`, which counts as a missing
  answer.

**Images** are sent as `assets`. Each one goes under its own placeholder,
listed in the state as `{"input": <state>, "images": [<placeholders>]}`.
- Each image is scaled uniformly so that both sides lie within the
  provider's `image_min_side` and `image_max_side`, 280 to 1568 pixels.
  The scale is uniform, so a box's fractions still hold for the original
  image.
- `image_model` (`jev-1.14.0`, `typesafe/jev-1.14`) answers when there are
  images.
- TypeSafe also needs the header `TypeSafe-Danger-Zone-Images: true`
  (`image_headers`). OpenRouter documents no such header.

**The model** is `jev-latest` at TypeSafe and `~typesafe/jev-latest` at
OpenRouter. Both point to the newest Jev.

**The chat-model fallback** uses the model Home's setup screen uses
(`resolve_llm_choice`).
- It asks once, through `employees_chat`, at temperature 0 with no SDK
  retry. It does not see images.
- Its reply is read leniently. Probabilities are clamped, missing ones
  count as 0, and they are renormalised over the question's own options or
  levels. The most likely option is the answer, and a score is the
  expected level.

## Cost

**A Jev call** is booked in `api_usage_metrics` under the provider's name
(`typesafe` or `openrouter`), operation `judge`.
- `resource_count` is the call's input tokens. They are priced at $0.042
  per million (`pricing.json` `api.typesafe` and `api.openrouter`), since
  output tokens are free.
- OpenRouter's reply carries its own charge, `usage.cost`. That charge is
  booked instead, by the same rule its chat calls use. On a
  bring-your-own-key call it is only OpenRouter's fee, so the upstream
  charge is added.

**A fallback call** is booked in `token_usage_metrics` like any model call
(`record_llm_usage`).

**Each provider books its own call** right after the reply arrives, before
its answers are read, since the call was paid for whatever they say. Like
every recorder call, the row takes the chat run and the employee job bound
in the log context.

Both Connectors list the service: TypeSafe's `usage_services` is
`typesafe`, and OpenRouter's is `openrouter`.

## Privacy

A judgment sends its input and questions to TypeSafe or OpenRouter, or to
the owner's chat model. With images, the images go too: the Mobile element
finder sends the phone's screenshot.

## Where it is used

- **The Classify node** ([card](./node-logic-flows/ai_tools/classify.md)).
  It asks choice, score and noul questions about a text, and an agent can
  call it as a tool. A judgment that is not available is a `NodeUserError`,
  and `not_connected` asks the owner to act.
- **Triage of app events** (`services/employees/triage.py`). It answers
  whether an app, webhook or polling event is worth a job for a team
  employee (recipe v3), before any model runs.
  - **The rule.** It is the "Wake me when" text kept as the team plan's
    `wake_rule`:
    - drafted at hire for an employee an app event starts (the setup card's
      field, the hire button's `wakeRule`, the request's `wake_rule`);
    - edited from the Wake button in Home's header and the editor's toolbar
      (`set_employee_wake_rule`; the summary's `wake_rule` is `null` for an
      employee no app event starts).

    `admit_team_plan` copies the plan into each generation at Start and
    Apply, so a running employee takes an edited rule on Apply.
  - **The judgment.** The agent's prepare step asks one noul right before it
    would admit the event as a job: "This event calls for the employee's
    work. The employee works on: <rule>". The state is the rendered intake
    mission, and the limit is 15 s.
  - **A drop.** Below `DROP_BELOW` (0.2) the event is dropped. The instance
    bootstrap ends the run as `event_not_matched` (success), with no job, no
    instance and no model run. The ending reuses the existing review-event
    return, so no Temporal patch is needed.
  - **Every doubt admits the event:**
    - no rule;
    - not the bootstrap of the lead for a fresh event;
    - a job already admitted for this run, so a retried prepare never drops
      what it admitted;
    - no judgment available;
    - a probability at or above the threshold.
  - **Cost.** The judgment's usage row carries the workflow, so it counts in
    the employee's monthly cost.
- **Review auto-accept** (`services/agent_instances/auto_review.py`). A
  teammate's submitted work is accepted without waking its reviewer (the
  Orchestrator, or an AI Employee managing specialists) for a model turn.
  - **The setting.** "Skip reviews when sure" in the cost panel,
    `UserSettings.review_auto_accept_percent`. It is 0 (off) by default and
    is read live.
  - **When it applies.** When the `agent.instance.claim` activity claims a
    reviewer's run that was woken only by fresh review notices of submitted
    work (`AgentInstanceService.review_candidates`).
  - **The judgment.** One per task: the noul "The result fully completes
    these instructions." It reads the task's instructions (with any review
    feedback) and its result.
    - It is asked with `calibrated_only`, so the chat-model fallback never
      accepts anything.
    - It runs inside the job's log context, so its cost counts toward the job,
      the reply that started it and the employee's monthly budget.
  - **Settling.** When every task is judged at least as sure as the setting,
    `settle_auto_review` does what the reviewer's turn would:
    - accepts each through `accept_task`, marking the task's
      `context.auto_accepted` (`{attempt, confidence}`; a model's tool input
      cannot carry the marker, because the review tools forbid extra
      fields);
    - writes one transcript line, for example `Accepted "Gather evidence"
      automatically: 95% sure it completes the instructions.`;
    - completes the run with the reviewer's accepted work as its result.

    The review gate then hands the reviewer's task on. The settled claim
    comes back `completed`, which the session loop already skips, so no
    Temporal patch is needed. The Work view shows the task as "Accepted
    automatically".
  - **Never applies to:**
    - Chat's review of the final answer (the lead's runs are never settled);
    - a batch with anything else in it;
    - no judgment available;
    - any task judged below the setting.

    Each of these leaves the review to the reviewer's turn, and so does a
    failure while settling.
- **The Mobile element finder** (`server/nodes/mobile/_locate.py`). When the
  phone engine's tap by text finds nothing in the UI tree (a canvas, a web
  view, an icon drawn without a label), the element is looked for on the
  screenshot instead.
  - **When.** The engine tries a tap by text last, after a tap at the model's
    coordinates and one by resource id. The worker's `find_element`
    (`nodes/mobile/runtime/worker.py`) asks the broker to `locate` the element
    only when a text lookup of the first match matches nothing. A lookup by
    resource id alone never does.
  - **The judgment.** The broker reads a screenshot under the run's lease,
    like any read, and then, outside the device lock, asks two questions
    about it: the noul `An element showing "<text>" is visible on this
    screen`, and a box for that element. The limit is 15 s. Only Jev can
    answer, since the chat-model fallback takes no images. The Workspace
    shows "Finding it on the screen" meanwhile.
  - **A tap.** Only a noul of at least `ON_SCREEN_AT_LEAST` (0.8) with ordered
    edges inside the image counts. The box becomes pixels with the
    screenshot's own size, the space the driver taps in, and the engine taps
    its centre, which still passes the driver's range and geometry checks.
  - **Anything else is "Element not found", as before:** no judgment, an
    unsure one, an unusable box, or any failure. None of these puts the phone
    into recovery, and a locate is not a step in the Workspace's step log.
  - **Cost.** The broker answers in the log context of whichever run started
    it, so each run keeps its own principal, chat run and employee job
    (`MobileRuntime._run_attribution`), and the judgment binds them. Its row
    counts for the phone task's reply and job.

## Tests

- `server/tests/services/judgment/test_judgment_service.py`: provider
  order, one attempt, the answer checks and the question rules.
- `server/tests/nodes/test_judgment.py`: the wire shapes, cost booking, the
  fallback's reading, the TypeSafe key check and card, and the Classify
  node.
- `server/tests/llm/test_json_reply.py`: the JSON reader the fallback and
  translate share.
- `server/tests/services/employees/test_triage.py`,
  `server/tests/services/employees/test_wake_rule.py` and
  `server/tests/temporal/test_employee_app_hierarchy.py`: the wake rule and
  triage at job admission.
- `server/tests/services/agent_instances/test_auto_review.py`: review
  auto-accept.
- `server/tests/test_mobile_locate.py`: the element finder's gates, the
  broker's `locate`, each run's attribution and the worker's fallback.
