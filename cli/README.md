# company

Project supervisor CLI for the OpenCompany stack. The public command is
`company`; the npm package is `@zeenie/opencompany`, and the legacy `machina` bin
remains as a deprecated compatibility alias. It is also backwards-compatible
with `pnpm run start`/`dev`/`stop`/etc.

## Install

Done automatically in `scripts/postinstall.js` after `pnpm install`. To
install manually:

```sh
python -m pip install -e .
```

## Use

```sh
company --help        # list commands
company stop          # kill ports + orphans + temporal
```

Or via the npm scripts (which now invoke the same Python CLI):

```sh
pnpm run stop
```

## Cross-platform notes

- **Windows**: `python` resolves correctly out of the box.
- **macOS / Linux**: requires `python` to point to Python 3.12+. Most
  modern distros provide this via `python-is-python3` or a symlink. If
  your distro only ships `python3`, run `python3 -m cli <cmd>` or
  add an alias.

## Architecture

Built on battle-tested primitives — minimal custom code:

| Layer | Library |
|---|---|
| CLI | `typer` |
| Subprocess | `anyio.open_process` |
| Tree-kill | `psutil` + `pywin32` (Job Objects on Windows) |
| Restart backoff | stdlib (deque + monotonic time + jittered exponential, ~20 LOC) |
| Output | `rich.Console` |
| Env | stdlib parser over `.env.template`, `.env`, and `.env.dev` |
