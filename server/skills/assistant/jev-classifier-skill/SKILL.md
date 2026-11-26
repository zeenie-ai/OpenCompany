---
name: jev-classifier-skill
description: Ask yes-or-no, multiple-choice and scale questions about a text with the classify tool (TypeSafe's Jev) and get probabilities back. Use it to sort, route, tag, triage or check something when a label or a yes or no is all you need, instead of reasoning it out.
allowed-tools: classify
metadata:
  author: opencompany
  version: "1.0"
  category: judgment
---

# Jev Classifier Skill

The `classify` tool asks TypeSafe's Jev, a judgment model, typed questions
about one text. It answers with probabilities, never prose: how likely a
statement is true, which option fits, or where the text sits on a scale. It
is much cheaper and faster than working the answer out yourself, so reach for
it whenever a label or a yes or no is enough.

## When to use it

- Sorting and routing: which topic, team or folder a message belongs to.
- Yes-or-no checks: "The email asks for a refund", "The reply is polite".
- Triage: how urgent or how serious something is.
- Checking work: "The summary mentions every deadline in the text".

Not for writing text, pulling out values (names, dates, amounts), arithmetic,
or any answer that is not one of a fixed set. Read the text yourself for
those.

## How to call it

Pass `text`, everything the judgment needs, and `questions`, a map of names
to questions. The judgment sees only `text`, not this conversation, so put
in it every fact the answer depends on. Ask all your questions about one
text in a single call.

```json
{
  "text": "Hi, my invoice for March was charged twice. Can you refund one of them today?",
  "questions": {
    "refund": {"type": "noul", "description": "The sender asks for a refund."},
    "topic": {
      "type": "choice",
      "description": "What the message is about.",
      "options": ["billing", "support", "sales", "other"]
    },
    "urgency": {
      "type": "score",
      "description": "How soon it needs a reply.",
      "levels": ["can wait", "this week", "today"]
    }
  }
}
```

- **Names** are letters, digits and underscores, not starting with a digit.
  Answers come back under the same names.
- **noul** takes a statement, not a question: "The sender asks for a
  refund.", not "Does the sender want a refund?". The answer is the
  probability that it is true.
- **choice** takes 2 to 255 different options. Make them cover every case:
  add "other" when the text might fit none of them.
- **score** takes 2 to 10 different levels, lowest first.
- **box** questions (where something is in an image) are refused, because
  this tool judges text.

## Reading the answer

- A noul: `answers.<name>.probability`, from 0 to 1.
- A choice: `answers.<name>.choice`, with its `confidence` and each option's
  `probabilities`.
- A score: `answers.<name>.score`, the expected level counted from 0, with
  its `confidence` and each level's `probabilities`, lowest first. It can be
  fractional: 1.7 lies between the second and the third level.
- `calibrated: true` means Jev answered, and its probabilities are measured
  on real outcomes. `false` means the owner's chat model stood in, because no
  TypeSafe or OpenRouter key is connected. Treat its numbers as rough.

Act on clear answers: about 0.8 and above, or 0.2 and below, for a noul, and
a high `confidence` for a choice or score. In between, read the text yourself
or ask the owner, rather than taking 0.55 as a yes.

## When it fails

- "No judgment model is connected": the owner has to add a TypeSafe or
  OpenRouter key, or connect an AI model. Tell them once, and do not retry.
- "... did not answer in time": try once more, then carry on without it.
- "... answer could not be used": simplify the questions (fewer options,
  plainer wording) and try once more.

## Anti-patterns

- One call per question about the same text. Put them all in one call.
- Passing a summary or a file path instead of the text itself.
- Asking about something the text does not say. Add the missing facts to
  `text`, or answer it another way.
- Sending passwords, keys or other secrets in `text`: it leaves the machine
  for TypeSafe or OpenRouter.
