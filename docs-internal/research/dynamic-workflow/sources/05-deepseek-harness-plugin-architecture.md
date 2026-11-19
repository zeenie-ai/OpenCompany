# DeepSeek Harness: plugin architecture (digest)
> **Status: Prototype reference.** External source digest: this file describes DeepSeek Harness and Cordis, not OpenCompany. Paths, names and commands in it belong to those projects.

Written 2026-11-12. Nothing from the sources was executed, built or installed. Behaviour is described from reading documentation and source code. Citations are `path:lines` in the clones listed below. "DSH" means the deepseek-harness clone, "Cordis" means the upstream cordis clone, "Paper" means the arXiv paper (cited by section, definition or algorithm number). INFERRED marks a conclusion the sources do not state themselves. NOT VERIFIED marks something that could not be checked.

## Sources read

| Tag | Source | URL | Fetched | Version and commit | Licence |
|---|---|---|---|---|---|
| DSH | deepseek-ai/deepseek-harness, branch `master` | https://github.com/deepseek-ai/deepseek-harness | 2026-11-12, shallow clone (depth 1) | commit `e8f7b93a344964de3dca1ace9e72b6e4d6de87cb`, committed 2026-11-11, "Merge pull request #8278 from deepseek-harness/worktree-sync-release-dsh-0.2.2-rc.2-to-master". Root package version `0.2.2-rc.2`. | MIT, "Copyright (c) 2026 DeepSeek" (`LICENSE:1-3`) |
| Site | Documentation website | https://deepseek-harness.github.io/deepseek-harness/ | 2026-11-12 | VitePress v1.6.4 build, no version shown | Projection of the repository's `docs/` folder (repository licence: MIT) |
| Topic | GitHub topic page `dsh-plugin` | https://github.com/topics/dsh-plugin | 2026-11-12 | "30,721 public repositories matching this topic" | Not applicable |
| RepoPage | Repository landing page, text snapshot saved by the lead agent | https://github.com/deepseek-ai/deepseek-harness | snapshot file dated 2026-11-12 | 279.3k stars, 34.4k forks, 26,771 commits | Not applicable |
| Cordis | cordiverse/cordis, branch `main` | https://github.com/cordiverse/cordis | 2026-11-12, shallow clone (depth 1) | commit `261c5fdae6edb54e7ed4f071024f530acd18903b`, committed 2026-11-10, "refactor: migrate http and cli packages from their repositories (#308)". `packages/core` is the npm package `cordis` at `4.0.0-rc.12`. | MIT, "Copyright (c) 2021-present Shigma" |
| Paper | Shi, Zhang, Cui, "A Programming Paradigm for Spatiotemporal Composability" | https://arxiv.org/abs/2608.25512 (PDF at https://arxiv.org/pdf/2608.25512) | 2026-11-12 | arXiv v1, submitted 26 Aug 2026, 92 pages, cs.PL | arXiv non-exclusive distribution licence, https://arxiv.org/licenses/nonexclusive-distrib/1.0/ |
| PaperRepo | cordiverse/paper, branch `main` | https://github.com/cordiverse/paper | 2026-11-12, shallow clone | commit `0d43a6f18004a7b5bf9662c31aa08c3712d232ec`, committed 2026-08-29. Contains only a README (abstract, citation) | No licence file |

Coverage and caveats:

- DSH, read in full: `README.md`, `SAFETY.md`, `CONTRIBUTING.md`, `AGENTS.md`, `packages/AGENTS.md`, `packages/README.md`, `LICENSE`, `vendor/README.md`, `docs/architecture.md`, `docs/development.md`, `docs/cordis-primer.md`, `docs/glossary.md`, `docs/testing.md`, `docs/rescope.md`, `docs/session-format-status.md`, `docs/agent-lifecycle.md`, `docs/tool-execution-pipeline.md`, all eight pages of `docs/cordis-tutorial/`, `docs/user/develop/basic/*`, `docs/user/develop/framework/*`, `docs/user/develop/practice/index.md` and `dynamic-cordis.md`, `docs/user/guide/plugin-market.md`, `docs/cookbook/adding-a-tool.md`, `docs/cookbook/extension-cookbook.md`, `docs/subsystems/workflow.md`, `docs/subsystems/scope.md`, the three workflow package READMEs, `packages/workflow/workflow/src/*`, `packages/workflow/workflow-ptc/src/{index,host,runtime,guest,meta,types}.ts`, `packages/workflow/tool-workflow/src/{index,types}.ts`, `packages/bundle/base/cordis.patch.yml`, `packages/boot/app-boot/README.md`, `packages/boot/plugin-manager/README.md`, `packages/preset/agent-preset/README.md`, `packages/preset/agent-preset-registry/README.md`, `packages/extensions/README.md`, `packages/extensions/tool-cordis/README.md`, `packages/ptc-runtime/README.md`, `packages/ptc-runtime/ptc-runtime/README.md`, `packages/shell/shell/src/index.ts`, `packages/subagent/subagent-spawn-in-process/src/index.ts`, `packages/guard/timeout-policy/src/index.ts`, the two Agent Notes on workflows and the capability-seams Agent Note.
- DSH, read in part: `docs/subsystems/core.md` (lines 1-260 and 373-507, plus headings), `tools.md` (1-575), `subagent.md` (1-425), `ptc-runtime.md` (1-140), `approval.md` (1-110), `sandbox.md` (1-150), `permission-presets.md` (1-70), `persistence.md` (1-120), `jobs.md` (1-66); `docs/cookbook/adding-a-package.md` (1-120); `docs/tool-catalog.md` (headings, the `workflow` and `run_code` entries); the `boot()` and `mountRootInclude()` functions of `packages/boot/app-boot/src/index.ts`; the head and tail of the web-app bundle patch and the head of its `standard` preset; the summaries of about fifteen other package READMEs.
- DSH, not read: the browser (client) packages' source, the LLM adapters' source, the source of `core/agent-loop` and `core/tools` (their documentation was read instead), `docs/subsystems/session.md`, `docs/config-catalog.md`, `docs/persistence-catalog.md`, `python/`, `native/`, `apps/desktop`, `benchmarks/`, `.agents/skills/`, and nearly all of the roughly 2,700 Markdown files under `.agents/notes/` (three were read in full, and the first 40 lines of the PTC note). In the workflow packages, `guest-source.ts`, `guest-types.ts`, `realm.ts` and `record.ts` were not read.
- Site: the index page returns HTTP 200 but its HTML body is a client-rendered shell with no text. The site publishes a raw Markdown twin of every page and an `llms.txt` index. Three raw pages (`en/reference/index.md`, `en/reference/subsystems/workflow.md`, `en/develop/basic/index.md`) were fetched and compared with `docs/architecture.md`, `docs/subsystems/workflow.md` and `docs/user/develop/basic/index.md` in the clone: identical text once link targets are ignored. The site manifest is `website/docs.ts` in the clone. The rest of the site was read through the repository's `docs/` folder on the assumption that it is projected the same way (INFERRED for the pages not compared).
- Cordis, read in full: `packages/core/README.md`, `packages/core/package.json`, `packages/core/bin.js`, `packages/core/src/{context,service,fiber,registry,reflect,events,utils}.ts`, `packages/loader/src/index.ts`, `packages/loader/src/internal.ts`, `packages/loader/src/config/{entry,group,isolate,tree}.ts`, `packages/include/src/patch.ts`, `packages/hmr/README.md`. Read in part: `packages/include/src/index.ts` (1-140). Not read: `packages/core/src/logger.ts`, `packages/loader/src/config/utils.ts`, `packages/loader/src/resolve.ts`, `packages/include/src/journal.ts`, the HMR source, the `cli`, `create`, `env`, `fs-node`, `http`, `package-manager` and `timer` packages, and all tests. The upstream repository has no `docs/` folder; every package README except `core` and `hmr` is one line.
- Paper: `https://arxiv.org/html/2608.25512` and the `v1` variant both returned HTTP 404. The PDF was converted with `pdftotext`. Prose, definitions, algorithms and tables are readable; mathematical symbols are garbled. Read: abstract, sections 1, 2, 3.1 (to Definition 8), 3.2, 3.3.1, 4.1, the start of 4.2, 5.1, 5.2.1, the start of 5.2.2, 5.3, 6.1, 6.2, 6.3. Not read: sections 3.1.3, 3.3.2 (beyond its first paragraph), 3.4, 4.2.1 to 4.4 (rules and proofs), the rest of 5.2.2, 6.4 to 6.7, 7, 8.

## 1. What it is

DeepSeek Harness, command name `dsh`, is "an open-source agent harness developed by DeepSeek AI" (DSH `README.md:5`). It is a Node.js and TypeScript monorepo that runs coding agents and ships five entry modes from one launcher: a Web UI, a one-shot headless runner, a JSON-RPC SDK server, a minimal SDK variant and an Agent Client Protocol server; an Electron desktop application wraps the Web application, and a Python SDK launches the same `dsh` command (DSH `docs/architecture.md:17`, `:43-53`; `apps/cli/README.md:9-20`). The quick start is `npx @deepseek-ai/dsh web`, which serves the Web UI at `http://127.0.0.1:3080` (DSH `README.md:24-27`). It needs Node.js 24.9 or newer and pnpm 11.28.5 for source work (DSH `docs/development.md:11-13`).

The project's own one-line description is "DeepSeek Harness: Everything is a Plugin" (DSH `README.md:65`). The architecture page states the claim this way: "Every part of the product is a plugin, including the model adapter, the tool registry, the session log, and the agent loop itself, so each is replaceable from configuration. There is no privileged core to patch: you extend dsh by mounting a plugin beside the others, and registrations are effects that unwind when their plugin unloads." (DSH `docs/architecture.md:9-11`).

It is built on Cordis, a TypeScript framework that also underlies the Koishi chatbot framework (Paper section 5.3). Cordis describes itself as "A Meta-Framework of Spatiotemporal Composability" (Cordis `packages/core/README.md:5`). Its design is described in the arXiv paper listed above, whose authors are affiliated with Peking University and DeepSeek-AI. The harness does not depend on the npm `cordis` package: it vendors a source copy under `vendor/` and republishes it under the `@deepseek-ai` scope (DSH `vendor/README.md:3-5`).

Size, counted in the clone: 53 package groups and 374 packages under `packages/`, of which 63 are in `packages/experimental/` and 71 in `packages/client/`; 9 vendored packages; the base bundle composes 99 plugin rows (DSH `packages/bundle/base/cordis.patch.yml`). The repository page snapshot shows 279.3k stars, 34.4k forks and 26,771 commits, and the `dsh-plugin` topic lists 30,721 public repositories (RepoPage; Topic).

Status: "DeepSeek Harness is in developer preview and iterating rapidly. THERE WILL BE COMPATIBILITY-BREAKING CHANGES." (DSH `README.md:13`). External pull requests are not accepted; the project asks the community to publish plugins instead and to tag their repositories with the `dsh-plugin` topic (DSH `CONTRIBUTING.md:9-15`).

## 2. Vocabulary

Cordis terms:

| Term | Meaning | Evidence |
|---|---|---|
| Plugin (DSH docs, vendored Cordis) or component (upstream Cordis `main`, Paper) | A unit of code the framework can load and unload: a function `(ctx, config)`, an object with `apply(ctx, config)`, or a class constructed with `(ctx, config)`. It may carry `name`, `Config`, `inject`, `provide`, `intercept`. | Cordis `packages/core/src/registry.ts:63-100`; DSH `vendor/cordis/src/registry.ts:92-146` |
| Context (`ctx`) | The object a plugin receives. Every registration goes through it, and every service is read from it. Contexts form a tree; each loaded plugin instance runs in its own child context. | Cordis `packages/core/src/context.ts:20-78`, `fiber.ts:150` |
| Service | A named capability stored on the context, reached as `ctx.<name>`. One provider per name per isolation realm. | Cordis `packages/core/src/service.ts:20-37`, `reflect.ts:213-232` |
| `inject` | A plugin's declared list of required services. The plugin runs only while all of them are provided by active plugins. | Cordis `registry.ts:11`, `fiber.ts:418-430` |
| Effect | A callback that makes a change and returns the function that undoes it. `ctx.effect(cb)` runs it and records the undo on the owning plugin instance. | Cordis `fiber.ts:292-357` |
| Disposer | The undo function an effect returns. | Cordis `fiber.ts:64` |
| Fiber | One loaded instance of a plugin in one context, with a lifecycle state and the list of its effects. `ctx.plugin(x)` (vendored) or `ctx.use(x)` (upstream) returns it. | Cordis `fiber.ts:115-220`, `registry.ts:238-252` |
| Fiber state | `PENDING`, `LOADING`, `ACTIVE`, `FAILED`, `DISPOSED`, `UNLOADING`. | Cordis `fiber.ts:90-97` |
| Event | A named, typed hook. Five dispatch modes: `emit`, `parallel`, `serial`, `bail`, `waterfall`. | Cordis `events.ts:14`, `:110-163` |
| Loader | The plugin that turns a list of configuration entries into fibers and keeps them in step with the list. | Cordis `packages/loader/src/index.ts:48-173` |
| Entry, also called a row | One element of the configuration list: `id`, `name` (module specifier), `config`, `disabled`, `inject`, `intercept`, `isolate`, `group`. | Cordis `packages/loader/src/config/entry.ts:8-16`, `isolate.ts:6-9` |
| Include | The plugin that loads a YAML or JSON entry list from a file and applies patches to it. | Cordis `packages/include/src/index.ts:113-121` |
| Group | An entry whose `config` is itself a list of entries; they load and unload as one unit. | Cordis `packages/loader/src/config/group.ts:107-120` |
| Realm, `isolate` | A per-key namespace for service bindings. `isolate: { shell: true }` on an entry gives it a private `shell` binding. | Cordis `isolate.ts:58-76`; DSH `docs/user/develop/framework/service.md:113-139` |
| Intercept | Metadata attached to a service name on a context, read by the service when it is used. | Cordis `context.ts:71-77`, `service.ts:57-70` |
| HMR | Hot module replacement: reload a changed plugin module in a running process. | Cordis `packages/hmr/README.md`; DSH `packages/boot/hmr/README.md:12` |

