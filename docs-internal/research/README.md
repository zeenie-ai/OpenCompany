# Research

Studies and run analyses, kept beside the maintained documentation. Each one records what was found on its date. None of them describes current behaviour, and none is a decision. Where a note proposes something, the note itself says how much of it has been built.

For how OpenCompany works today, start from the documentation table in [CLAUDE.md](../../CLAUDE.md).

| Note | Written | What it covers | State on 2026-11-19 |
|---|---|---|---|
| [dynamic-workflow/](dynamic-workflow/README.md) | 2026-11-12, extended 2026-11-19 | How a dynamic workflow (a script the model writes, which runs sub-agents) could be built on the Temporal engine. Six digests of other projects, among them Claude Code's Code Mode; six maps of the code; a cross-reference; a plan | Research complete, nothing built. Six questions wait for the owner ([PLAN.md](dynamic-workflow/PLAN.md)) |
| [treg-reels-run.md](treg-reels-run.md) | 2026-11-17 | One treg search job of 2026-11-13: why it cost $2.41 of model time for $0.05 of data, and why it found 9 reels where the owner counted 11 | Two of five fixes built, one built in part |
| [three-js-weather-app-run.md](three-js-weather-app-run.md) | 2026-11-17 | One day of building a three.js site: where $10.74 of model cost went, and what in the product let it | Two of ten fixes built, four built in part |
| [cache-rewrites.md](cache-rewrites.md) | 2026-11-21 | Two sessions of 2026-11-20, one on the older agent lifecycle and one on the reusable agents: how much of what an agent writes to the prompt cache it had already written, and why | Measured only, nothing built (2026-11-21). A cache marker for the fixed prefix waits for the owner |
| [noodle/](noodle/README.md) | 2026-10-08 | Five notes from a review of the Noodle app's interface and runtime | Reference only |

## How these notes are kept

- **Dated.** A note states its date and the commit it was read at. The code moves on, so find a symbol by name before relying on a line number.
- **Marked.** One of the first twenty lines is a status line, which the docs lint reads ([docs_checks.py](../../cli/tests/docs_checks.py)):
  - `Historical` marks a dated record. From then on the lint checks only the links in the note's opening notice, so a later rename in the code does not fail the lint on an old note.
  - `Planned` and `Prototype` mark a proposal and a description of another project. The lint still checks every link in them.
- **Public.** This folder ships in the public repository: no local paths, account names or secrets.
- **Searchable.** Like every Markdown file under `docs-internal`, these notes are packed into the Agent Builder's documentation bundle ([builder_documentation.py](../../server/services/builder_documentation.py)), so its documentation search can return them.
