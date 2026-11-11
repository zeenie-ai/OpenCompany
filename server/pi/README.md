# Private Pi SDK host

This package pins the full Earendil Pi Coding Agent SDK to 1.1.0. It is a
maintenance driver for one assistant response and its tool batch. Model requests
and tool effects are performed by OpenCompany Temporal leaves, never by this
process. The host disables ambient resources, provider networking, SDK retries,
compaction and cache warming.

Run `bun install --frozen-lockfile`, `bun test`, and `bun run typecheck` here.
`bun run build` writes three files to `dist/`:

- `index.js`: the host with the SDK and every package it imports inlined, one
  file of about 12 MiB. It runs on Bun with no `node_modules`. Nothing is
  installed, unpacked or cached where it runs.
- `package.json`: the SDK's own manifest. The SDK reads its name and version
  from `PI_PACKAGE_DIR`, and the Python driver points that at this folder.
- `THIRD-PARTY-LICENSES.txt`: the licenses of the inlined packages.

The build fails when the installed SDK differs from the pin in `src/types.ts`.
It rebuilds `dist/` whole, so a file left by an earlier build never ships.
`bun run test:distribution` runs the built bundle from a folder outside the
repository. CLI, desktop and Docker ship these three files.

The bundle holds what the host imports statically. The SDK's lazily loaded
features are not in it, and they fail if called:

- codemode (QuickJS WebAssembly and its worker);
- image resizing (photon WebAssembly). Its loader returns nothing, and the
  bundle carries the build machine's path to that package;
- extensions loaded from source, and provider modules the SDK imports by a
  computed name.

The host calls none of them: it passes empty resources, no built-in tools, its
own provider and text input. A change that needs one of them has to ship that
feature's files beside the bundle.

Without a build, a source checkout runs `src/index.ts` against this folder's
`node_modules` (`server/services/pi_runtime.py`).

The JSONL protocol is defined in `src/types.ts`. Frames are UTF-8 JSON separated
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