Harness terms:

| Term | Meaning | Evidence |
|---|---|---|
| `cordis.yml`, `cordis.patch.yml` | The entry list, and a patch file that inserts or overrides entries by `id`. | DSH `docs/architecture.md:25` |
| Bundle | "A distribution format for Cordis config rows and the code they mount": an npm package whose `package.json` declares `dsh.bundle.patch`. | DSH `docs/architecture.md:19-21` |
| Profile | A named composition under `$DSH_HOME/profiles/<name>`: an ordered list of bundles (`dsh.profile.bundles`), installed out-of-tree plugins, and the user's own `cordis.patch.yml`. Shipped templates: `web`, `headless`, `sdk`, `sdk-minimal`, `acp`. | DSH `docs/architecture.md:17` |
| `$DSH_HOME` | The harness data root, default `~/.dsh`. | DSH `packages/util/home-paths/README.md:33-37` |
| Seam | "A swappable capability with three roles": Service Definition (the Cordis service that owns `ctx.<key>`), Service Provider (an implementation), Consumer (what injects it, commonly a model-facing tool). | DSH `docs/glossary.md:9`; `docs/architecture.md:138` |
| Scope, `agent.ctx` | Per-agent registration. A contribution is global or owned by exactly one agent; scoped contributions do not inherit down to subagents. A scoped tool shadows its same-named global twin. | DSH `docs/glossary.md:13-21` |
| Agent preset | A named list of child plugins that composes one session's tools, prompt sections and skills. | DSH `packages/preset/agent-preset/README.md:12`, `:40-48` |
| Session | An append-only log of typed events; the model's message history is derived from it. | DSH `docs/subsystems/core.md:417` |
| Step, turn, round, exchange | A step is one model request plus the tools it calls. A turn is zero or more steps. A round is an outer policy iteration that contains a turn. | DSH `docs/glossary.md:37-40` |
| Agent | The live handle that drives one session: `send`, `followup`, `steer`, `inject`, `cancel`, `whenIdle`, `ctx`. | DSH `docs/subsystems/core.md:61-144` |
| Tool executor | The plugin that owns parsing, scheduling and logging of a batch of model tool calls. Two ship: native and PTC. | DSH `docs/subsystems/tools.md:286-308` |
| PTC | The mode in which "the model writes TypeScript against the tool registry": the model sees one tool, `run_code`, and calls the other tools from a program. The abbreviation is not expanded in the files read. | DSH `.agents/notes/implemented/feature/2026-06-15-ptc.md:1`, `:21` |
| Subagent, activation, provider | A child agent started by an agent; one managed execution of it; a registered backend that runs it. | DSH `docs/subsystems/subagent.md:5-9`, `:353-396` |
| Workflow | A model-written JavaScript script that starts subagents and returns a JSON value. | DSH `docs/subsystems/workflow.md:5` |
| Job | A background unit of work owned by one agent session, with an id, an output ring and a completion notice. | DSH `packages/jobs/README.md:12` |
| Human command | A slash command executed by a UI adapter without creating a model message. | DSH `docs/glossary.md:31` |

## 3. Cordis: the paradigm underneath

### 3.1 What "spatiotemporal composability" means

The paper separates two requirements for a system whose parts are loaded and unloaded while it runs (Paper section 1.1):

- Temporal composability, the time dimension: when a component is removed, everything it changed in the shared environment is reversed, completely and in order.
- Spatial composability, the space dimension: components declare what they need from one another, and the system starts and stops them as those needs become satisfied or unsatisfied.

In plain terms: a part can leave without leaving anything behind, and a part can arrive or depart without the others having to know about it in advance.

The paper builds each property from a known idea in programming-language theory and moves it from compile time to run time:

- A revertible effect is a context change that returns its own inverse. The paper types it as a function from a context to the changed context plus an inverse function, and proves that tracking such effects and then applying the accumulated inverses recovers the initial context (Paper section 3.1, Definitions 2, 3, 6 and 8, Theorems 4, 5 and 7).
- A reactive coeffect is a declared dependency. The context holds a table of provided keys. A component declares a set of required keys, the "coeffect specification". Every change to the table is classified against that set as activating, deactivating or neutral, and the classification drives the component's activation and deactivation (Paper section 3.2, Definitions 19 to 22).
- The "context paradigm" merges both into one context type through which every interaction between a component and its environment passes (Paper section 3.3.1, Definition 28). Providing a dependency is itself an effect, so it is reverted like any other: "coeffect operations are effects, and effects are revertible" (Paper section 3.2.1).
- A component is a triple: what it requires, what it provides, and an effect function. A fiber is one instantiation of a component with its own lifecycle state (Paper section 4.1, Definitions 48 and 49). The paper gives nine transition rules and proves that the two properties hold for a whole system of interleaved fibers, not only for one (Paper sections 4.2 and 4.3; proofs not read).

The paper's motivation names agent harnesses directly: "A future harness may generate and deploy modifications to its own components while continuously serving requests." It argues that without temporal composability each self-modification forces a restart, and without spatial composability each module must detect changes in its dependencies by ad hoc means (Paper section 1.2.2). Its evidence is one case study: Koishi, a chatbot framework on Cordis v3 with over 4,000 community plugins. The paper states the limits of that evidence: one ecosystem, one host language, observational, with no measurement of overhead or productivity (Paper section 5.3).

### 3.2 How the code does it

Line numbers in this subsection are from the upstream Cordis clone. The harness vendors an older snapshot with the same structure (section 11).

Context. `new Context()` creates the root context as a JavaScript `Proxy`, creates the root fiber, and installs four built-in services: `reflect`, `registry`, `events`, `logger` (Cordis `packages/core/src/context.ts:36-49`). `ctx.extend(meta)` creates a child context by prototype inheritance (`:55-63`). `ctx.isolate(name, label?)` and `ctx.intercept(name, config)` return derived contexts that change how one service name resolves or is used; they change nothing in the parent (`:65-77`).

Services. A service is an entry in a store keyed by a realm symbol. `ctx.provide(name, value)` is an effect: it adds the entry, records it on the providing fiber, and notifies dependents. Providing a name that is already provided in the same realm throws `service "<name>" has been registered at <fiber>` (Cordis `reflect.ts:213-232`). Subclassing `Service` and calling `super(ctx, 'name')` calls `provide` for the instance (Cordis `service.ts:20-37`).

Reading a service. Property access on a context goes through the proxy. If the plugin (or an ancestor fiber) declared the name in `inject` and the fiber is loaded, the value is returned from the fiber's committed view. If no fiber declares it, the access throws `cannot get property "<name>" without inject`. If it is declared but the fiber is not loaded, the access throws `cannot get required service "<name>" in inactive context` (Cordis `reflect.ts:104-135`, `:83-102`). `ctx.get(name)` is the unchecked form: it returns the value, or `undefined` when nothing active provides it (`:188-198`). The paper calls the checked form a capability request: "a component can only access dependencies it has declared; an undeclared access raises an error" (Paper section 6.3).

Effects. `ctx.effect(execute, label)` runs `execute` and collects what it returns: a disposer function, an iterable or async iterable that yields disposers, or a promise of a disposer. It returns a disposer for the whole effect and adds it to the owning fiber's list (Cordis `fiber.ts:64-76`, `:240-290`, `:292-357`). All built-in registrations are effects: `ctx.on` (Cordis `events.ts:165-173`), `ctx.provide` (`reflect.ts:214`), `ctx.use` or `ctx.plugin` (`fiber.ts:178-206`), `ctx.accessor` and `ctx.mixin` (`reflect.ts:272-308`). Creating an effect on a disposed fiber throws `CordisError('INACTIVE_EFFECT')` (`fiber.ts:231-234`).

Fibers and dependency tracking. Loading a plugin creates a `Fiber` whose own existence is an effect of the parent fiber, so unloading a parent unloads its children (Cordis `fiber.ts:178-206`). Each fiber computes an "epoch" string from its revision number and the unique ids of the service implementations its `inject` names currently resolve to. If any injected name has no implementation provided by an `ACTIVE` fiber, the epoch is `INACTIVE` (`fiber.ts:418-430`; `reflect.ts:192-198`). Whenever a service is provided or withdrawn, `notify` re-evaluates every fiber that injects that name in the same realm (`reflect.ts:251-264`). A changed epoch starts a transition (`fiber.ts:432-448`):

- Loading: the fiber commits its resolved view of dependencies, registers itself as a dependent of each provider, then runs the plugin: `new callback(ctx, config)` for a class, `callback(ctx, config)` otherwise. An exception records the error and the state becomes `FAILED`; "a failed fiber only recovers through update()" (`fiber.ts:155-168`, `:435-436`, `:450-479`).
- Unloading: the fiber first waits until none of the services it provides has a dependent left, then runs all its disposers, started in reverse order of registration and run concurrently, logging each failure separately, and then releases the providers that were waiting on it (`fiber.ts:481-518`).
- Transitions are "inertial": one runs to completion before the next begins, and if the target changed meanwhile, the fiber chains straight into the opposite transition (`fiber.ts:471-478`, `:510-517`; Paper section 5.1.3, Algorithm 5).

The identity that matters is the provider, not the value: "a fiber reloads precisely when one of its declared keys comes to be provided by a different fiber. A provider that overwrites its own binding in place is therefore not observed" (Paper section 5.1.3).

Configuration updates. `fiber.update(config)` emits `internal/commit`, stores the new config, and dispatches the `internal/update` waterfall whose default action is a restart. A plugin can listen to `internal/update` on its own context and apply the change without a restart (Cordis `fiber.ts:536-556`; `events.ts:55-79`). Config is validated through the `internal/config` waterfall against the plugin's `Config` schema; a failed validation throws `ValidationError` listing each issue with its path (`fiber.ts:16-58`; `events.ts:81-83`).

Replacement. `registry.replace(oldComponent, newComponent)` swaps the implementation behind existing fibers and restarts them in place; HMR uses it (Cordis `registry.ts:187-211`; `fiber.ts:401-416`).

### 3.3 Events

Events are declared by TypeScript declaration merging on an `Events` interface and dispatched in one of five modes (Cordis `events.ts:110-163`; DSH `docs/cordis-primer.md:19-25`):

| Mode | Behaviour |
|---|---|
| `emit` | Synchronous broadcast in registration order. Return values are ignored. A listener that throws is not caught. |
| `parallel` | All listeners run concurrently and are awaited. Failures are collected into one `AggregateError`. |
| `serial` | Listeners run in order and are awaited. The first return value other than `null`, `false` or `undefined` stops the rest and is returned. |
| `bail` | The synchronous version of `serial`. |
| `waterfall` | Around-middleware. Each listener receives the arguments plus `next()`. Calling `next()` delegates to the next listener; returning without it short-circuits. Calling `next()` twice throws. |

The harness treats the mode as part of an event's public contract and makes one rule a repository convention: "Waterfall listeners MUST call `next()` to delegate; returning without it short-circuits the chain" (DSH `AGENTS.md:137`). A listener can register with `prepend: true` to run before ordinary registrations (Cordis `events.ts:35-38`, `:165-173`). An event dispatched with a `this` argument that carries a filter reaches only listeners whose context passes the filter; the harness uses this for per-agent scoped dispatch (Cordis `events.ts:86-96`; DSH `docs/glossary.md:16-17`).

Cordis itself emits internal events that tools such as the Loader and HMR use: `internal/plugin`, `internal/replace`, `internal/status`, `internal/service`, `internal/update`, `internal/commit`, `internal/config`, `internal/get`, `internal/set`, `internal/listener`, `internal/dispatch` (Cordis `events.ts:211-224`).

### 3.4 Loader, Include, Group, isolation, HMR

The Loader holds a tree of entries and reconciles it with fibers. When an entry changes, it applies the least disruptive operation: a change of `name` or `inject` rebuilds the entry; a change of `config` is handed to the fiber's `update`; `disabled` unloads or reloads; `isolate` reassigns realms (Cordis `packages/loader/src/config/entry.ts:218-257`; Paper section 5.2.1). A group reconciles its child list by `id`: new ids are created, missing ids removed, surviving ids updated (Cordis `group.ts:80-98`). An entry without an `id` gets a random one each time the list is read (Cordis `tree.ts:112-119`), which is why the harness tutorial tells authors to give entries explicit ids: without one, any edit to the file remounts the entry (DSH `docs/cordis-tutorial/06-composition-and-hmr.md:59`).

The binding between an entry and its fiber runs both ways. When a plugin disposes its own fiber, the Loader writes `disabled: true` back to the entry; when a plugin updates its own config, the Loader writes the new config back (Cordis `packages/loader/src/index.ts:105-112`, `:128-170`).

An entry can declare `inject` in configuration, in addition to what the module declares. The Loader then creates an outer fiber that holds that `inject` and starts the plugin inside it (Cordis `entry.ts:36-46`, `:339-358`). The harness uses this to let a row wait for a service before its `!!js` config expressions are evaluated (DSH `docs/user/develop/basic/publish.md:149-159`).

