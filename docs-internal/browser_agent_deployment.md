# Browser AI Agent deployment and recovery

`browser_agent` uses the existing agent/Workspace/Temporal frameworks and
browser-use `0.13.10`. The Browser tool remains the profile/viewer capability.
See [creation](./node_creation.md), [workspace](./browser_workspace.md),
[Temporal control](./temporal-workflow-control.md) and
[1Password credentials](./onepassword_credentials.md).

## Local deployment

Keep `DISTRIBUTED_MODE=false` and the existing local SQLite and embedded
Temporal setup. Existing encrypted credentials, OAuth refresh stores and CLI
login caches retain their local behavior. 1Password bindings are optional;
desktop authorization supports personal vaults. Install the tested CLI v2
`2.40.0` through the Credentials panel or the normal managed provisioning
handler, and enable 1Password desktop CLI integration on the backend machine.
An interactive desktop authorization must authorize the actual backend
subprocess, not merely another terminal's `op` session.

Add Browser AI Agent from the canvas palette, the saved Browser workspace,
or Agent Builder. The shared recipe creates its private Context, Browser,
Master Skill and vision tool. Existing employees gain the capability only
when the user adds it; no background upgrade rewrites existing graphs.

## Distributed prerequisites

Each backend has one browser-owner process (`WORKERS=1`). Separate
orchestration workers run the existing standalone Temporal worker and do not
register Browser ownership. Every participant uses the same PostgreSQL
application database, external Temporal service/namespace, authentication
keys, trusted owner-forwarding secret and authorized artifact storage.

Start from `.env.template`, override these values through deployment
configuration, and keep bootstrap secrets out of graph or node parameters:

```dotenv
DISTRIBUTED_MODE=true
DATABASE_URL=postgresql+asyncpg://opencompany:...@database:5432/opencompany
TEMPORAL_ENABLED=true
TEMPORAL_SERVER_ADDRESS=temporal.internal:7233
TEMPORAL_NAMESPACE=opencompany
TEMPORAL_AGENT_WORKFLOW_ENABLED=true
TEMPORAL_PER_TYPE_DISPATCH=true
WORKERS=1
BROWSER_REPLICA_ID=backend-a
BROWSER_REPLICA_URL=http://backend-a.internal:5678
BROWSER_MACHINE_ID=machine-a
BROWSER_ROUTER_SECRET=...shared trusted deployment secret...
ONEPASSWORD_AUTH_MODE=service_account
ONEPASSWORD_CLI_VERSION=2.40.0
```

Use a distinct stable replica ID and internal backend origin for each owner.
`BROWSER_MACHINE_ID` identifies its original physical machine and must survive
container restarts; absent an explicit value it uses the machine hostname.
Backend origins contain no path, credentials, query or fragment. They are
server configuration, never client-supplied addresses. Shared `SECRET_KEY`
is the forwarding-secret fallback; explicit `BROWSER_ROUTER_SECRET` permits
separate rotation. Use authenticated, trusted internal transport and normal
TLS termination between machines.

Provision `OP_SERVICE_ACCOUNT_TOKEN` from trusted deployment secret
configuration with read-only access to a dedicated automation vault. The
resolver passes it only to the private `op` child; Browser/Chrome children do
not inherit it. Connect environment variables are excluded. Static provider
keys and configured website logins require approved 1Password bindings.
Provision the verified CLI and resolver authorization on every backend and
orchestration worker that resolves provider keys. Website credentials resolve
only on their browser owner. Keep platform-specific executable installations
local to each runtime.
Cluster preflight and runtime reject inline/local/ambient credentials,
OAuth refresh stores and CLI-managed sessions without an audited static
adapter. Their local behavior is preserved; this release does not migrate
refresh tokens or write them back into the read-only vault.
Generic static bindings cover one API field. Connections requiring additional
credential fields also require an explicit audited adapter; their existing
local enrollment remains available.

Mount artifacts at the same relative workspace paths on replicas and workers.
FileRefs use authorized application routes rather than absolute filesystem
paths. Persistent browser profile storage must provide an exclusive writer
and proven cross-machine file-lock behavior. Owner process and Chrome profile
locks fence overlapping writers. Keep owner-local runtime/PID state local:
Chrome PID records are stable per owner/profile on its original machine;
browser-use daemon homes and temporary state are separate per runtime epoch.
Do not share these local directories between owners. Never put the
application or encrypted-credential SQLite databases on NFS/SMB.

