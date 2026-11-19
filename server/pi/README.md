# Private Pi SDK host

This package pins the full Earendil Pi Coding Agent SDK to 1.1.0 and builds two
hosts. The maintenance driver manages one assistant response and its tool batch;
model requests and tool effects are performed by OpenCompany Temporal leaves,
never by the driver process. The separate tool host executes SDK tools inside
those admitted node activities. The maintenance driver disables ambient
resources, provider networking, SDK retries, compaction and cache warming.

Run `bun install --frozen-lockfile`, `bun test`, and `bun run typecheck` here.
`bun run build` writes four files to `dist/`:

- `index.js`: the host with the SDK and every package it imports inlined, one
  file of about 12 MiB. It runs on Bun with no `node_modules`. Nothing is
  installed, unpacked or cached where it runs.
- `tool-host.js`: the separate leaf runner for the Pi Read File, Write File,
  Edit File, Bash, PowerShell, Search Text, Find Files and List Directory nodes.
  It invokes the pinned SDK's actual tools; the maintenance driver still has
  all built-ins disabled.
- `package.json`: the SDK's own manifest. The SDK reads its name and version
  from `PI_PACKAGE_DIR`, and the Python driver points that at this folder.
- `THIRD-PARTY-LICENSES.txt`: the licenses of the inlined packages.

The build fails when the installed SDK differs from the pin in `src/types.ts`.
It rebuilds `dist/` whole, so a file left by an earlier build never ships.
`bun run test:distribution` runs the built bundle from a folder outside the
repository. CLI, desktop and Docker ship these four files.

New Coding Agents created through the editor/API, Agent Builder and Hire receive
the seven portable Pi tool nodes, plus Pi PowerShell on Windows, connected to
their Tools input. This is a creation recipe; existing agents are not migrated.
The [Pi integration guide](../../docs-internal/pi_coding_agent.md) maps node
types to model tool names and describes operator policy and saved-graph wiring.

Pi has no core todo tool. Its `examples/extensions/todo.ts` is an optional
extension example that this host does not load. Connect OpenCompany's
[Write Todos node](../../docs-internal/node-logic-flows/ai_tools/writeTodos.md)
for the existing checklist UI. It is not part of the automatic Pi bundle;
its workflow/node-scoped state is in memory and does not survive server restarts.

The bundle holds what the host imports statically. The SDK's lazily loaded
features are not in it, and they fail if called:

- codemode (QuickJS WebAssembly and its worker);
- image resizing (photon WebAssembly). Its loader returns nothing, and the
  bundle carries the build machine's path to that package;
- extensions loaded from source, and provider modules the SDK imports by a
  computed name.

The model host calls none of them: it passes empty resources, no built-in tools, its
own provider and text input. A change that needs one of them has to ship that
feature's files beside the bundle.

The leaf runner disables image resizing, preserving native PNG/JPEG/GIF/WebP
bytes for OpenCompany's media pipeline. BMP conversion still requires the
unshipped Photon assets. It uses the SDK's operation hooks to confine file
operations to the workflow workspace and rejects symlinks/junctions. Bash and
PowerShell remain explicitly open-world command capabilities. Shell calls have
a 30-second default deadline (maximum 600 seconds), SDK process-tree
cancellation and the Python leaf's independent bounded watchdog/cleanup.
Truncated shell output is retained under `.pi-output/` in the workflow
workspace; SDK result paths are rewritten so Pi Read File can retrieve it.

Search Text requires `rg` (ripgrep) on PATH. Find Files supplies the SDK's glob
operation using `rg --files`, preserving `.gitignore` filtering without an `fd`
dependency. `PI_OFFLINE=1` prevents automatic downloads. Windows Bash uses Git
Bash or a configured Bash installation; PowerShell is Windows-only. Missing
executables are reported as tool errors.

The leaf protocol accepts one LF-delimited `{protocol:1,tool,cwd,args}` request,
followed optionally by `{type:"abort"}`. Stdin stays open until the result;
unexpected EOF aborts the call. Stdout contains exactly one `{ok:true,result}`
or `{ok:false,error}` response. Frames are bounded to 16 MiB, and the parent
retains ownership of timeout, cancellation, approvals and result recording.

Without a build, a source checkout resolves `src/index.ts` for the maintenance
driver and `src/tool-host.ts` for tool activities against this folder's pinned
`node_modules` (`server/services/pi_runtime.py`). A packaged install without
`dist/tool-host.js` must be rebuilt.

For tool validation, run `bun run test tests/builtin-tools.test.ts tests/distribution.test.ts`
after building, along with `bun run typecheck`.
The tests exercise actual SDK file/image operations, shell exit errors,
timeouts, cancellation, inherited output pipes and offline packaged startup.
They require Bash and ripgrep; Windows additionally exercises PowerShell.
The Python bridge smoke is opt-in: set `PI_TOOL_INTEGRATION=1`, then run
`.venv/Scripts/python.exe -m pytest tests/services/test_pi_builtin_tools.py -k real_source_host`
from `server/` on Windows (use `.venv/bin/python` on Unix). It checks that
command children have exited after timeout and cancellation.

The maintenance driver's JSONL protocol is defined in `src/types.ts`. Frames are UTF-8 JSON separated
only by LF, bounded to 256 KiB. Larger values use canonical JSON transport files
in Python's private attempt directory, bounded to 16 MiB. References verify
scope, epoch, content hash, byte count, canonical encoding and regular-file paths.
Stdout contains protocol frames; errors expose
fixed messages without provider exceptions. Each host accepts one `run_turn`.
The Python bridge includes recognized fixed error codes, such as
`projection_mismatch`, in activity failures while keeping raw diagnostic text
and unknown codes private.
`keepalive` renews its 120-second input lease; the Python driver sends it every
30 seconds while a durable operation is pending. `abort`, `park`, input EOF and
lease expiry abort the SDK and reap the host. Parking completes maintenance
successfully without committing an aborted transcript.

The model operation references admitted canonical context/configuration/tool
digests. SDK validation produces an `assistant_ready` preflight before tool
callbacks. The Python bridge durably acknowledges this frame before processing
later tool requests or pre-filling a delegation batch. Tool requests reference
the recorded call ID/index, never coerced SDK arguments. Native malformed
arguments, unknown tools, validation failures and truncated calls produce
non-effect receipts. Duplicate or missing recorded call IDs fail before effects.

Canonical `MessageWire` remains authoritative. Pi session entries carry only
conversion metadata, original provider state and durable image references;
hydrated image bytes are rejected. Declaration-only system messages are derived
Pi loadout metadata. A completed snapshot contains the full header, entries,
leaf pointer, scope, epoch, turn, version and canonical projection digest.
Scope mismatch and corruption fail explicitly. Context/configuration/tool
changes rebuild the derived session from canonical wires.

The conversation store's optional display timestamp, `ts`, is retained in
`_oc` conversion metadata and restored when projecting a stored message. It
does not set Pi's SDK timestamp or reach a provider. Omitting it remains valid;
preserving it prevents restored conversations from failing the pre-model
projection check while leaving existing snapshot and operation digests intact.

The protocol preserves integer tokens beyond JavaScript's safe-number range as
BigInt in memory and emits them as exact numeric JSON tokens. Unicode key sorting
matches Python's scalar ordering. Pi receives a cloned numeric validation view
of tool arguments and schemas; native Pydantic validation always checks the exact
recorded arguments before any effect.