Module exports. The Loader takes `exports.default ?? exports` as the plugin (Cordis `packages/loader/src/internal.ts:32-39`). A module that has both a default export and named `name`, `inject`, `Config`, `apply` exports therefore loses the named ones. The harness records a production failure caused by exactly this: a stray `export default apply` dropped the plugin's `inject`, and the first request failed with `cannot get property "agents" without inject` despite 178 passing unit tests (DSH `docs/postmortem/0001-acp-default-export-drops-inject.md:7-9`, `:25-38`).

Include patches. A patch list is applied to the entry list in order. A patch with `insert` appends entries to the root or to the group named by `id`. Any other patch needs an `id`, may assert the target's `name`, and overwrites the keys it lists; a patch that names a missing entry or a mismatched name is skipped with a warning (Cordis `packages/include/src/patch.ts:5-16`, `:136-170`).

Isolation. `isolate: { key: true }` gives the entry a local realm, private to it. `isolate: { key: 'label' }` selects a global realm shared by every entry that names the same label (Cordis `packages/loader/src/config/isolate.ts:62-76`; Paper section 5.2.1, "Managed realms").

HMR. Upstream `@cordisjs/hmr` at the cloned commit describes itself as transactional: after files change it imports the new modules and "replaces the running components in place: a fiber keeps its identity and config". Changes it cannot apply in place are reported as skipped with a reason (a module reached statically from the main entry, a native addon, ambiguous imports or exports), and "A restart applies them" (Cordis `packages/hmr/README.md`). It needs access to Node internals and replaces some Node module-loading methods. The paper notes that this HMR needs no developer-annotated acceptance boundaries, because a fiber already bounds everything its component installed (Paper section 5.2.2).

### 3.5 What the paradigm does not guarantee

- The runtime does not check that a disposer really undoes its effect: "that the inverse reverts the effect it accompanies is an obligation on the component author rather than a property the runtime verifies" (Paper section 5.1.1).
- Reversal covers only what the system controls exclusively. Data that has left the system (bytes written to a shared file, a message sent on the network) cannot be reverted; the paper names two workarounds, withholding output until it is certain and compensation actions (Paper section 6.1).
- It is not a sandbox. "Cordis supports the first through dependency declarations and interception; the second requires an external sandbox." Language-level access control "is insufficient, since a malicious component with access to the host runtime can reach the underlying objects directly" (Paper section 6.3).
- A provider that mutates its own service value in place is not observed by dependents (Paper section 5.1.3).
- A plugin whose required service never appears stays `PENDING` forever with no error (DSH `docs/cordis-tutorial/06-composition-and-hmr.md:63`).

## 4. The plugin contract

### 4.1 Declaration

A harness plugin is a TypeScript or JavaScript ES module. The documentation shows three forms (DSH `docs/user/develop/basic/index.md:17-29`, `:107-136`; `docs/cordis-tutorial/01-first-plugin.md:55-77`):

- Function form: named exports `name`, optional `inject`, optional `Config`, and `apply(ctx, config)`. This is the common form.
- Object form: a default-exported object with `name`, `inject` and `apply`.
- Class form: a default-exported class that extends `Service` and calls `super(ctx, 'serviceName')`; `static inject` and `static Config` declare dependencies and schema. Used when the plugin provides a service.

The repository rule for its own packages: "service packages default-export their service class; function plugins named-export `name` / `inject` / `Config` / `apply` and have no default export. Mixing the forms makes the Loader discard the function plugin's namespace" (DSH `packages/AGENTS.md:5`).

There is no separate manifest for a single plugin: the module's exports are the contract. Package-level metadata lives in `package.json`:

| `package.json` field | Meaning | Evidence |
|---|---|---|
| `dsh.bundle.patch` | Path, or ordered list of paths, of the patch file(s) the package contributes. Makes the package a bundle. | DSH `docs/user/develop/basic/publish.md:35-44`, `:64` |
| `dsh.bundle.platforms` | Optional list of Node platform names (plus `wsl`) on which the bundle applies. | DSH `packages/boot/app-boot/README.md:60` |
| `dsh.profile.bundles` | In a profile directory's manifest: the ordered bundle list. | DSH `docs/user/develop/basic/publish.md:87-102` |
| `dsh.client` | Marks a package that has a browser half; the Host scans loaded entries for it and serves the bundle to the page. | DSH `docs/subsystems/client-modules.md:5`; `docs/cookbook/adding-a-package.md:38` |
| `engines.dsh` | SemVer range of harness versions the bundle supports. | DSH `docs/user/develop/basic/publish.md:66` |
| `peerDependencies` on `@deepseek-ai/cordis` and `@deepseek-ai/dsh-*` | Packages whose instances must be shared with the host are declared as peers (and as devDependencies for type checking). | DSH `docs/user/develop/basic/publish.md:105`; `AGENTS.md:132` |
| `locale/en.json` with `meta.title`, `meta.description`, `meta.detail`; `icon` | Optional display metadata for the Plugins page. | DSH `docs/cookbook/adding-a-package.md:17-18`, `:114-120` |

### 4.2 Configuration schema

A plugin exports a TypeScript `Config` type and a runtime schema of the same name. The harness uses Schemastery (vendored); Cordis accepts any Standard Schema validator, "so a plain object exported as `Config` will not work" (DSH `docs/cordis-tutorial/05-config.md:34`). The schema validates the entry's `config` and fills defaults before `apply` runs. Invalid config fails the load: the fiber becomes `FAILED` with a `ValidationError` that names each path (DSH `docs/cordis-tutorial/05-config.md:64-68`).

Two harness rules follow from this (DSH `AGENTS.md:144-145`): "No hardcoded tunables in plugins: deployment-varying choices are validated `Config` fields changeable from cordis.yml", and "Misconfiguration fails loud at load when self-contained, otherwise at the earliest resolvable point; never silently skip a missing referent."

Volatile fields. A schema field marked `.volatile()` can change without remounting the plugin. The plugin holds a reference and reads it with `.get()` at each use; the Loader commits the new value in place and emits `loader/volatile-update`. A change to any ordinary field still goes through the normal update flow (DSH `docs/cordis-tutorial/05-config.md:73-95`). This is a harness addition to the vendored code (DSH `vendor/README.md:45`).

Computed values. A `!!js` YAML tag marks a JavaScript expression evaluated at load time against the plugin's context, allowed only under `config` and in an entry's `disabled` field (DSH `docs/cordis-primer.md:39`; `AGENTS.md:128`). Examples from the base bundle: `disabled: !!js process.platform === 'win32'` and `root: !!js dshHomePath('sessions')` (DSH `packages/bundle/base/cordis.patch.yml:251`, `:141`).

### 4.3 Declared dependencies

- Required: `export const inject = ['tools']`. "Cordis holds the plugin in PENDING until every listed service exists, so inside `apply`, `ctx.greeter` is guaranteed ready." (DSH `docs/cordis-tutorial/03-services.md:59`).
- Optional: no `inject`; probe with `ctx.get('name')` at the use site (DSH `docs/cordis-tutorial/03-services.md:84-90`). The harness reserves `ctx.<name>` for declared injections (DSH `packages/AGENTS.md:6`).
- Provided: a `Service` subclass claims one name. Names live in one flat namespace per application, so authors are told to prefix their own (DSH `docs/cordis-tutorial/03-services.md:94`).
- Types: `declare module '@deepseek-ai/cordis' { interface Context { greeter: GreeterService } }` adds the property type; a second merge on `interface Events` declares event names and signatures. Neither generates runtime code (DSH `docs/cordis-tutorial/03-services.md:37-42`; `04-events.md:44`).

The dependency rule between harness packages: "Extension plugins depend on Service Definitions, never concrete providers." (DSH `packages/README.md:99`).

### 4.4 Discovery and loading

There is no directory scan and no global plugin registry in the runtime. A plugin is loaded because a configuration row names its module (DSH `docs/cordis-tutorial/01-first-plugin.md:27-31`). The ways a row gets into the composition:

1. Bundles. A profile lists bundles; each bundle's patch inserts rows. `@deepseek-ai/dsh-base` is the shared first layer of the `web`, `headless`, `sdk` and `acp` profiles; `dsh-web-app`, `dsh-headless`, `dsh-sdk-app` and `dsh-acp-app` add one surface each; `dsh-sdk-minimal` is a standalone tree that does not apply the base (DSH `docs/architecture.md:23`).
2. The profile's own `cordis.patch.yml`, then the home-level `$DSH_HOME/cordis.patch.yml`, then `--patch <file>` overlays on the command line (DSH `docs/user/develop/basic/publish.md:120-127`).
3. `dsh plugin --profile <name> add <spec>`, which forwards to pnpm in the profile directory. If the installed package declares `dsh.bundle`, the launcher appends it to `dsh.profile.bundles`. The spec can be an npm name, a local directory, a tarball, or a git host address such as `github:you/hello-plugin` (DSH `docs/user/develop/basic/publish.md:79-103`, `:161-186`).
4. Plugin Manager, a plugin that offers the same operations from the Web sidebar's Plugins page and as the model-facing `plugin_manager` tool: enable or disable rows, select bundles, install or remove bundles (DSH `packages/boot/plugin-manager/README.md:14`, `:31`).
5. Plugin indexes, an experimental, default-off bundle. An index is one JSON file (`"format": 1`) listing installable bundles with versions and sources (npm, or git with a 40-character commit). The user adds index URLs; "DSH ships no index source." Installing from an index goes through the normal Plugin Manager flow after a confirmation (DSH `docs/user/guide/plugin-market.md:7`, `:23`, `:30`, `:45-80`).
6. The `dsh-plugin` GitHub topic, for discoverability only. It is a convention in the README and the contributing guide, not something the software reads (DSH `README.md:46`; `CONTRIBUTING.md:15`). The topic page describes a plugin as something that "typically ships a host bundle plus an optional client section, and registers into the harness through Cordis" (Topic).

Layer order. The effective configuration starts from an empty list and applies, in order: each bundle patch in `dsh.profile.bundles` order, the profile's `cordis.patch.yml`, the home-level `cordis.patch.yml`, each `--patch` overlay. "Later layers win per row, and a patch replaces a row's entire `config` value rather than deep-merging keys." (DSH `docs/user/develop/basic/publish.md:120-134`). `dsh --profile web --dump-config` prints the composed tree without booting (DSH `docs/architecture.md:31-37`).

Boot. The launcher's boot function creates a root `Context`, provides one helper (`dshHomePath`), mounts the Loader, and creates one root Include entry (id `include`, name `cordis:include`) with the configuration path and the prepared patches. Everything else comes from the rows (DSH `packages/boot/app-boot/src/index.ts:1227-1256`, `:789-799`).

Module resolution. Before mounting rows, the launcher computes one runtime resolution table from the installation and the ordered bundle dependency graphs and installs it on Node's ESM and CommonJS resolvers, so a plugin's peer imports resolve to the host's copies (DSH `packages/boot/app-boot/README.md:71`).

### 4.5 Load order and dependency resolution

There is no load order. "Entries start concurrently, so list position guarantees nothing about which plugin loads first; ordering comes from service dependencies" (DSH `docs/cordis-tutorial/01-first-plugin.md:31`). The base bundle repeats it: "Row order carries no load semantics (activation is service-availability driven); the grouping is for readers." (DSH `packages/bundle/base/cordis.patch.yml:12-13`). Modules are imported concurrently; a dependency constrains when a fiber activates, not when its module is fetched (Paper section 5.2.1).