Backend lifecycle starts an owner-specific Browser Activity worker even when
`TEMPORAL_WORKER_POOL_ENABLED=false`. Generic workers never consume owner
queues. Logical plugin Activity selection remains separate from physical
queue names. Frozen routing covers new ordinary Browser nodes as well as
Browser Agents; old recorded Temporal command paths retain their compatibility
branches. See [Temporal task routing](https://docs.temporal.io/task-routing).
Before enabling distributed mode, finish or cancel unversioned Browser work
using its existing local workers and confirm cleanup. Retaining old replay
branches does not route an already-recorded generic Browser Activity onto a
new owner queue. Distributed workers intentionally do not execute those
unowned Browser Activities.

## Offline SQLite transfer

Stop local writers, close browsers, finish/cancel direct tasks, and Reset
deployed workflows before transfer. Stop target writers too. The command is
an explicit export/import into an empty target, not live replication or a
token migration. Keep the export private: it contains application history
and approved secret-reference metadata.

From `server/`, with normal application configuration available:

```powershell
python -m tools.database_transfer export --sqlite C:/local/workflow.db --output C:/private/opencompany-transfer.json --offline
# Set DATABASE_URL to the new shared PostgreSQL database in trusted configuration.
python -m tools.database_transfer import --input C:/private/opencompany-transfer.json --offline
```

The current SQLModel schema is created through normal database startup.
Transfer preserves application IDs, graphs, parameters, account records,
bindings and terminal task history, and reseeds PostgreSQL integer sequences.
Inline credential fields, active Workspace tasks and unfinished Browser
claims are rejected. Local encrypted secrets, OAuth tables, caches, transient
routes and live owner registrations are excluded. Import refuses a nonempty
application target and commits in one transaction. Copy the associated
workspace artifacts and persistent browser profile files separately while
their writers are stopped. Existing Temporal histories are not exported by
this application database command.

Profile ownership and nonsecret sensitive-login/challenge state are retained.
Legacy profiles that predate ownership records receive original-machine
ownership during export; importing them does not permit another replica to
adopt them.
The manifest records the original local machine. When imported profiles have
owner `local`, import creates an unavailable owner registration for that
machine. Bootstrap that original machine's first cluster backend with
`BROWSER_REPLICA_ID=local` and the matching `BROWSER_MACHINE_ID`. Other
backends use distinct IDs. Registration explicitly renews the epoch and
requires observation; another machine cannot adopt these profiles silently.

PostgreSQL startup uses a transaction advisory lock for schema creation and
rejects missing columns before serving an incompatible schema. Existing
SQLite migrations remain local. Shared read/modify/write mutation and
reservation paths use transaction advisory locking to preserve their former
SQLite write-reservation semantics; portable compare-and-set claims remain
atomic. UTC timestamps accept the existing aware and naive UTC conventions.
Startup adds the nullable workflow/tool association columns for earlier
development owner-registry schemas. Other missing shared columns fail closed.

## Ownership, unavailability and cleanup

Profile ownership is permanent for this release. A stable task token holds
the profile during model reasoning, browser work and human assistance.
Competing tasks return `BrowserBusy` before browser effects. Human control
keeps its separate lease; a stale cancellation cannot release a newer task.

An offline owner's queued Activity remains on that owner's queue without
consuming retries or starting another model turn. The UI reports
**Browser unavailable — waiting for its owner.** Attempt/heartbeat timeouts
start after Activity pickup. Recovery renews the epoch on the original
machine, restores nonsecret control state and requires fresh observation;
Chrome tabs and element references are not restored by Temporal. Mutating
operations on retry attempts remain refused. An uncertain outcome requires
observation and effect confirmation before another irreversible action.
Use human assistance when the effect cannot be established.

Cancel and Reset wait for a confirmed owner cleanup receipt. Cleanup waits
for admitted browser work, stops/drains the CLI daemon, and retires Chrome if
daemon suspension cannot be confirmed. Reset can remain in progress while
the owner is unavailable. An operator restores the original owner; there is
no scheduler, automatic profile transfer or retry identity reset. Wrong
machine identity, a still-live owner process or failed cleanup stays
unavailable instead of creating a second writer.

Open/stop/profile controls, manual input, live binary WebSockets and cleanup
are proxied to the owner through authenticated application endpoints. Owners
recheck principal and resource authorization; CDP stays private. PostgreSQL
NOTIFY carries only Browser identifiers. Receiving replicas request current
state through authorized APIs; page contents, frames, secrets and owner URLs
never appear in invalidation messages. Task history is an authorized 35-day
terminal projection with 20 records per page (maximum 100), not the execution
authority. Active history survives Reset and retention cleanup.

Direct tasks are generation zero and independent of Start/Stop/Resume. Their
server-versioned native child uses saved Browser configuration, a separate
task transcript and frozen routing; Continue-As-New preserves completed model
turns and tools. `/api/browser/tasks` accepts only saved workflow/agent IDs,
the task and a submission UUID. Extra execution bindings are rejected. The
server resolves the saved Browser tool through `/discovery`; `/history`
returns metadata and bounded public results. Stable IDs and fingerprints
prevent duplicate submissions, including terminal records whose Temporal
history has expired. Cancel and historical status remain authorized through
the workflow after an agent is removed. Completion accounting occurs once
at the invocation parent boundary.

## Acceptance and release gate

Automated checks use controlled fixtures: exact configured form login,
capture gates and secret canaries, policy restrictions and handoff, reading
account data, preparing a change, task/cancellation isolation, two owner
queues, replay/Continue-As-New, PostgreSQL migration/concurrent claims and
identifier-only invalidation. Ordinary Browser/profile/cookie/viewer and old
agent/graph regressions remain required.

Run the isolated real-database tests separately from the legacy stubbed
`tests/` suite:

```powershell
python -m pytest -q integration_tests/test_browser_foundations.py
# A test PostgreSQL role must be allowed to create disposable databases.
$env:TEST_POSTGRES_URL='postgresql://test@127.0.0.1:55438/browser_integration'
python -m pytest -q integration_tests/test_postgresql_browser.py
```

Real 1Password desktop authorization/expiry and scoped service-account
rotation/revocation require configured test vaults on each supported platform.
Validate the target multi-machine storage locks, authenticated owner proxy
and viewer transport, owner outage/recovery and external Temporal namespace
before production release. External-account smoke tests remain explicitly
authorized: GitHub login with human MFA followed by a read-only issue
summary; Gmail human multi-step login followed by an invoice summary without
sending mail. Passing mocked or local fixture tests does not certify those
deployment/account gates.
