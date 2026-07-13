---
name: process-manager-skill
description: Start, stop, and manage long-running processes with full system PATH. Use for npm, python, node, dev servers, watchers, build tools. Destructive file commands blocked.
allowed-tools: process_manager
metadata:
  author: opencompany
  version: "3.0"
  category: execution

---

# Process Manager Tool

Manage long-running processes with **full system PATH access**. Use this for anything that needs `npm`, `python`, `node`, or runs as a persistent server/watcher.

## Security

The process manager **blocks destructive file commands** to prevent accidental deletion outside the agent's workspace. Use `shell_execute` for file operations instead (sandboxed, no PATH, confined to workspace).

**Blocked commands:** `rm`, `rmdir`, `del`, `rd`, `Remove-Item`, `format`, `mkfs`, `dd`, `shred`, `chmod 777`

**Allowed:** `npm`, `python`, `node`, `pip`, `cargo`, `go`, servers, build tools, installers

## Operations

### start
Launch a process. Output streams to the Terminal tab and is saved to log files.
```json
{"operation": "start", "name": "dev-server", "command": "python -m http.server 8080"}
```

### stop
Kill a process and its entire process tree.
```json
{"operation": "stop", "name": "dev-server"}
```

### restart
Stop then re-launch with the same command.
```json
{"operation": "restart", "name": "dev-server"}
```

### list
Show all running processes for this workflow.
```json
{"operation": "list"}
```

### get_output
Read output from a process's log file. Use `tail` to get last N lines, `stream` to choose stdout or stderr.
```json
{"operation": "get_output", "name": "dev-server", "stream": "stdout", "tail": 20}
```
Returns `{"lines": [...], "total": 150, "file": "/path/to/stdout.log"}`.

### send_input
Write text to a process's stdin (newline appended automatically).
```json
{"operation": "send_input", "name": "dev-server", "text": "quit"}
```

## Shell vs Process Manager

| Need | Tool | Why |
|------|------|-----|
| `ls`, `cat`, `find`, `grep` | **shell_execute** | Sandboxed, fast, no PATH |
| `rm`, `mv`, `cp`, file ops | **shell_execute** | Sandboxed, confined to workspace |
| `npm install`, `pip install` | **process_manager** | Needs PATH |
| `python script.py` | **process_manager** | Needs PATH |
| `npm run dev`, `flask run` | **process_manager** | Long-running, streams output |
| Dev servers, watchers | **process_manager** | Persistent, log files |

## Output Log Files

Each process writes to its own log files in the agent's workspace:
```
{workspace}/{agent_node_id}/.processes/{process_name}/
  stdout.log    # Standard output
  stderr.log    # Error output
```

Use `get_output` to read them selectively. The agent can also read these files directly with the `file_read` tool.

## OS-Specific Commands

Commands differ by platform. Refer to:
- **bash-skill** -- Linux/macOS (apt, brew, lsof, kill, curl)
- **powershell-skill** -- Windows (Get-Process, Stop-Process, Invoke-WebRequest)
- **wsl-skill** -- Running Linux tools on Windows (prefix with `wsl`)

Detect the OS first:
```json
{"operation": "start", "name": "os", "command": "python -c \"import platform; print(platform.system())\""}
```
Returns `Windows`, `Linux`, or `Darwin` (macOS).

## Guidelines

- Always give processes a meaningful **name** so you can stop/check them later
- Use **get_output** after starting to verify the process launched correctly
- Use **stop** to clean up processes when done -- don't leave servers running
- **Do NOT use process_manager for file deletion** -- use shell_execute instead
- Processes are automatically killed when the server shuts down
- Max concurrent processes: configurable in Settings (default: 10)
- Output streams to the Terminal tab in real-time