The resolution rule is the one in section 3.2: a fiber activates when every name in its `inject` is provided by an active fiber in its realm. There is no cycle analysis ahead of time; the paper says the provider graph "is traversed on demand rather than analyzed in advance" (Paper section 5.1.3). What happens with a dependency cycle between two plugins is NOT VERIFIED (the paper's section 6.5 on mutual dependencies was not read); from the activation rule, both would stay `PENDING` (INFERRED).

### 4.6 Version compatibility

Before a profile imports a plugin, the launcher checks the package's declared harness requirement against the running version: `engines.dsh` if present, otherwise every `peerDependencies` range on `@deepseek-ai/dsh` and `@deepseek-ai/dsh-*`. An incompatible plugin row is disabled in the launcher's copy of the composition and reported; an incompatible bundle is skipped. A user can grant an exact-version exemption (`package@version` mapped to exact harness versions) stored in the profile's `compatibility.json`; neither a plugin upgrade nor a harness upgrade inherits it. The README states: "These checks are not a sandbox against malicious package code." (DSH `packages/boot/app-boot/README.md:54-58`).

## 5. Lifecycle and disposal

States. The harness documents the fiber state machine as follows (DSH `docs/user/develop/framework/index.md:11-24`; arrows redrawn in ASCII):

```text
PENDING -> LOADING -> ACTIVE
                   \-> FAILED
ACTIVE -> UNLOADING -> DISPOSED
```

`PENDING`: declared, a required service is missing. `LOADING`: `apply` is running. `ACTIVE`: running. `FAILED`: `apply` or config validation threw. `UNLOADING`: disposers are running. `DISPOSED`: fully torn down.

What triggers an unload. "A Cordis plugin can be unloaded by a config edit, hot reload, explicit disposal, or loss of a required service." (DSH `docs/cordis-tutorial/02-lifecycle-and-effects.md:5`).

What is undone. Everything registered through the context: event listeners, child plugins, service registrations, and harness registry entries such as tools and LLM adapters, whose `register()` returns a disposer attached to the calling plugin (DSH `docs/user/develop/framework/index.md:57-61`; `AGENTS.md:134`). A resource Cordis does not manage (a timer, a connection, a watcher) must be acquired inside `ctx.effect()` with a returned disposer (DSH `docs/cordis-tutorial/02-lifecycle-and-effects.md:9-27`).

Ordering. "disposers start in reverse registration order, but multiple async disposers run concurrently. If teardown steps must run in sequence, keep them in one disposer and await them there." (DSH `docs/cordis-tutorial/02-lifecycle-and-effects.md:94`). `fiber.dispose()` resolves after all cleanup, including async disposers, and recursively unloads child plugins (`:66`).

When a service goes away. "If a required service disappears while the app runs — its provider was unloaded or hot-replaced — every dependent plugin is unloaded too, and loads again when the service returns." (DSH `docs/cordis-tutorial/03-services.md:76`). The order is: the provider leaves `ACTIVE` first and stops counting as a provider; its dependents are notified and unload while the provider's services are still readable; only then do the provider's own disposers run (Cordis `fiber.ts:481-488`; `reflect.ts:233-247`; Paper section 5.1.3). This is also how a provider is swapped from configuration: "unload the `dsh-bash-local` entry, mount a different `shell` provider, and every plugin injecting `'shell'` cleanly restarts against the new implementation" (DSH `docs/cordis-tutorial/03-services.md:78`).

The harness hardened three disposal cases in its vendored copy: an unload that starts from inside an effect's setup waits for the setup and its cleanups; async cleanup stays visible to the owner until it finishes; creating an effect while the owner is `UNLOADING` is rejected, so a cleanup cannot register something that escapes the unload (DSH `vendor/README.md:36`).

Reload at run time:

- Config edit. With the `dsh-hmr` plugin enabled, it watches the profile manifest and the profile and home patch files, re-reads the ordered bundle layers and recomposes. The Loader diffs entries by `id` and mounts, unmounts or reconfigures only what changed (DSH `packages/boot/app-boot/README.md:67`; `docs/cordis-tutorial/06-composition-and-hmr.md:59`). Without HMR, changes apply on restart.
- Source edit. HMR also watches module roots when `root` is set. The base bundle enables HMR with `root: []` (configuration only); the headless, SDK and ACP bundles disable it; a profile patch can set `root: ["."]` (DSH `packages/boot/hmr/README.md:28-36`; `packages/bundle/base/cordis.patch.yml:27-32`). The tutorial describes the effect of a save as: "The old instance unloaded (all its effects unwound), the new code loaded, `apply` ran again." (DSH `docs/cordis-tutorial/06-composition-and-hmr.md:59`).
- Package install or removal through Plugin Manager applies immediately with HMR, and reports `restart-required` without it (DSH `packages/boot/plugin-manager/README.md:14`, `:31`).
- For development, `pnpm run dev:web` builds, serves and rebuilds client bundles on source edits (DSH `README.md:57`).

Every registry in the repository must have a test that disposes the contributing fiber and observes the removal (DSH `packages/AGENTS.md:19`; `docs/testing.md:9`).

Failure handling at startup and reload (DSH `packages/boot/app-boot/README.md:45`, `:102-115`):

| Failure | Optional entry at startup | Required entry at startup | Later config reload |
|---|---|---|---|
| Module import fails, `apply` throws, config schema fails, `!!js` throws | Warn and continue | Stop startup | Report; keep working siblings; a correction can activate |
| An injected service is unavailable | Warn; the entry keeps waiting | Stop startup | Keep waiting |
| Malformed root config or required overlay | Stop startup | Stop startup | Reject the edit; keep the running configuration |
| Unhandled promise rejection or uncaught exception anywhere in the process | Fatal: dispose the app and exit nonzero | Fatal | Fatal |

The global required list is `agent-loop`, `tool-executor-native`, `tool-executor-ptc`, `webserver`, `web-runtime`, `modules`, `connection`, `headless-runner`, `acp`, `sdk-jsonrpc-server`; required ids that are absent or disabled do not count (DSH `packages/boot/app-boot/README.md:45`).

## 6. What is a plugin in dsh

### 6.1 What is not a plugin

From the code and layout (the list as a whole is INFERRED; each item is cited):

- The vendored framework under `vendor/`: `cordis` (core), `loader`, `include`, `group`, `hmr`, `timer`, `logger-console`, `schemastery`, `cosmokit` (DSH `vendor/README.md:11-21`). Of these, the Loader, Include, Group, HMR, timer and console logger are themselves Cordis plugins.
- The launcher `apps/cli` (the `dsh` bin) and the boot library `packages/boot/app-boot`, which load environment layers, compose profile layers, install module resolution, and call the boot function described in section 4.4 (DSH `apps/cli/README.md:5`; `packages/boot/app-boot/README.md:12`).
- Library packages that are imported directly and never mounted: `packages/util/*` ("Use it as a direct library dependency, not through `cordis.yml`", DSH `packages/util/home-paths/README.md:12`) and `packages/core/scope` ("library, no key", DSH `docs/architecture.md:64`).
- The native addon under `native/`, the Python SDK under `python/`, and `packages/test-support/*`.

### 6.2 Feature map

Each row below is one or more plugin packages mounted from configuration. Package names are `@deepseek-ai/dsh-<name>`; folders are `packages/<group>/<pkg>/` (DSH `packages/README.md:29-83`; `docs/architecture.md:57-65`; rows in `packages/bundle/base/cordis.patch.yml`).

| Feature | Packages (group and members) | Service key | Notes |
|---|---|---|---|
| Model providers | `llm/llm` (adapter registry), `llm-deepseek`, `llm-deepseek-api-key`, `llm-deepseek-account`, `llm-pi-ai` (multi-provider), `llm-retry`, `token-meter` | `ctx.llm` | To add a model provider, a plugin registers its adapter on `ctx.llm` (DSH `docs/architecture.md:150`) |
| Session log | `core/session`; `session/session-persistence`, `session-persistence-jsonl`, `session-projection`, format migration packages | `ctx.sessions`, `ctx.sessionPersistence` | Append-only event log |
| Prompt assembly | `core/system-prompt`; `context/*` (workspace instructions, time, references) | `ctx.systemPrompt` | Plugins add sections with `ctx.systemPrompt.section()` |
| Tool registry and pipeline | `core/tools` | `ctx.tools` | Scoped registry, guarded execution |
| Tool executors | `core/tool-executor-native`, `core/tool-executor-ptc` | (selected per scope) | Native function calling, or `run_code` programs |
| Agent interface and registry | `core/agent` | `ctx.agents` | The `Agent` type and `agent/*` events |
| The agent loop | `core/agent-loop` | `ctx.agentLoop` | "the default driver"; "`dsh-agent-loop` is swappable; UI, hook, and tool plugins use `dsh-agent`" (DSH `packages/README.md:99`) |
| Shell, terminal, subprocess | `shell/*` (definition, local and sandboxed bash and PowerShell providers, tools), `terminal/*`, `subprocess/*`, `ssh/*` | `ctx.shell`, `ctx.terminals`, `ctx.subprocess` | The canonical three-role seam |
| Filesystem, search, LSP, web | `fs/*`, `lsp/*`, `web/*` | `ctx.fs`, `ctx.lsp`, `ctx.web` | Providers plus model-facing tools |
| Process sandbox | `sandbox/sandbox`, `sandbox-local`, `sandbox-policy`, `sandbox-windows-acl` | `ctx.sandbox`, `ctx.sandboxPolicy` | bwrap or Landlock, Seatbelt, Windows ACL |
| Approval and permissions | `interaction/user-approval`, `interaction/permission-presets`, `interaction/tool-ask-user`, `interaction/user-questions` | `ctx.approval`, `ctx.permissionPresets` | Section 8 |
| Human commands | `interaction/commands` plus `command-*` plugins in feature groups | `ctx.commands` | Slash commands, no model turn |
| Subagents | `subagent/subagent`, six provider packages, `tool-subagent`, `tool-subagent-control` | `ctx.subagents` | Section 7.4 |
| Teams | `team/agent-team`, `team/tool-agent-team` | `ctx.agentTeams` | Shared task board |
| Workflows | `workflow/workflow`, `workflow-ptc`, `tool-workflow` | `ctx.workflowEngine` | Section 7.6 |
| Code execution | `ptc-runtime/ptc-runtime`, `ptc-runtime-node`; experimental Python backend | `ctx.ptcRuntime` | Section 7.5 |
| Background jobs | `jobs/jobs`, `jobs-local`, `tool-jobs` | `ctx.jobs` | Section 7.7 |
| Goals, plan mode, todo | `goal/*`, `plan/plan-mode`, `todo/tool-todo` | `ctx.goals` | |
| Scheduling | `schedule/schedule`, `schedule/tool-schedule` | `ctx.schedule` | Durable recurring tasks |
| Skills | `skill/skill`, `skill-filesystem`, `skill-office`, `tool-skill` | `ctx.skills` | Instructions loaded on demand |
| MCP | `mcp/mcp-client`, `mcp-manager`, `mcp-resources` | `ctx.mcpResources` | One plugin instance per server; it discovers the server's tools and registers each with `ctx.tools.register()` (DSH `docs/cookbook/extension-cookbook.md:124`) |
| Context compaction | `compaction/*` | `ctx.compaction` | |
| Loop guards | `guard/repeat-tool-reminder`, `guard/timeout-policy` | none | Listeners on `tools/*` events |
| Storage, settings, credentials | `storage/*`, `settings/settings`, `credentials/*`, `attachment/*`, `spill/*` | `ctx.settings` and others | |
| Web UI, Host half | `host/*` (web server, static frontend, directory picker, plugin inventory) | | |
| Web UI, browser half | `client/*`, 71 packages, mostly `ui-*` plugins and slots | browser-side context | Declared with `dsh.client` |
| Remote API between page and Host | `api/*`, `typert/*` | `ctx.typert` and others | Services mark methods with `@Remote` |
| SDK and ACP | `sdk/*`, `acp/acp` | | JSON-RPC over stdio |
| Per-session composition | `preset/agent-preset`, `agent-preset-registry`, `persona` | `ctx.agentPresets` | Section 8.2 |
| Plugin management | `boot/plugin-manager`, `plugin-market`, `hmr`, `config-editor`, `cmdline` | `ctx.pluginManager`, `ctx.hmr` | |
| Runtime inspection | `extensions/*` | `ctx.cordisInspect`, `ctx.dynamicCordisRunner` | Section 7.8 |
| Telemetry | `telemetry/otel`, `session/session-telemetry*` | | |
| Application surfaces | `bundle/*` | | Patch files plus glue plugins |
| Experimental | `experimental/*`, 63 packages: hook bridges for Claude Code and Codex hook files, webhooks, Ralph, voice input, session search, live sharing, browser and computer use drivers, and more | | "not a product-support commitment" (DSH `packages/experimental/README.md:26`) |

The extension cookbook gives the same map from the other side, as a table from product feature to plugin mechanism, and states: "Every product feature maps to a listener on a documented extension point", and "No row modifies the loop." (DSH `docs/cookbook/extension-cookbook.md:98-132`).

### 6.3 Extension points

How one plugin extends another (DSH `docs/architecture.md:67-79`, `:146-169`; `docs/cordis-primer.md:43`):

- Services: call another plugin's public methods. "Prefer service methods for direct capability calls."
- Registries on a service: `ctx.tools.register()`, `ctx.llm.registerAdapter()`, `ctx.subagents.registerProvider()`, `ctx.commands`, `ctx.jobs`, `ctx.systemPrompt.section()`. Each returns or attaches a disposer.
- Events in three domains. Session events are durable facts appended to the log and broadcast through `session/event`. Agent events (`agent/*`) carry a live agent and let a plugin observe or intercept work in flight. Capability events (`fs/*`, `tools/*`, `telemetry/*`) attach policy to a seam. "Prefer events for interception and policy."
- Waterfall events as middleware: `agent/pre-step`, `agent/request`, `agent/request-error`, `llm/stream`, `tools/pre-execute`, `tools/execute`, `tools/post-execute`, `approval/request`, `system-prompt/assemble`.
- Merge-extensible type maps: `SessionEventMap`, `ContentBlockMap`, `MessageSourceMap`, `JobKindMap` and others are interfaces a plugin extends by declaration merging to add a variant without editing the owning package (DSH `docs/subsystems/core.md:433-463`).
- Seams: replace a provider from configuration without touching consumers.
- Scopes: register through `agent.ctx` to affect one agent only.
- Browser slots: a client plugin injects UI into typed slots (DSH `docs/subsystems/README.md:69`).

Conflicts:

- Two providers of one service name in one realm: the second throws at load (Cordis `reflect.ts:225-227`; DSH `vendor/cordis/src/reflect.ts:304`). Seams with one engine per context rely on this; the shell definition calls it "cordis' standard duplicate-service behavior" (DSH `packages/shell/shell/src/index.ts:38-40`). The answer for several providers is either isolation realms or a registry service with named members (Paper section 6.2).
- Two tools with one name in one scope: `tool "<name>" is already registered in this scope` (DSH `packages/core/tools/src/index.ts:69-70`). A tool registered through `agent.ctx` shadows the global one for that agent.
- Two configuration layers that touch one row: the later layer wins and replaces the whole `config` (section 4.4).
- Waterfall listeners: order is registration order unless `prepend: true`; for tool policy the harness adds monotonic guards, which can only deny, "so a later listener cannot undo it" (DSH `docs/subsystems/tools.md:458`).

Isolation between plugins: all Host plugins run in one Node process and share one object graph. The repository rule is "Trust TypeScript at typed same-process boundaries" (DSH `AGENTS.md:147`), and provider interfaces are documented as "trusted same-process implementations" (DSH `docs/subsystems/subagent.md:346`). Isolation realms separate service bindings, not code. What runs outside the Host process: shell commands, PTC programs and workflow scripts (sandboxed child Node processes), out-of-process subagent providers (ACP, Codex, Claude Code, another harness through the SDK), MCP servers, and the browser half of UI plugins.

Error containment: a failed `apply` marks one fiber `FAILED` and leaves siblings running; a failing disposer is logged and does not stop the others (Cordis `fiber.ts:492-502`); a throwing tool becomes an error result and does not end the turn (DSH `docs/subsystems/tools.md:574`); observer events that must not affect the producer are dispatched with a per-listener try and catch (for example `workflow/*`, DSH `packages/workflow/workflow/src/index.ts:175-186`). An unhandled rejection or uncaught exception from detached work is fatal for the whole process (section 5).

## 7. Tools, the agent loop, sub-agents and orchestration

### 7.1 Tools

A tool is contributed by calling `ctx.tools.register(definition, options?)`, which returns the disposer (DSH `docs/subsystems/tools.md:662`). First-party tools build the definition with `defineTool`, which takes (DSH `docs/cookbook/adding-a-tool.md:9-49`):

- `name`, `description`: what the model sees.
- `parameters`: a typed schema DSL; each property is a value spec with optional `required: true`. `defineTool` converts it to JSON Schema, infers the TypeScript type of `args`, and validates model-supplied arguments before `execute` runs.
- `output.schema` and `output.render(args, value)`: the tool body returns one canonical JSON value declared by the schema; `render` separately produces the content the model reads. `output.presentationMeta(args, value)` optionally derives replayable data for a UI card.
- `execute(args, exec)`: `exec` carries `callId`, `rootCallId`, `name`, `agent`, `signal`, an optional `parent` token, and the methods `deferContext()` and `concludeTurn()` (DSH `docs/subsystems/tools.md:326-385`).
- Optional `timeoutMs`, `isConcurrencySafe(args)`, `presentCall`, `presentResult`, `projectContent`, `finalizeContent` (DSH `docs/subsystems/tools.md:27-103`).

Exposure to the model: "Schemas flow into the system-prompt assembly automatically." The registry builds the model-facing schema list from an explicit allowlist of fields, so execution callbacks never reach a request (DSH `docs/cookbook/adding-a-tool.md:39`; `docs/subsystems/tools.md:11`). Raw JSON Schema definitions are also accepted; "that is how MCP-sourced tools arrive" (DSH `docs/cookbook/extension-cookbook.md:9`).

Per-agent control: `ctx.tools.restrict({ allow, deny })` filters the tools a scope inherits; registrations made through that scope stay exempt. A restricted tool "is absent from the prompt AND refuses execution" (DSH `docs/subsystems/tools.md:165-177`; `docs/glossary.md:19`). A registration can carry a `when(context)` condition that the loop evaluates once per proposed step with the agent and the selected model (DSH `docs/subsystems/tools.md:182-207`).

Tool guidance for the model ships with the tool plugin as its own prompt section, not in the deployment persona (DSH `packages/workflow/tool-workflow/src/index.ts:324-330`).

Long-running tools: a tool exposes `run_in_background`, registers the work with `ctx.jobs.start({ kind, label, owner, run })`, and returns a typed handle such as `{ kind: 'background', jobId }` (DSH `docs/cookbook/adding-a-tool.md:52-56`).

### 7.2 The tool execution pipeline

For each call (DSH `docs/tool-execution-pipeline.md:6-64`; `docs/subsystems/tools.md:541-574`):

1. `tool/call` is logged before execution.
2. `tools/pre-execute`, a waterfall. A listener returns `{ kind: 'allow' }`, `{ kind: 'deny', reason }`, `{ kind: 'cancel' }` or `{ kind: 'ask', reason? }`. Arguments cannot be rewritten "because history, audit, UI, and execution must agree".
3. On `ask`, `ctx.approval` is consulted. Only `allowed-once` proceeds.
4. Registered guards. A guard returns a denial or nothing; it cannot allow.
5. `tools/execute`, a waterfall around the tool body. Used for deadlines, retries and metrics. A wrapper may replace `exec.signal` for its lifetime.
6. The tool body.
7. `tools/post-execute`, a waterfall. A listener accepts, replaces the content or the value, attaches context for the next request, or blocks the result with corrective feedback.
8. `tools/result`, an `emit` to observers of the frozen final outcome.
9. `tool/result` is logged.

### 7.3 The agent loop

The loop is the plugin `core/agent-loop`. It "creates fresh agents or resumes persisted sessions, then drives each turn through model requests, streamed responses, tool execution, and durable session history". Its own README says to write a custom `Agent` implementation "only when the standard 'call model, run tools, repeat' lifecycle is insufficient" (DSH `packages/core/agent-loop/README.md:12`). The loop registers a factory with `ctx.agents.setFactory()`, so other plugins create and drive agents through `ctx.agents` without importing the loop (DSH `docs/subsystems/core.md:51`).

One turn, condensed and reworded from the turn-flow listing (DSH `docs/architecture.md:83-106`):

```text
turn/start
  claim next-step input plus one queued message
  snapshot the session's model selection; assemble prompt sections and step-admitted tool schemas
  agent/pre-step (waterfall): reject, or enter with a message batch
     step/start
     agent/request, then prepare the model call
     append system and user messages to the log; derive and freeze model history from the log
     stream the call through llm/stream; publish agent/assistant-stream frames
     append assistant/message (or assistant/attempt for a failed attempt)
     for each tool call: tool/call, tools/pre-execute, tools/execute, tools/post-execute, tool/result
     step/end
     if tools owe another request or new input arrived: next step
  agent/turn-stopping (serial)
turn/end
```

Design rules that shape extensions:

- "Model-visible means logged. Every model request must be reconstructable from the log. New model-visible inputs require session events." (DSH `docs/architecture.md:130`).
- "Plugins, not loop changes: new behavior goes on documented extension points; changing `agent-loop` requires updating docs/architecture.md." (DSH `AGENTS.md:140`).
- Input reaches an agent through one inbox: `followup(message)` queues a new turn and wakes the driver; `steer(message)` is consumed at the next step boundary; `inject(message)` adds model-facing context without waking an idle agent (DSH `docs/subsystems/core.md:117-143`).
- Tool calls in one model response run in a bounded rolling pool; a tool opts into overlap with `isConcurrencySafe`, everything else runs alone as a barrier (DSH `docs/subsystems/tools.md:72-84`, `:388-398`). The default limit is 10 (DSH `packages/core/agent-loop/README.md:51`).
- Agents can be declared in the loop's config (`agents: [{ id, provider, model, cwd, ... }]`) and start at boot, or be created with `ctx.agents.create()` and resumed with `ctx.agents.resume()`. Both return an `AgentHandle` whose `dispose()` stops the loop, unregisters the agent and unwinds its scope (DSH `packages/core/agent-loop/README.md:30-65`; `docs/subsystems/core.md:43-46`). A `setup(agentCtx, agent)` callback registers scoped contributions before the agent is published (DSH `docs/subsystems/core.md:49`).

### 7.4 Subagents

The subagent seam is "the named-provider registry" `ctx.subagents`. A provider is one transport for running child agents (DSH `docs/subsystems/subagent.md:353-396`). Six ship (DSH `packages/subagent/README.md:27-37`):

| Provider package | What the child is |
|---|---|
| `subagent-spawn-in-process` | A fresh in-process child agent with its own session and no parent context |
| `subagent-fork-in-process` | An in-process child seeded with the parent's completed history |
| `subagent-acp` | An out-of-process child over the Agent Client Protocol |
| `subagent-codex` | A Codex child through its app-server protocol |
| `subagent-claude-code` | A Claude Code child through its Agent SDK |
| `subagent-dsh-sdk` | Another harness process through the TypeScript SDK |

A provider declares capabilities (`agentOptions`, `agentPreset`, `outputSchema`, `depthLimit`) and whether the child inherits parent context. In-process providers contribute only "whether the child session is seeded"; a manager in the subagent runtime owns identity, creation, prompt delivery, resume and disposal. External providers return one execution handle with `result` and `dispose()` (DSH `docs/subsystems/subagent.md:20-26`, `:342`, `:380-395`).

Model-facing tools:

- `subagent` (one tool instance per configured provider and tool name; the base bundle mounts `subagent` on `spawn` and `subagent_fork` on `fork`). A call starts a managed activation and returns `started subagent <name>` at once. "It does not wait for the child's result." The runtime later delivers a completion notice with the final answer to the calling agent if it is still online (DSH `packages/subagent/tool-subagent/README.md:60-62`; `packages/bundle/base/cordis.patch.yml:391-405`).
- `send_message`, `list_agents`, `wait_agent`, `interrupt_agent` from `tool-subagent-control` (DSH `docs/tool-catalog.md` headings at 2267-2329). The shipped compositions set `interrupt: false`, so `interrupt_agent` is not offered by default (DSH `packages/bundle/base/cordis.patch.yml:386-389`). Local spawn and fork children accept follow-up messages and can be resumed after they settle; external children run one task (DSH `packages/subagent/tool-subagent/README.md:62`).
- Depth: `maxDepth` caps recursion; the Host default is 1 (DSH `packages/subagent/tool-subagent/README.md:64`).

Results for program consumers: `SubagentResult { output, structured?, diagnostic?, stopReason }` with stop reasons `completed`, `aborted`, `error`, `max-tokens`, `refusal`. A child-level failure resolves with a non-`completed` stop reason; only an infrastructure fault rejects (DSH `docs/subsystems/subagent.md:263-310`, `:321-337`).

Structured output: a start request may carry `outputSchema`, an object-rooted JSON Schema in an enforced subset. The in-process child gets a scoped capture tool, an instruction and enforcement registrations on its own context. A validation failure is a retryable tool error for the child; finishing without a valid capture settles the child as an error (DSH `.agents/notes/archived/feature/2026-07-05-dynamic-workflows.md:42-46`).

Delivery policy: an activation is started with `delivery: 'parent'` (the model is notified) or `delivery: 'caller'` (only the calling code gets the result). "Workflow and code mode consumers can await caller results without injecting another parent message." (DSH `docs/subsystems/subagent.md:9`, `:46-47`).

Agent Teams adds a shared task board on top of the subagent tree: tasks with status, owner, `blockedBy` edges that must stay acyclic, advisory write scopes, and a revision number for compare-and-set. It is enabled per root session (DSH `docs/subsystems/agent-team.md:54-76`).

### 7.5 Code execution (PTC)

The tool registry has two presentations. In native mode every visible tool is a function definition. In PTC mode the model sees only `run_code` plus a generated SDK declaration file in the system prompt, and calls tools as `await tools.name(args)` from a TypeScript program. The design note credits Cloudflare's Code Mode and gives the reason: the model can loop, branch and fan out without a model round trip per call, and only what the program prints comes back into context (DSH `.agents/notes/implemented/feature/2026-06-15-ptc.md:7-13`, `:21`).

`run_code` takes `code` (the body of an async TypeScript function), `description`, optional `timeoutMs` (default 120,000 ms, cap 600,000 ms), and optional `sandbox_permissions` with a `justification` that needs approval (DSH `docs/tool-catalog.md:626-661`). Each tool call from the program re-enters the normal pipeline with its own approvals and logging; denied calls reject inside the program (DSH `docs/tool-execution-pipeline.md:64`). A program gets a total call allowance derived from its timeout (default 1,024 per minute) and up to 10 parallel sub-calls (DSH `packages/core/tool-executor-ptc/README.md:37-39`).

The execution seam is `ctx.ptcRuntime`: "run one program against host-provided bindings and report what it printed and returned". It knows nothing about tools or sessions (DSH `packages/ptc-runtime/README.md:29`; `packages/ptc-runtime/ptc-runtime/README.md:76`). The shipped provider runs each program in a fresh managed Node process under the calling session's file sandbox policy, with an elapsed deadline, an output cap and a V8 heap limit; opt-in modes retain a REPL per agent. Retained memory is never restored from a session (DSH `packages/ptc-runtime/ptc-runtime-node/README.md:12`, `:79`; `packages/ptc-runtime/ptc-runtime/README.md:137`). The base bundle mounts the PTC executor disabled; native execution is the default (DSH `packages/bundle/base/cordis.patch.yml:516-521`).

### 7.6 The workflow capability: a model-written script that orchestrates subagents

The harness has exactly this feature. Its own documents call it "dynamic workflows" (DSH `docs/cookbook/extension-cookbook.md:109`; `.agents/notes/archived/feature/2026-07-05-dynamic-workflows.md:1`).

Definition. "The workflow seam lets an agent run a model-written orchestration SCRIPT that starts subagents." It is one optional capability, not part of the agent loop (DSH `docs/subsystems/workflow.md:5`).

Packages (DSH `packages/workflow/README.md:27-29`):

| Package | Role | Service key |
|---|---|---|
| `@deepseek-ai/dsh-workflow` | Service Definition: the abstract `WorkflowEngine`, the request, run and result types, the `workflow/*` events, `WorkflowError` | `ctx.workflowEngine` |
| `@deepseek-ai/dsh-workflow-ptc` | Service Provider: the only engine. Runs scripts through the PTC Node process runtime | provides `ctx.workflowEngine` |
| `@deepseek-ai/dsh-tool-workflow` | Consumer: the model-facing `workflow` tool | registers on `ctx.tools` |

One engine per context: "loading a second engine fails loudly, so swapping engines means changing which engine plugin the composition loads" (DSH `packages/workflow/workflow/README.md:71`).

Where it is mounted. The base bundle mounts `ptc-runtime`, `workflow-ptc` (with `provider: spawn`) and `tool-workflow` (DSH `packages/bundle/base/cordis.patch.yml:407-416`). The headless, SDK and ACP bundle patches contain no row that mentions workflow, so those profiles keep the base rows (INFERRED from a text search of the bundle patches). The Web bundle disables the Host-level rows and mounts both packages inside each of the `standard`, `ptc` and `cordis` agent presets, in a `cordis:group` with `isolate: { workflowEngine: true }` (DSH `packages/bundle/web-app/cordis.patch.yml:598-602`; `packages/bundle/web-app/presets/standard.patch.yml:89-116`). The `minimal` preset and the `sdk-minimal` bundle do not mention it.

When the model may use it. The tool registers this prompt section: "Use the workflow tool ONLY when the user explicitly asks for a workflow or for large multi-agent orchestration: you write a JavaScript script (the tool description documents the exact format) that fans work out across many subagents with phases and structured results. For one or two delegations, prefer plain subagent calls." (DSH `packages/workflow/tool-workflow/src/index.ts:326-330`, with the default tool name substituted).

Tool call. Parameters (DSH `packages/workflow/tool-workflow/src/index.ts:334-377`; `docs/tool-catalog.md:2717-2791`):

| Parameter | Type | Meaning |
|---|---|---|
| `script` | string, required | "The plain JavaScript body, not TypeScript and without an `export const meta` statement; top-level await is allowed. End with `return <value>`; the JSON-serializable value is this tool's result." |
| `meta` | object, required | Identity as plain JSON, never evaluated as code: `name` (short kebab-case) and `description` required; optional `whenToUse`; optional `phases`, each `{ title, detail?, provider?, model? }` |
| `args` | object, optional | Exposed to the script as the `args` global. A bare list must be wrapped in a field. |
| `run_in_background` | boolean, optional | Return a job id at once instead of waiting |

The tool description is the authoring specification the model receives. Its hook list, verbatim (DSH `docs/tool-catalog.md:2709-2715`, generated from `packages/workflow/tool-workflow/src/index.ts:160-166`):

```text
Script-body hooks:
- `agent(prompt, opts?): Promise<any>` — run one subagent to completion. Without `opts.schema` it resolves to the child's final text; with `opts.schema` (an object-rooted JSON Schema using ONLY type/properties/required/additionalProperties/items/enum/const/oneOf) it resolves to the validated object. Resolves `null` when the child fails (filter with `.filter(Boolean)`). Other opts: `label` (display), `phase` (progress group), and independent `provider`/`model` LLM target overrides.
- `pipeline(items, ...stages): Promise<any[]>` — run each item through the stages independently with NO barrier between stages (prefer this for multi-stage work). Each stage receives `(prev, item, index)`. A stage throw drops that ITEM to `null` and skips its remaining stages.
- `parallel(thunks): Promise<any[]>` — run zero-argument functions concurrently and await ALL of them (a barrier; use only when a stage genuinely needs every prior result together). A throwing thunk resolves to `null`.
- `phase(title)` — start a progress phase; `log(message)` — narrate progress; `args` — the tool call's `args` input, verbatim.

Misused hooks (bad arguments, unknown options, unsupported schemas, tripped caps) end the whole script instead of producing `null`. The script has no filesystem, network, timer, or Node.js APIs; the agents do the work.
```

A script, from the package README (DSH `packages/workflow/workflow/README.md:38-45`):

```text
// Script body — runs with top-level await, ends with a JSON return value:
const reviews = await parallel([
  () => agent('Review src/a.ts for correctness'),
  () => agent('Review src/b.ts for correctness'),
])
return { reviewed: reviews.length }
```

How the engine runs it:

1. Validation before anything runs. `start()` validates `meta` field by field and rejects unknown fields (`META_INVALID`); rejects a body that still begins with `export const meta` and parses the body with `new vm.Script` without executing it (`SCRIPT_PARSE`); checks that the subagent provider is registered; resolves the per-run caps. These failures throw synchronously, and the tool returns them to the model as an error it can correct (DSH `packages/workflow/workflow-ptc/src/index.ts:49-65`, `:134-148`; `src/meta.ts:13-82`).
2. Process. The engine starts one PTC program in a Node process under the calling session's sandbox policy and working directory, with `timeoutMs: null` (no elapsed deadline). If the runtime cannot confine and the session's mode is not `danger-full-access`, the run fails (DSH `packages/workflow/workflow-ptc/src/host.ts:280-300`).
3. Script realm. Inside that process a guest program wraps the body as `(async () => { <body> })()`, compiles it with `vm.Script`, and runs it in a new `vm` context whose only added globals are `agent`, `parallel`, `pipeline`, `phase`, `log` and `args` (DSH `packages/workflow/workflow-ptc/src/runtime.ts:75-98`, `:105-114`). The script's first synchronous slice is bounded by `syncTimeoutMs`.
4. Host bindings. The guest talks to the Host over five JSON bindings: `begin`, `startChild`, `childResult`, `disposeChild`, `progress` (DSH `packages/workflow/workflow-ptc/src/host.ts:178-189`).
5. Children. Each `agent()` call becomes `ctx.subagents.startActivation({ provider, label: '<meta.name> child <n>', delivery: 'caller', parent, cwd, prompt, outputSchema?, agentOptions? })`. Every child is attributed to the calling agent. Children do not notify the parent model (DSH `packages/workflow/workflow-ptc/src/host.ts:200-227`).

Hook semantics in the implementation (DSH `packages/workflow/workflow-ptc/src/runtime.ts`):

- `agent(prompt, opts)`: the prompt must be a non-empty string. Allowed option keys are `label`, `phase`, `schema`, `provider`, `model`. The keys `effort`, `isolation` and `agentType` are rejected with a message that calls them deferred; any other key is rejected as unrecognized (`:33-35`, `:252-258`). The call counts against the total cap before it waits for a concurrency slot (`:168-179`). It returns the child's final text, or the structured value when a schema was given. It returns `null` when the child's stop reason is not `completed`, and also when a schema was given but no structured value came back. The child is always disposed afterwards (`:207-225`).
- `parallel(thunks)`: every thunk runs; a thunk that throws yields `null`, unless the error is a fatal `WorkflowError`, which is re-thrown (`:285-308`).
- `pipeline(items, ...stages)`: items run independently and concurrently; within one item the stages run in order; a throwing stage yields `null` for that item (`:311-340`).
- `phase(title)` sets the label for later `agent()` calls and reports progress; `log(message)` reports a line (`:352-366`).
- Concurrency is a FIFO slot counter inside the guest (`:143-160`).
- The return value must be plain JSON: functions, symbols, cycles, sparse arrays, non-finite numbers and nested `undefined` are rejected (`RESULT_UNSERIALIZABLE`); a script that returns nothing yields `null` (`:109`, `:128-140`; DSH `packages/workflow/workflow-ptc/README.md:91`).

Failure discipline. `WorkflowError` codes are `SCRIPT_PARSE`, `META_INVALID`, `INVALID_ARGUMENT`, `UNSUPPORTED_OPTION`, `UNSUPPORTED_SCHEMA`, `AGENT_CAP`, `ITEM_CAP`, `AGENT_START`, `AGENT_RESULT`, `RESULT_UNSERIALIZABLE`, `CANCELLED`. All are fatal: the combinators re-throw them instead of nulling the item. The stated reason: "a typo'd option must kill the script loudly, never dissolve into something that reads as an ordinary child failure" (DSH `packages/workflow/workflow/src/index.ts:108-139`; `docs/subsystems/workflow.md:116`). An ordinary child failure is not an error of the workflow: `agent()` returns `null` and the script decides.

Limits (DSH `packages/workflow/workflow-ptc/src/index.ts:106-112`, `:141-148`; `packages/workflow/tool-workflow/src/index.ts:59-63`):

| Setting | Default | Meaning |
|---|---|---|
| `provider` | `spawn` | Subagent provider used by `agent()` |
| `maxConcurrentAgents` | `0` | `0` resolves to `min(16, max(1, cores - 2))` |
| `maxTotalAgents` | `1000` | Total `agent()` calls per run, "the runaway-loop backstop" |
| `maxItemsPerCall` | `4096` | Items per `parallel()` or `pipeline()` call |
| `syncTimeoutMs` | `5000` | Timeout of the script's first synchronous slice |
| `toolName` | `workflow` | Tool name |
| `maxResultChars` | `50000` | Rendered result ceiling |
| `enableRunInBackground` | `true` | Expose `run_in_background` |

The caps are cooperative counters inside the guest, "not host-enforced security quotas or descendant token budgets". There is no overall elapsed deadline; a caller's abort signal still applies. "The VM is not a security boundary — withheld globals guide script authors; OS policy governs code that reaches Node." Network access is not restricted by the file policy (DSH `packages/workflow/workflow-ptc/README.md:59`, `:154-159`).

The run handle. `ctx.workflowEngine.start(request)` returns `WorkflowRun { id, meta, result, cancel(reason?), dispose() }`. `result` never rejects: it resolves to `WorkflowResult { value, stopReason, error?, agentsStarted }` with `stopReason` one of `completed`, `cancelled`, `error`. The caller owns the run and must call `dispose()` on every path; disposal cancels remaining work and waits for the script process and every child to finish cleanup (DSH `packages/workflow/workflow/src/runtime-types.ts:19-49`; `src/types.ts:63-87`). Unloading the engine plugin prevents new starts but does not revoke accepted runs (DSH `packages/workflow/workflow/README.md:83`).

What the model sees.

- Foreground: the parent turn blocks until the whole workflow settles. Success renders as `workflow "<name>" completed (<n> agents).`, then `Return value:` and the pretty-printed JSON, clipped at `maxResultChars`. A non-completed run becomes a tool error: `workflow run was cancelled` or `workflow run failed: <error>`; "partial output is never reported as success". The model never sees intermediate child messages (DSH `packages/workflow/tool-workflow/src/index.ts:197-210`, `:239-246`, `:436-482`; `packages/workflow/tool-workflow/README.md:34-38`).
- Background: `run_in_background: true` registers the run as a job of kind `workflow` owned by the calling agent and returns `{ kind: 'background', jobId, runId }` at once. The job's output ring streams `phase()` and `log()` lines and member start and end lines. The return value arrives with the job's completion notice and through `job_output`; `job_kill` cancels it. `job_output` before settlement returns status only (DSH `packages/workflow/tool-workflow/src/index.ts:263-316`; `packages/workflow/tool-workflow/README.md:42`, `:170`).

Cancellation. An aborted parent step cancels the run. Cancel aborts the script process at once and disposes every started child; a child that finishes starting after the cancel is disposed too. The run settles only after pending starts and child disposals have finished (DSH `packages/workflow/workflow-ptc/src/host.ts:151-156`, `:222-225`, `:310-320`).

Events and records.

- Live events, all `emit` and observe-only: `workflow/start`, `workflow/phase`, `workflow/log`, `workflow/agent-start`, `workflow/agent-end`, `workflow/end`. Payloads are data snapshots (run id and meta, never the live run), and `workflow/end` omits the result value so that a listener gains neither control nor the caller's data (DSH `packages/workflow/workflow/src/index.ts:36-90`; `docs/subsystems/workflow.md:120`).
- Durable records: for a top-level call the tool appends four log-only session events to the calling session: `tool-workflow/run-start`, `tool-workflow/agent-start`, `tool-workflow/agent-end`, `tool-workflow/run-end`. Recording never affects execution: the first failed append disables later writes for that run. `phase()` and `log()` lines are not persisted (DSH `packages/workflow/tool-workflow/src/types.ts:13-63`; `packages/workflow/tool-workflow/README.md:78-80`). A browser plugin folds the four events into one chat row (DSH `docs/subsystems/workflow.md:128`).
- The script, meta and args themselves stay in the assistant's tool call in the session log (DSH `packages/workflow/tool-workflow/README.md:153`).

Stated limitations (DSH `packages/workflow/workflow/README.md:126-130`):

- "No journaling or resume — scripts, child progress, and intermediate values are not checkpointed, so a process restart cannot continue a run."
- "No saved or nested workflows": only caller-supplied scripts, and a script gets no `workflow()` hook.
- "No token-budget vocabulary": neither the request nor the result accounts for model tokens across children.
- Runs are holder-owned, not tracked by the service.
- At the seam there is no background start and poll interface; the tool's background mode is built on the job runtime.

Deferred, named as non-goals in the design note (DSH `.agents/notes/archived/feature/2026-07-05-dynamic-workflows.md:52-59`): journaling and resume ("implementing it reintroduces CC's determinism bans as a script-contract tightening"), saved or bundled workflows with a slash-command interface, nested `workflow()`, a token budget, the `effort`, `isolation` and `agentType` agent options, an overall wall-clock timeout.

Design lineage, as the harness documents state it. The note says the capability follows Claude Code's dynamic workflows (it cites https://code.claude.com/docs/en/workflows) and calls its script contract "Claude Code-compatible". It names its deliberate differences: hook misuse is fatal instead of yielding `null`; `meta` is a JSON parameter instead of an `export const meta` statement inside the script, because obtaining meta from the script "requires evaluating model-written text on the host"; `args` must be an object; determinism restrictions are not imposed because there is no journaling (DSH `.agents/notes/archived/feature/2026-07-05-dynamic-workflows.md:10`, `:18-20`, `:68`). These statements about Claude Code were not checked against Claude Code's documentation.

Alternatives the note records as rejected: running the script in-process with `node:vm` (a synchronous spin past the first await cannot be killed, and `start()` blocks the Host); `isolated-vm` (a second JavaScript engine with native dependencies); background execution as the default; provider JSON mode instead of a capture tool for structured output; a full JSON Schema validator (DSH `.agents/notes/archived/feature/2026-07-05-dynamic-workflows.md:63-72`). An earlier implementation ran the script in a worker thread; it was replaced because "code escaping its VM can use Node with the host process's file authority" (DSH `.agents/notes/implemented/architecture/2026-09-13-workflow-ptc-sandbox-reuse.md:9-13`).

### 7.7 Related orchestration features

- Ralph (experimental, off by default). The `ralph` tool runs "a foreground sequence of fresh child agents against one immutable objective"; each round receives only the previous bounded structured report and the shared workspace. It is "an ordinary plugin over `ctx.workflowEngine` and `ctx.subagents`": a fixed, deployment-owned script; the model supplies `{ objective, maxRounds? }` only (DSH `packages/experimental/tool-ralph/README.md:12`, `:30-36`, `:59-61`).
- Goals. One durable completion objective per session, with a phase and a round cap; an optional driver plugin continues the same session in rounds. "A goal is state, not a scheduler or a separate conversation." (DSH `docs/glossary.md:25-27`).
- Jobs. Tools that run long work register it as a job; the owning agent can read, wait on, list and cancel it, and completion "is delivered to the owning agent in-session instead of polled" (DSH `packages/jobs/README.md:12`).
- Schedule. Recurring tasks (`after`, `at`, `every`, `daily`, `weekly`, `cron`, `rrule`) that deliver a follow-up message to an existing session or a new one, kept across Host restarts (DSH `docs/subsystems/schedule.md:5`; `packages/schedule/README.md:12`).
- Plan mode and todo. Logged planning state with a user-reviewed exit, and a whole-list `todo_write` tool (DSH `packages/README.md:63-64`).
- Hooks. A "native hook" is an ordinary plugin on an interception event. Experimental bridges map Claude Code and Codex hook configuration files onto `agent/created`, `agent/pre-step`, `tools/pre-execute`, `tools/post-execute` and `agent/turn-stopping` (DSH `docs/cookbook/extension-cookbook.md:13`, `:106`).

### 7.8 The agent changing its own harness

The Web application ships four agent presets: `standard`, `ptc`, `minimal`, and `cordis`, which the documentation calls Creator mode (DSH `packages/bundle/web-app/README.md:95`, `:133`, `:177`; `packages/bundle/web-app/presets/cordis.patch.yml:8-17`). In Standard and Creator the agent has the `plugin_manager` tool; Creator adds two read-only tools, `cordis_inspect_list` and `cordis_inspect_query`, that return the live service and event catalogue, live tool schemas, the live Loader entries and one entry's config schema (DSH `packages/extensions/tool-cordis/README.md:12`, `:28`). The documented flow: the user asks for an MCP server or a plugin; "The agent writes a configuration-only bundle whose patch inserts `@deepseek-ai/dsh-mcp-client`, then installs it with `plugin_manager install_bundle`. With HMR enabled, the tools appear in the same running session." The change belongs to the profile and survives restarts (DSH `docs/user/develop/practice/dynamic-cordis.md:5-13`).

A second mechanism, the dynamic Cordis runner, keeps process-local plugin definitions for programmatic callers and browser controls. Its Host half runs in a `node:vm` realm, definitions disappear on restart, and "no model tool creates dynamic definitions" (DSH `packages/extensions/cordis-host-runner/README.md:12`, `:58`).

## 8. Configuration, permissions and safety

### 8.1 Where configuration lives

| Place | What it holds | Evidence |
|---|---|---|
| A bundle's `cordis.patch.yml` | The rows and defaults a package contributes | DSH `docs/user/develop/basic/publish.md:56-62` |
| `$DSH_HOME/profiles/<name>/package.json` | Out-of-tree plugin dependencies and `dsh.profile.bundles` | DSH `docs/user/develop/basic/publish.md:70-75` |
| `$DSH_HOME/profiles/<name>/cordis.patch.yml` | The user's patch layer for one profile; UI edits (plugin toggles, preset edits, config forms) are written here | DSH `packages/boot/plugin-manager/README.md:40`; `packages/bundle/web-app/README.md:95` |
| `$DSH_HOME/cordis.patch.yml` | Machine-local overrides for every profile | DSH `packages/boot/app-boot/README.md:65` |
| `--patch <file>` | Per-invocation overlays | DSH `docs/user/develop/basic/publish.md:127` |
| `$DSH_HOME/profiles/<name>/compatibility.json` | Exact-version exemptions | DSH `packages/boot/app-boot/README.md:58` |
| `.env` in the invoking directory and in `$DSH_HOME` | Environment layers below the inherited environment; variables that decide how the process starts are rejected from files | DSH `packages/boot/app-boot/README.md:64` |
| The session log | Per-session state such as approval policy, sandbox mode, model selection, preset id | DSH `docs/subsystems/approval.md:33` |

Validation: every row's `config` is validated by its plugin's schema at load. `dsh --dump-config-schema` prints a JSON Schema for the composed entry list; it imports each composed module to read its schema, so the documentation says to run it only against plugins already trusted (DSH `packages/boot/app-boot/README.md:56`, `:81`).

### 8.2 Per-scope overrides

- Per layer: a later patch layer overrides a row by `id` (section 4.4).
- Per group: `isolate` gives a group its own instance of a service, so two groups can run differently configured providers of the same name (DSH `docs/user/develop/framework/service.md:113-139`).
- Per session: an agent preset is a row of `@deepseek-ai/dsh-agent-preset` with `config.id` and `config.plugins`, a child entry list. The registry mounts each declaration in its own scope and in-memory Loader tree. A session selects a preset; "existing Agents retain the composition they already use", and edits affect agents created afterwards (DSH `packages/preset/agent-preset-registry/README.md:12`, `:56`). A service row inside a preset needs an `isolate` realm (DSH `docs/architecture.md:152`).
- Per agent: registrations through `agent.ctx`, tool restrictions, a scoped persona section (section 2).
- Per field at run time: volatile fields (section 4.2) and the settings service, which layers defaults, the composition's base and the user's document (DSH `docs/subsystems/README.md:25`).

### 8.3 Permission and approval model

Two independent controls, bundled for the user as permission presets (DSH `docs/subsystems/permission-presets.md:5-11`):

- Sandbox mode, a file-effect policy for spawned processes: `read-only`, `workspace-write`, `danger-full-access`. Backends: Linux bwrap and Landlock, macOS Seatbelt, a Windows ACL restricted-token runner. Enforcement is reported as `full` or `partial`. "Network and process visibility are outside this vocabulary." (DSH `docs/subsystems/sandbox.md:5-38`).
- Approval policy per session: `ask` (delegate to the answerers a UI or automation bridge registered) or `never` (every ask resolves `rejected` without prompting anyone). The outcome of one ask is `allowed-once`, `rejected`, `cancelled` or `unavailable`; callers "fail closed unless it is `allowed-once`". A grant covers only the asked-about action. Every ask and decision is appended to the session log as an audit pair (DSH `docs/subsystems/approval.md:5`, `:21-46`, `:86-88`).

Shipped defaults: sandbox mode `workspace-write` unless `DSH_PERMISSION_MODE` says otherwise; approval `ask`, or `never` under `danger-full-access`; presets `read-only`, `workspace-write`, `danger-full-access` (DSH `packages/bundle/base/cordis.patch.yml:240-276`).

Where a decision is enforced: a plugin returns `ask` or `deny` from `tools/pre-execute`, or registers a guard (section 7.2). The package rule is "Enforce a decision in the operation that makes it. Schema omission, prompt filtering, facades, wrappers, and listener order are not enforcement when direct or alternate callers can bypass them; test denial through the executor." (DSH `packages/AGENTS.md:16`).

The `plugin_manager` tool is gated: "Every tool action requires `danger-full-access` or approval for that call." (DSH `packages/boot/plugin-manager/README.md:31`).

### 8.4 What SAFETY.md says

- "DeepSeek Harness is experimental developer-preview software. It has not undergone a security audit and must not be treated as secure or production-ready." (DSH `SAFETY.md:7`).
- The project "can execute model-generated code and commands, load third-party plugins, and access the network, processes, credentials, and files made available to it" (`:9`).
- "Sandboxing, approval prompts, and permission controls can reduce risk, but they do not guarantee isolation or prevent damage." "Do not rely on DeepSeek Harness as the sole security control for untrusted workloads." (`:13-15`).
- Advice: least privilege, a disposable virtual machine or container, backups, no sensitive credentials unless the risk is accepted, and "Review plugins, configuration, and proposed commands before allowing them to run." (`:19-23`).
- No warranty, MIT terms (`:27`).

### 8.5 How untrusted plugins are treated

They are not isolated. A plugin is Host code in the Host process:

- "Profile changes persist across sessions, and installed Host code executes in-process outside the workspace sandbox." (DSH `packages/boot/plugin-manager/README.md:31`).
- "Presets are not security sandboxes: YAML and plugins can execute Host code." (DSH `packages/preset/agent-preset-registry/README.md:99`).
- A dynamic package's sandbox "isolates globals but is not a security boundary ... Treat a dynamic package like bash access" (DSH `packages/extensions/cordis-host-runner/README.md:62`).
- Version compatibility checks "are not a sandbox against malicious package code" (DSH `packages/boot/app-boot/README.md:54`).

The protections that exist are at install time and are consent-based:

- pnpm refuses to run a git dependency's build script until the package is allowlisted under `allowBuilds` in the profile's `pnpm-workspace.yaml`. The guide tells users to "Treat that allowance as permission to execute the package's code on your machine at install time, outside any sandbox the agent runs under", to allow only trusted sources, and to pin a commit (DSH `docs/user/develop/basic/publish.md:172-181`). When an agent installs a bundle, it can pass approved build names only after the user approves them in the conversation; "The service validates pending names; it does not verify conversation approval." (DSH `packages/boot/plugin-manager/README.md:72`).
- Plugin indexes are third-party content: "DSH checks the file format but not those claims. Add only sources you trust, and check the source in the confirmation before installing." Git sources in an index must name a full commit (DSH `docs/user/guide/plugin-market.md:34`, `:136`).
- Installation runs with a scrubbed environment when started by the service (DSH `packages/boot/plugin-manager/README.md:130`).

## 9. State, sessions and durability

Sessions. A session is an append-only log of typed events and "the single source of truth"; the model's history is derived from it by `deriveMessages()`, not stored separately. Fork, resume, transcripts, telemetry and persistence all derive from the log (DSH `docs/subsystems/core.md:417`; `docs/architecture.md:126`).

Persistence is a seam: `ctx.sessionPersistence` with `create`, `open`, `stat`, `list`; `create` and `open` return a per-session handle with `read`, `append`, `flush`, `close` and single-writer ownership. The shipped provider writes JSONL files under `$DSH_HOME/sessions` (DSH `docs/subsystems/persistence.md:7-11`; `packages/bundle/base/cordis.patch.yml:138-141`). Appends are buffered; only a resolved `flush` promises that data survives a crash. A checkpoint-policy plugin flushes before each model request and top-level tool dispatch (DSH `docs/subsystems/persistence.md:67-91`; `packages/bundle/base/cordis.patch.yml:429-431`).

Crash recovery. A log that ends inside a turn is kept as it is: "Persistence does not truncate or repair it." On resume the loop computes closing events (missing tool errors, an open `step/end`, a synthetic `turn/end` with reason `interrupted`) and appends them before publishing the session. A hard process loss before an assistant message settles leaves no record of that attempt's stream (DSH `docs/subsystems/persistence.md:110`; `docs/architecture.md:126`). Resume replays history; it does not continue the interrupted model call or tool (INFERRED from the closing-event description).

Format versions. The session format is versioned (writer, finalized and released versions are all 4 at this commit). A stored session is migrated by adjacent `vN` to `vN+1` steps into a new file; committed files "are never renamed, replaced, or deleted" (DSH `packages/core/session/src/types.ts:90`; `docs/session-format-status.md:31-47`; `docs/architecture.md:128`).

Other state:

| Thing | Durable? | Evidence |
|---|---|---|
| Subagent children (local) | Yes. Each has its own session; the parent keeps a catalogue; a settled child can be resumed cold | DSH `docs/subsystems/subagent.md:140`, `:253` |
| Subagent children (external providers) | One execution; no follow-up | DSH `docs/subsystems/subagent.md:198` |
| Workflow runs | No. No journaling, no resume; a restart loses the run. Only the run and member start and end records and the original tool call are in the log | DSH `packages/workflow/workflow/README.md:127`; `packages/workflow/tool-workflow/src/types.ts:13-63` |
| Background jobs | Registry is process-local. `job/registered` and `job/settled` records are logged; "A missing `job/settled` remains incomplete" | DSH `docs/subsystems/jobs.md:28-32`; `packages/jobs/README.md:29` |
| PTC program memory | No. "Retained state has no disk checkpoint or restoration protocol." | DSH `packages/ptc-runtime/ptc-runtime/README.md:151` |
| Goals | Yes, in the session log; automatic continuation must be re-armed by a human after resume or fork | DSH `docs/glossary.md:25-27` |
| Scheduled tasks | Yes, in the storage domain, across Host restarts | DSH `docs/subsystems/schedule.md:5-11` |
| Team task board | Yes, as session events with revisions | DSH `docs/subsystems/agent-team.md:54-56` |
| Dynamic Cordis definitions | No, process-local | DSH `packages/extensions/cordis-host-runner/README.md:58` |
| Plugin composition | Yes, in the profile directory | Section 8.1 |

## 10. Concrete shapes

All snippets below are verbatim from the cited files.

A minimal plugin and its configuration row (DSH `docs/cordis-tutorial/01-first-plugin.md:11-29`):

```ts
import type { Context } from '@deepseek-ai/cordis'

export const name = 'hello'

export function apply(ctx: Context) {
  console.log('hello from my first plugin')
}
```

```yaml
- name: './hello.ts'
```

A service declaration and a consumer (DSH `docs/cordis-tutorial/03-services.md:12-35`, `:48-56`):

```ts
import { Service, type Context } from '@deepseek-ai/cordis'

declare module '@deepseek-ai/cordis' {
  interface Context {
    greeter: GreeterService
  }
}

export class GreeterService extends Service {
  constructor(ctx: Context) {
    super(ctx, 'greeter')
  }

  greet(who: string) {
    return `Hello, ${who}!`
  }
}

export const name = 'greeter'

export function apply(ctx: Context) {
  ctx.plugin(GreeterService)
}
```

```ts
import type { Context } from '@deepseek-ai/cordis'

export const name = 'consumer'
export const inject = ['greeter']

export function apply(ctx: Context) {
  console.log(ctx.greeter.greet('world'))
}
```

A tool contribution (DSH `docs/user/develop/basic/tool.md:11-34`):

```ts
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'greet-tool'
export const inject = ['tools']

export function apply(ctx: Context) {
  ctx.tools.register(defineTool({
    name: 'greet',
    description: 'Greet someone by name.',
    parameters: {
      name: { type: 'string', required: true, description: 'The name to greet' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args) {
      return `Hello, ${args.name}!`
    },
  }))
}
```

A config schema, excerpt (DSH `docs/cordis-tutorial/05-config.md:17-27`):

```ts
export interface Config {
  greeting: string
  targets: string[]
}

export const Config: Schema<Config> = Schema.object({
  greeting: Schema.string().default('Hello'),
  targets: Schema.array(String).default(['world']),
})

export function apply(ctx: Context, config: Config) {
```

A policy plugin on a waterfall event (DSH `docs/cookbook/extension-cookbook.md:21-30`):

```ts
export const name = 'permission-gate'

export function apply(ctx: Context) {
  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    if (!(await isAllowed(exec))) {
      return { kind: 'deny', reason: 'Denied by policy.' }
    }
    return next()
  })
}
```

A first-party provider plugin, the whole runtime part of the spawn subagent provider (DSH `packages/subagent/subagent-spawn-in-process/src/index.ts:17-20`, `:28-30`, `:57-59`):

```ts
export const name = 'subagent-spawn-in-process'
// `tools` is deliberately not injected: the child factory already provides it during setup,
// and adding it here would unnecessarily change this provider's apply timing.
export const inject = ['subagents']
```

```ts
export const Config: z<Config> = z.object({
  providerName: z.string().default('spawn'),
})
```

```ts
export function apply(ctx: Context, config: Config): void {
  ctx.subagents.registerProvider(new SpawnInProcessProvider(config.providerName))
}
```

A Service Definition for a seam, trimmed to the class (DSH `packages/shell/shell/src/index.ts:30-34`, `:64-67`, `:84`, `:93`):

```ts
declare module '@deepseek-ai/cordis' {
  interface Context {
    shell: ShellExecutor
  }
}
```

```ts
export abstract class ShellExecutor extends Service {
  constructor(ctx: Context) {
    super(ctx, 'shell')
  }
```

```ts
  abstract resolve(request: ShellExecRequest): ShellExecSpec
```

```ts
  abstract execute(spec: ShellExecSpec): Promise<ShellExecution>
```

A bundle: manifest and patch (DSH `docs/user/develop/basic/publish.md:35-44`, `:58-62`):

```json
{
  "name": "dsh-hello-plugin",
  "version": "0.1.0",
  "type": "module",
  "main": "index.js",
  "files": ["index.js", "cordis.patch.yml"],
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```

```yaml
- insert:
    - id: hello
      name: dsh-hello-plugin
```

A profile manifest after `dsh plugin --profile demo add ./hello-plugin` (DSH `docs/user/develop/basic/publish.md:87-102`):

```json
{
  "name": "dsh-profile-demo",
  "private": true,
  "dependencies": {
    "dsh-hello-plugin": "link:/path/to/hello-plugin"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "dsh-hello-plugin"
      ]
    }
  }
}
```

Rows from the shipped base bundle: a conditional row, a configured row, and the workflow rows (DSH `packages/bundle/base/cordis.patch.yml:249-253`, `:391-395`, `:407-416`):

```yaml
    - id: bash-sandbox
      name: '@deepseek-ai/dsh-bash-sandbox'
      disabled: !!js process.platform === 'win32'
      config:
        timeoutMs: 60000
```

```yaml
    - id: tool-subagent
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: spawn
        toolName: subagent
```

```yaml
    - id: ptc-runtime
      name: '@deepseek-ai/dsh-ptc-runtime-node'

    - id: workflow-ptc
      name: '@deepseek-ai/dsh-workflow-ptc'
      config:
        provider: spawn

    - id: tool-workflow
      name: '@deepseek-ai/dsh-tool-workflow'
```

An isolated group inside an agent preset, excerpt (DSH `packages/bundle/web-app/presets/standard.patch.yml:89-93`, `:111-116`):

```yaml
          - id: delegation
            name: cordis:group
            group: true
            isolate:
              workflowEngine: true
```

```yaml
              - id: workflow-ptc
                name: '@deepseek-ai/dsh-workflow-ptc'
                config:
                  provider: spawn
              - id: tool-workflow
                name: '@deepseek-ai/dsh-tool-workflow'
```

The workflow start request and run handle (DSH `packages/workflow/workflow/src/runtime-types.ts:19-49`, comments removed):

```ts
export interface WorkflowStartRequest {
  script: string
  meta: WorkflowMeta
  args?: unknown
  subagentProvider?: string
  maxTotalAgents?: number
  parent: Agent
  signal?: AbortSignal
}

export interface WorkflowRun {
  readonly id: WorkflowRunId
  readonly meta: WorkflowMeta
  readonly result: Promise<WorkflowResult>
  cancel(reason?: string): void
  dispose(): Promise<void>
}
```

The upstream Cordis launcher, which is the whole bootstrap of a Cordis application (Cordis `packages/core/bin.js:3-17`):

```js
import { Context } from 'cordis'
import { pathToFileURL } from 'node:url'
import Loader from '@cordisjs/loader'

const ctx = new Context()
ctx.baseUrl = pathToFileURL(process.cwd()).href + '/'

await ctx.use(Loader)
await ctx.loader.create({
  name: '@cordisjs/include',
  config: {
    path: './cordis.yml',
  },
})
```

## 11. Maturity, versions, caveats

Versions:

- Harness: the root package and the two harness packages checked (`dsh-base`, `dsh-workflow`) are at `0.2.2-rc.2` at the cloned commit, and the package checklist requires every workspace package's version to match the root (DSH `package.json:2-4`; `packages/workflow/workflow/package.json:4`; `docs/cookbook/adding-a-package.md:27`). Upgrade guides exist for thirteen pre-releases from `v0.1.7-rc.2` to `v0.2.2-rc.1` (DSH `docs/upgrade-guide/`). Whether `0.2.2-rc.2` is published on npm is NOT VERIFIED; the registry was not queried.
- Cordis upstream: `cordis` `4.0.0-rc.12`. "Cordis is under active development. The API is not yet stable and may change without notice." Its official documentation is "still under construction" and points to the harness's Cordis primer (Cordis `packages/core/README.md:7-10`).
- Cordis as vendored in the harness: upstream `4.0.0-rc.11` at upstream commit `0b96d9fd517b998e44e2209560c9e5f0671ce933`, republished as `@deepseek-ai/cordis` with the harness's own version number `4.1.0`. Vendored HMR is a snapshot of upstream `1.0.15` (republished as `1.1.0`); upstream `main` is `1.1.2` (DSH `vendor/README.md:11-21`; `vendor/cordis/package.json:4`; `vendor/hmr/package.json:4`). The harness keeps a numbered log of its local modifications, up to entry V27, including lifecycle hardening, volatile config and Loader adaptations (DSH `vendor/README.md:29-50`).
- Naming drift between the two: upstream `main` calls the unit a "component" and loads it with `ctx.use()`, with `ctx.plugin()` kept as a deprecated alias (Cordis `packages/core/src/registry.ts:63-66`, `:118-125`). The vendored copy and all harness documentation use "plugin" and `ctx.plugin()` (DSH `vendor/cordis/src/registry.ts:92-95`, `:185`).
- The paper describes Cordis v4. Koishi, its case study, runs Cordis v3 (Paper section 5.3, footnote).

Stability statements:

- "Public APIs are pre-stable; update every consumer." "Record each externally perceptible breaking change immediately in an upgrade guide." (DSH `AGENTS.md:7-11`).
- Product package groups "have maintained product roles, while their public APIs remain pre-stable" (DSH `packages/README.md:90`).
- Experimental packages may change or be withdrawn (DSH `packages/experimental/README.md:26`).
- Stored session data is treated more strictly than APIs: alpha, beta and release-candidate publications create released-format obligations, and "GitHub's prerelease flag does not make persisted user data disposable" (DSH `docs/session-format-status.md:24`).
- Plugin compatibility is declared with `engines.dsh` and enforced at install and at startup (section 4.6).

Test approach (DSH `docs/testing.md:9-15`, `:25-41`; `packages/AGENTS.md:7`, `:19`):

- Unit tests with Vitest; a coverage gate of 100% per file on `packages/*/*/src`.
- Real-API end-to-end tests that skip themselves without a key.
- Keyless recorded-session snapshot tests: a recorded session supplies user input and model replay and is then the expected persisted result. "Every non-trivial model-, protocol-, or human-visible change adds or updates a keyless recorded-session scenario in the same PR."
- Browser snapshot tests for the Web UI.
- "Product-visible plugins require a non-unit REAL-composition test. Hand-built `ctx.plugin(...)` suites are insufficient": the test must boot a test `cordis.yml` through the Loader. The postmortem of the default-export failure described in section 3.4 lists real-Loader coverage among its fixes (DSH `docs/postmortem/0001-acp-default-export-drops-inject.md:9`).
- Every registry has a disposal test.
- Built-artifact smoke tests run the published entry points under plain Node.
- Much of the reference documentation is generated from source and checked for freshness in CI: the service and event catalogue on each subsystem page, the tool catalogue, the config catalogue, the module graph (DSH `docs/subsystems/workflow.md:136`; `docs/development.md:215-221`).

Licence: MIT for the harness; MIT for Cordis; upstream licence files are kept in each vendored package directory (DSH `vendor/README.md:5`).

Caveats worth carrying forward:

- No security audit; plugins are unsandboxed Host code (section 8).
- The workflow capability is not durable and has no token accounting across children (section 7.6).
- The project does not accept external code contributions (DSH `CONTRIBUTING.md:9`).
- HMR depends on Node internals and replaces Node module-loading methods; some changes still need a restart (Cordis `packages/hmr/README.md`).
- The upstream Cordis repository carries almost no documentation of its own; the harness's primer, tutorial and API pages are the practical documentation.

## 12. What could not be confirmed

- The expansion of "PTC". No file read spells it out.
- The paper's formal content beyond the definitions cited: the transition rules, the metatheory and proofs, and sections 3.4, 4.2.1 to 4.4, 6.4 to 6.7, 7 and 8 were not read. Mathematical notation was garbled in the text extraction, so the formal names of the calculus's four lifecycle states are not known; the implementation section's names (`INACTIVE`, `LOADING`, `ACTIVE`, `UNLOADING`, plus `FAILED`) were readable and differ slightly from the code's enum (`PENDING`, `DISPOSED`).
- Whether the paper's theorems hold for the code as shipped. The paper itself says the runtime does not verify that a disposer undoes its effect.
- How a dependency cycle between two plugins behaves (section 4.5).
- Behaviour at run time. Nothing was executed, so every statement about behaviour rests on documentation and source reading. In particular: the actual hot-reload behaviour of the vendored HMR, and whether the headless, SDK and ACP profiles expose the `workflow` tool by default (inferred from the bundle patches).
- A line-by-line comparison of the vendored Cordis with upstream. Citations to Cordis source in sections 3 and 5 are to upstream `main`; the harness runs a modified `rc.11` snapshot.
- The source of `core/agent-loop` and of the `core/tools` pipeline. Their behaviour is taken from the architecture page, the subsystem pages and the generated diagrams.
- The harness note's statements about Claude Code's dynamic workflows.
- The published documentation site beyond three sampled pages.
- npm publication state, release dates and download numbers. The clone is shallow, so repository history was not examined.
- The contents of the 30,721 repositories under the `dsh-plugin` topic. None was opened.
- The star, fork and commit counts, which come from a saved copy of the repository page and were not fetched again.
