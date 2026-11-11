# Pi Coding Agent

New Coding Agent (`coding_agent`) executions use
`@earendil-works/pi-coding-agent@1.1.0`. The full SDK session owns an
assistant response and its tool batch. `PiCodingAgentWorkflow` owns durable
admission, effects, results and recovery. OpenCompany's current provider
transport, gateway, credentials, tool policies and Context remain authoritative.

## Admission and public contract

The existing node type, handles, model settings and output envelope stay the
same. `tool_mode` is `connected` by default; `workspace` fills missing
`fileRead`, `fileModify`, `fsSearch` and `shell` capabilities. Connected nodes
of those types override automatic bindings. Remaining tool-name collisions
fail before billing. Other tool types must be connected explicitly.

New generations/invocations freeze `pi_harness_version=1` before preparation.
Missing metadata denotes an existing native run and stays native across
retries, delegation and Continue-As-New. New Pi execution requires Temporal
even when general agent-workflow routing is disabled. A whole-node Activity
cannot wait for the Pi child: that would occupy an admitted action while the
child is paused and can deadlock Stop.

Durable AI Agent/team callers dispatch Coding Agent children directly. Inline
delegation from RLM or Claude Code runtimes starts a durable
`PiInlineDelegationWorkflow` and returns the existing task acknowledgement.
The wrapper owns the attached child, control registration and terminal result
bookkeeping; callers continue polling through `check_delegated_tasks`.
Task Manager keeps the same trusted scope when assigning Coding work or starting
an EmployeeJob. Polling admits only this principal/root/parent's task receipts.
Claude MCP call identities include the validated HTTP session and JSON-RPC ID,
so parallel CLI clients cannot merge separate recorded operations.
Admission records bind retries to the recorded caller operation and reject
conflicting reuse. Existing native invocations retain their recorded behavior.

New one-off invocations without a deployment controller freeze
`pi_local_control_version=1`. Their workflow accounts for admitted actions and
forwards Stop/Resume to attached descendants without inventing a controller
identity. Deployment executions retain the existing generation controller and
its registration, checkpoint and child-discovery rules.

`CODING_PI_ADMISSIONS_ENABLED=false` blocks new Pi admissions during rollout
or rollback. It does not change already admitted runtimes. Start workers and
ship runtime assets before enabling new admissions; never silently fall back
to native execution after a Pi failure.

## SDK and broker boundary

The Bun host runs an in-memory SessionManager with empty resources and exact
custom tool-name allowlists. Ambient extensions, skills, AGENTS files, prompt
templates and credentials are not loaded. OpenCompany's connected skill tool
continues to provide scoped progressive discovery.

SDK settings explicitly disable automatic compaction, agent retries, provider
retries and background cache warming (`cacheWarming: "off"`). The default
warming setting otherwise sends extra model requests. Ordinary SDK tools run
sequentially. The host calls `session.agent.prompt([])` after seeding the
admitted transcript, adds no synthetic user message, and ends after a complete
round through `finishTurn`.

A custom ModelRuntime provider exchanges model operations with the Python
broker. Custom tool callbacks exchange recorded tool call identities. Neither
performs business I/O. Provider credentials resolve inside existing model/tool
Activities and never enter the host, prepared payload or workflow history.

The broker uses bounded LF-delimited JSON frames with correlated operation
requests/results. Diagnostics use stderr. Updates `register_driver` and
`submit_operation` validate/queue synchronously; the workflow main loop alone
schedules effects. `operation_status` is a read-only Query. Model submission
uses context/configuration/binding digests; tool submission uses the recorded
call ID/index. The coordinator constructs actual requests from trusted state.

Stable operation IDs derive from the turn and recorded assistant call order.
An exact retry attaches to the receipt. Different content under the same
logical ID fails explicitly; stale epochs cannot insert new operations. SDK
validation/unknown/truncated calls are non-effect receipts, reconciled against
the canonical assistant before checkpoint acceptance. Host validation uses
the original arguments, preserving malformed raw arguments and omitted
defaults rather than trusting SDK coercion.

## Control and recovery

Maintenance drivers poll a mandatory private `coding-pi-driver` queue. They
cannot consume framework/model/tool slots. Driver capacity defaults to one
locally and two in cloud/self-hosted mode; a positive integer
`TEMPORAL_CODING_PI_DRIVER_CONCURRENCY` overrides it.

Separate slots alone cannot prevent a parent-to-child driver cycle. When the
recorded assistant requests attached delegated work or a durable task wait,
the coordinator parks and disposes the parent driver before waiting. Its slot
is released while children run through the existing bounded permits and depth
limits. After their receipts are terminal, a new driver reconstructs the round
and consumes cached results in original call order.

Stop closes admission and drains already admitted leaves. Their results remain
recorded, then the driver epoch is fenced and maintenance is quiesced. Aborted
partial SDK transcripts are discarded. Resume reconstructs from the last
complete snapshot and consumes recorded outcomes before admitting remaining
effects. Expected parking is successful maintenance completion, not a
retryable failure. Provider retries remain the native leaf policy.

