---
name: powershell-skill
description: Windows PowerShell commands and patterns for process management, file operations, and system administration.
allowed-tools: "process_manager shell"
metadata:
  author: opencompany
  version: "1.0"
  category: terminal

---

# PowerShell Skill (Windows)

Use this skill when the host system is **Windows**. Commands run via the `process_manager` tool (which has full PATH access).

## Detect Windows

Before using PowerShell commands, verify the OS:
```json
{"operation": "start", "name": "os-check", "command": "powershell -Command \"$env:OS\""}
```
If output contains `Windows_NT`, use this skill. Otherwise use the bash skill.

## Common Patterns

### Run a PowerShell command
```json
{"operation": "start", "name": "ps-cmd", "command": "powershell -NoProfile -Command \"Get-Process | Sort-Object CPU -Descending | Select-Object -First 10\""}
```

### Install packages
```json
{"operation": "start", "name": "npm-install", "command": "npm install express"}
```
```json
{"operation": "start", "name": "pip-install", "command": "pip install flask"}
```

### Start a dev server
```json
{"operation": "start", "name": "dev-server", "command": "npm run dev"}
```

### Check what's running on a port
```json
{"operation": "start", "name": "port-check", "command": "powershell -Command \"Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | Select-Object OwningProcess\""}
```

### List files recursively
```json
{"operation": "start", "name": "find-files", "command": "powershell -Command \"Get-ChildItem -Recurse -Filter *.py | Select-Object FullName\""}
```

### Environment variables
```json
{"operation": "start", "name": "env", "command": "powershell -Command \"$env:PATH -split ';'\""}
```

### Kill a process by port
```json
{"operation": "start", "name": "kill-port", "command": "powershell -Command \"Stop-Process -Id (Get-NetTCPConnection -LocalPort 8080).OwningProcess -Force\""}
```

### Download a file
```json
{"operation": "start", "name": "download", "command": "powershell -Command \"Invoke-WebRequest -Uri 'https://example.com/file.zip' -OutFile 'file.zip'\""}
```

## Key Differences from Bash

| Task | Bash | PowerShell |
|------|------|------------|
| List files | `ls -la` | `Get-ChildItem` or `dir` |
| Find files | `find . -name "*.py"` | `Get-ChildItem -Recurse -Filter *.py` |
| Environment | `echo $PATH` | `$env:PATH` |
| Process list | `ps aux` | `Get-Process` |
| Kill process | `kill -9 PID` | `Stop-Process -Id PID -Force` |
| Download | `curl -O url` | `Invoke-WebRequest -Uri url -OutFile file` |
| Grep | `grep pattern file` | `Select-String -Pattern pattern -Path file` |

## Guidelines

- Always prefix PowerShell commands with `powershell -NoProfile -Command "..."` when running via process_manager
- Use `-ErrorAction SilentlyContinue` to suppress non-critical errors
- Use `Select-Object` to limit output columns
- Use `ConvertTo-Json` for structured output the agent can parse
- Escape inner quotes with backtick `` ` `` or use single quotes inside double quotes
