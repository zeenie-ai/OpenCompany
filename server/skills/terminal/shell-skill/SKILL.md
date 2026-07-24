---
name: shell-skill
description: Execute short-lived shell commands inside the per-workflow workspace. The shell is Nushell (cross-platform, same syntax on Windows/macOS/Linux). External tools on PATH (npm, node, python, git, ...) are available.
allowed-tools: "shell"
metadata:
  author: opencompany
  version: "4.1"
  category: execution

---

# Shell Tool (Nushell)

Execute short-lived shell commands in the workflow workspace. **The shell is [Nushell](https://www.nushell.sh/) — the same grammar runs on Windows, macOS, and Linux.** Do not write `cmd.exe`, PowerShell, or Bash idioms; they will fail or behave wrong.

External binaries on `PATH` (`npm`, `node`, `python`, `git`, `pwd`, etc.) are available — Nu invokes them as external commands automatically.

**GNU coreutils (`sed`, `awk`, `head`, `tail`, `grep`, `cut`, `sort`, `uniq`, `wc`, `tr`, `xargs`, `find`) are NOT in PATH on Windows** and will fail with `Command not found`. Use Nushell builtins or switch tools — see the table below.

## Check the host before reaching for external tools

Don't burn turns on trial-and-error (`sed not found` → retry with `awk` → `awk not found` → ...). Detect the host **once** at the start of a task that needs platform-sensitive binaries, then branch:

```nu
# `$nu.os-info.name` is "windows" | "linux" | "macos" | "android" | ...
let os = $nu.os-info.name
```

Then either:
1. **Skip the shell entirely.** For reading / editing / searching files, the right answer almost always is `file_read` / `file_modify` / `fs_search` — they are path-sandboxed and platform-agnostic by construction.
2. **Use Nu builtins.** `open`, `lines`, `first`, `last`, `find`, `glob`, `length`, `save`, `cp`, `mv`, `rm`, `mkdir` exist on every host.
3. **Probe before invoking external CLIs.** `if (which sed | is-empty) { ... } else { sed ... }` — but at that point, `file_read` / `file_modify` is shorter and works everywhere.

**Rule of thumb**: if your command starts with `sed` / `awk` / `head` / `tail` / `grep`, stop and reach for `file_read` or `fs_search` instead. You almost never need a host check — you need the dedicated tool.

## shell_execute Tool

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| command | string | Yes | Nushell command (single or `;`-chained) |
| timeout | int | No | Seconds (default 30, max 300) |

### Response

```json
{
  "stdout": "command output",
  "exit_code": 0,
  "truncated": false,
  "command": "ls"
}
```

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 124 | Timed out |
| non-zero | Failure |

## Critical: Nushell ≠ Bash

| Bash / cmd.exe (do NOT use) | Nushell (correct) |
|---|---|
| `cmd1 && cmd2` (and-then) | `cmd1; cmd2` *(unconditional sequential — see below for short-circuit)* |
| `cmd1 || cmd2` (or-else) | `try { cmd1 } catch { cmd2 }` |
| `$VAR` substitution | `$env.VAR` |
| `` `cmd` `` or `$(cmd)` | `(cmd)` *(parens, no dollar)* |
| `cmd > file.txt` | `cmd \| save file.txt` |
| `cmd >> file.txt` | `cmd \| save --append file.txt` |
| `cmd 2>&1` | `cmd \| complete \| get stdout` *(all output is captured anyway)* |
| `if [ -f x.txt ]; then ...` | `if ('x.txt' \| path exists) { ... }` |
| `for f in *.py; do ...` | `glob '*.py' \| each { \|f\| ... }` |
| `*` glob in argv (auto-expand) | wrap in quotes or use `glob` |
| `~/path` | `('~/path' \| path expand)` |
| `sed -n '1,N p' file` | `open file --raw \| lines \| first N` *(prefer `file_read` with `limit`)* |
| `head -n N file` | `open file --raw \| lines \| first N` |
| `tail -n N file` | `open file --raw \| lines \| last N` |
| `sed -i 's/a/b/' file` | use `file_modify` (edit op) — not the shell |
| `grep 'pat' file` | `open file --raw \| find 'pat'` *(prefer `fs_search`)* |
| `grep -r 'pat' src/` | use `fs_search` (grep mode) |
| `wc -l file` | `open file --raw \| lines \| length` |
| `find . -name '*.py'` | `glob '**/*.py'` |
| `xargs cmd` | `each { \|x\| cmd $x }` |

### Short-circuit "and-then" (the `&&` replacement)

The user log showed `pwd && ls -la` failing — the parser explicitly rejects `&&`. Use one of:

```nu
# A: just sequential, doesn't short-circuit on failure
pwd; ls -la

# B: short-circuit using exit code via try/catch
try { npm install } catch { print 'install failed'; exit 1 }
ls -la

# C: explicit conditional on the previous command's success
let r = (do { npm install } | complete)
if $r.exit_code == 0 { ls -la } else { print $r.stderr }
```

Use **A** for "run these in order regardless of outcome", **B/C** when you must stop on failure.

## Common tasks (cross-platform, Nushell)

| Task | Command |
|---|---|
| Show current dir | `pwd` *(nu builtin)* |
| List files | `ls` *(returns a table — pipe further)* |
| List recursively | `ls **/*` |
| Read file | `open README.md` *(text/json/csv auto-parsed)* or `cat README.md` |
| Write to file | `'hello' \| save -f output.txt` |
| Append | `'more' \| save --append output.txt` |
| Find files by name | `glob '**/*.py'` |
| Search content | `rg 'pattern' .` *(if ripgrep on PATH)* or `open file.txt \| find 'pattern'` |
| Copy / move / delete | `cp a b`, `mv a b`, `rm a` |
| Make folder | `mkdir new` |
| Run npm / node / python | `npm install`, `node app.js`, `python -V` *(via PATH)* |
| Capture command output into a var | `let v = (npm -v \| str trim)` |
| Conditional on a binary existing | `if (which git \| is-empty) { print 'no git' }` |

## Workspace and paths

- The cwd is the per-workflow workspace; relative paths resolve there.
- Filesystem operations elsewhere on this tool (read/write/edit via `file_*`) are workspace-contained and reject `..`/`~` traversal. Shell `execute()` itself retains historical host-shell behavior and is **not** path-restricted, so prefer `file_read` / `file_modify` / `fs_search` for actual filesystem work.

## Use the right tool

| Need | Tool | Why |
|---|---|---|
| List / search / one-shot file ops | **shell_execute** | Fast, in-workspace |
| Reading or editing a specific file | **file_read** / **file_modify** | Path-sandboxed, no shell parsing surprises |
| Long-running processes (dev servers, watchers, `npm run dev`) | **process_manager** | Streams output, restartable, doesn't tie up the agent |
| Recursive code search | **fs_search** | grep mode, structured results |

## Guidelines

1. **Never use `&&`, `||`, backticks, `$VAR`, or `>` redirection.** Use the Nushell equivalent on the right side of the table above.
2. **Never invoke GNU coreutils** (`sed`, `awk`, `head`, `tail`, `grep`, `find`, `wc`, `cut`, `sort`, `uniq`, `xargs`, `tr`). They are not on Windows. For peeking at a file, use **`file_read`** (line-numbered, `offset`/`limit` aware); for searching, use **`fs_search`** (grep mode).
3. **Check the host first** when an external CLI is unavoidable. `let os = $nu.os-info.name` once, then gate by `windows` / `linux` / `macos`. Don't loop "try → fail → retry".
4. **One command (or `;`-chain) per call.** No multi-line scripts; if you need control flow, use `if` / `try` / `each` inline.
5. **Short-lived only.** `shell_execute` *always* awaits completion — a small `timeout` does **not** make it run in the background, it just kills the command after N seconds. If the command runs longer than ~30s, opens a port, watches files, or is described as "dev server / watcher / daemon" (`npm run dev`, `vite`, `tsx watch`, `python -m http.server`, ...), use **`process_manager`** instead. Trying to "fire and forget" with `timeout=2` will kill the process the moment the port comes up.
6. **Nu syntax is host-agnostic.** Builtins (`ls`, `open`, `glob`, `lines`, `save`, `cp`, ...) work the same everywhere. You only need the host check for *external* tools.
7. **Quote glob patterns** (`'*.py'`) so Nu's `glob` builtin expands them, not the caller.
8. **Capture command exit code** with `do { … } | complete` if you need to branch on success/failure.