Drivers heartbeat every 30 seconds with a 90-second heartbeat timeout and a
30-minute attempt timeout. Classified transport failures reconstruct from
receipts; protocol conflicts and incompatible snapshots fail explicitly. EOF
and a lease watchdog dispose orphan hosts. Physical cleanup is best-effort
under a worker partition; fencing prevents an orphan from admitting effects.

Recognized host error codes, including `projection_mismatch`, accompany the
fixed activity error message and details. Unknown codes, raw host messages and
stderr remain private. A projection mismatch before the first model operation
means transcript reconstruction failed; no provider request or tool effect has
been admitted for that round.

Continue-As-New runs only between accepted rounds, after the driver and all
handlers finish, with no live children or pending operations. The complete
serialized continuation retains the 1,900,000-byte guard.

## Context and immutable caches

`MessageWire` and `(workflow_id, generation, agent_node_id)` Context are the
conversation authority. Preserve ordered blocks, raw calls, provider replay
state/signatures and durable media references. Native model Activities save
the assistant before tools; idempotent tool-result bookkeeping closes the turn.
Pi declaration-only system entries cannot modify canonical policy or grant
tools. Hydrated images and duplicate message bodies are not durable metadata.
Credential redaction applies to saved configuration. It preserves complete tool
schemas and canonical wires, including arbitrary tool-argument and opaque
provider-state field names that happen to resemble credential fields.

Stored conversation messages also carry the view-only `ts` added by the
conversation store. The Pi codec preserves this optional field in its existing
`_oc` reconstruction metadata so the projected transcript matches the admitted
digest exactly. It does not become the SDK's `timestamp` or provider input;
the native provider codec still ignores it. Messages and snapshots without
`ts` stay unchanged, and nested `ts` keys in arguments or provider state remain
opaque data. This keeps recorded driver inputs and snapshot digests compatible
without changing workflow commands or weakening integrity checks.

Immutable scoped blobs hold complete Pi headers/entries/leaf IDs and large
receipts, with SHA-256 integrity and a 256 KiB inline threshold. Protocol frames
above that threshold use verified files in a Python-selected attempt directory;
those disposable buffers are separate from the durable database blobs. Durable
blobs are execution caches with no mutable conversation head. Accept a snapshot only
after scope/epoch/turn, projection/configuration/binding digests and the exact
consumed receipt set agree and required bookkeeping has finished. Only then
can the workflow release active-turn receipts.

Canonical compaction, chat branching or configuration changes invalidate the
derived session. Rebuild from retained MessageWire and frozen bindings. Keep
OpenCompany's existing pressure relief, summary under the user role, original
byte-stable system prompt and unread latest tool turn. Never let Pi and native
automatic compaction run independently.
An unsupported snapshot format or SDK version fails terminally; it cannot enter
the ordinary canonical/configuration/binding rebuild path.

## Distribution and validation

The private Pi package has a frozen lockfile. Its build writes one bundle,
`server/pi/dist/index.js`, with the SDK and every package it imports inlined,
plus the SDK's own `package.json` and the inlined packages' licenses. CLI,
desktop and Docker ship those three files, and the backend runs the bundle
where it is: nothing is verified, unpacked or cached first. No runtime package
installation or dependency on the root workspace is permitted. The bundle
leaves out the SDK's lazily loaded features (codemode, image resizing,
extensions loaded from source), which the host never calls;
[server/pi/README.md](../server/pi/README.md) lists them.

SDK contract tests exercise reconstruction, declaration refresh, raw arguments,
provider state, media, incompatible snapshots, bootstrap lease cleanup, bounded
terminal errors and non-effect failures. Temporal integration tests cover lost
replies, snapshot-write interruption, Stop/Resume, parking and Continue-As-New.
The worker-recovery gate hard-kills a Python driver after a successful tool,
checks orphan Bun disposal, and waits for the real heartbeat timeout before
receipt reconstruction. The nested-control gate runs five children and five
grandchildren with one SDK driver slot and two teammate permits, checks central
Stop/Resume, ordering and exact usage, then replays all twelve histories.
Frozen pre-cutover histories prove native v1 retries and legacy rollover
compatibility independently of histories produced by current source.

Public-boundary regressions exercise actual AIService/RLM/Claude plugin
dispatch, Task Manager, EmployeeJob admission, scoped polling, locked schemas
and approvals. Standalone bootstrap tests run the native compaction Activity
before any queue polls. Distribution smoke tests launch the archive offline
without the root dependency tree.

Temporal durability scenarios use fixture model/blob/permit Activities. They
do not prove database-backed permit recovery or live paid-provider execution.
Context branches, clear, Reset and every public feature do not yet have combined
Pi end-to-end coverage. Cross-platform packaged startup is configured in CI;
Linux/macOS/Docker execution still requires that release validation to run.

Primary references: [Pi SDK](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/docs/sdk.md),
[Pi agent loop](https://github.com/earendil-works/pi/blob/v1.1.0/packages/agent/src/agent-loop.ts),
[Pi session manager](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/src/core/session-manager.ts),
[Temporal parallel execution](https://docs.temporal.io/design-patterns/parallel-execution),
[Temporal versioning](https://docs.temporal.io/develop/python/workflows/versioning).
