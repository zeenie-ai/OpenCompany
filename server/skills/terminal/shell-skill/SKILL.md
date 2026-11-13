---
name: shell-skill
description: Execute short-lived shell commands inside the per-workflow workspace. Uses Nushell when installed and otherwise the host shell. External tools on PATH (npm, node, python, git, ...) are available.
allowed-tools: "shell"
metadata:
  author: opencompany
  version: "4.2"
  category: execution

---

# Shell Tool

Execute short-lived shell commands in the workflow workspace. The backend uses
[Nushell](https://www.nushell.sh/) when `nu` is installed. Otherwise it passes
the command to the host shell (`cmd.exe` on Windows or the platform
`/bin/sh`-style shell on POSIX). Shell-specific syntax is therefore
conditional on the installed runner.

External binaries on `PATH` (`npm`, `node`, `python`, `git`, etc.) are
available to whichever runner is selected.

For portable calls, prefer one external command with ordinary arguments, such
as `git status` or `python -V`. Use `file_read`, `file_modify`, or `fs_search`
for filesystem work; they avoid runner-specific parsing.

## Runner-dependent syntax

Pipes, redirection, chaining with `;`, `&&` or `||`, quoting and backslash
escapes all depend on the runner, so send one program with plain arguments per
call and make separate calls for separate steps. Under Nushell, `&&` and `||`
are parse errors (Nushell's message names `;` instead); under the host shell
they behave as that shell defines them.

Inline code such as `python -c "..."` works under every runner only as one
line with no backslashes or double quotes inside: Nushell turns `\n` in a
double-quoted string into a newline and rejects unknown escapes such as `\[`,
and each shell escapes a double quote inside a double-quoted argument
differently. Put longer code in a file with `file_modify` and run the file
(`python script.py`).

If the environment is known to have Nushell, its equivalents include:

```nu
try { npm install } catch { print 'install failed'; exit 1 }
open README.md --raw | lines | first 20
```

Do not send Nushell pipelines, Bash substitutions, PowerShell cmdlets, or
`cmd.exe` builtins unless the selected environment is known to support that
syntax. GNU utilities such as `sed`, `awk`, and `grep` are not generally
available on Windows.

## shell_execute Tool

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| command | string | Yes | One program with plain arguments, such as `python script.py`; parsed by Nushell when installed, otherwise by the host shell |
| timeout | int | No | Seconds (default 30, max 600) |

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

## Nushell reference (only when `nu` is installed)

| Bash / cmd.exe form | Nushell form |
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

### Short-circuit "and-then" when Nushell is active

Nushell rejects `&&` and `||`. With Nushell available, use:

```nu
# Short-circuit using try/catch
try { npm install } catch { print 'install failed'; exit 1 }
ls -la

# Or inspect an external command's exit code
let r = (do { npm install } | complete)
if $r.exit_code == 0 { ls -la } else { print $r.stderr }
```

## Common tasks when Nushell is active

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

1. **One program per call.** Chaining (`&&`, `||`, `;`), variables, redirection, pipelines, and builtins depend on whether Nushell or the host shell was selected; send separate calls for separate steps.
2. **Keep inline code to one line.** No backslashes or double quotes inside it; put longer code in a file with `file_modify` and run the file.
3. **Use dedicated filesystem tools.** `file_read`, `file_modify`, and `fs_search` are workspace-contained and avoid shell parsing differences.
4. **Treat Nushell syntax as conditional.** The reference above applies only when `nu` is installed.
5. **Short-lived only.** `shell_execute` *always* awaits completion — a small `timeout` does **not** make it run in the background, it just kills the command after N seconds. If the command runs longer than ~30s, opens a port, watches files, or is described as "dev server / watcher / daemon" (`npm run dev`, `vite`, `tsx watch`, `python -m http.server`, ...), use **`process_manager`** instead. Trying to "fire and forget" with `timeout=2` will kill the process the moment the port comes up.
6. **The timeout range is 1–600 seconds.** Use `process_manager` instead of raising it for persistent processes.
