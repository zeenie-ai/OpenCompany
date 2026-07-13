---
name: bash-skill
description: Linux/macOS Bash commands and patterns for process management, file operations, and system administration.
allowed-tools: "process_manager shell"
metadata:
  author: opencompany
  version: "1.0"
  category: terminal

---

# Bash Skill (Linux / macOS)

Use this skill when the host system is **Linux or macOS**. Commands run via the `process_manager` tool (which has full PATH access).

## Detect Linux/macOS

Before using Bash commands, verify the OS:
```json
{"operation": "start", "name": "os-check", "command": "uname -s"}
```
`Linux` = Linux, `Darwin` = macOS. If neither, use the powershell skill.

## Common Patterns

### Install packages

**npm:**
```json
{"operation": "start", "name": "npm-install", "command": "npm install express"}
```

**pip:**
```json
{"operation": "start", "name": "pip-install", "command": "pip3 install flask"}
```

**apt (Debian/Ubuntu):**
```json
{"operation": "start", "name": "apt-install", "command": "sudo apt-get install -y curl jq"}
```

**brew (macOS):**
```json
{"operation": "start", "name": "brew-install", "command": "brew install jq"}
```

### Start a dev server
```json
{"operation": "start", "name": "dev-server", "command": "npm run dev"}
```

### Check what's running on a port
```json
{"operation": "start", "name": "port-check", "command": "lsof -ti :3000"}
```

### Find files
```json
{"operation": "start", "name": "find-py", "command": "find . -name '*.py' -type f"}
```

### Search file contents
```json
{"operation": "start", "name": "grep-todo", "command": "grep -rn 'TODO' --include='*.py' ."}
```

### Disk usage
```json
{"operation": "start", "name": "disk", "command": "df -h && echo '---' && du -sh ./*"}
```

### Environment
```json
{"operation": "start", "name": "env", "command": "echo $PATH | tr ':' '\\n'"}
```

### Kill process by port
```json
{"operation": "start", "name": "kill-port", "command": "kill -9 $(lsof -ti :8080)"}
```

### Download a file
```json
{"operation": "start", "name": "download", "command": "curl -fsSL -o file.zip https://example.com/file.zip"}
```

### Watch a log file
```json
{"operation": "start", "name": "tail-log", "command": "tail -f /var/log/app.log"}
```

## macOS-Specific

| Task | Command |
|------|---------|
| Open in Finder | `open .` |
| System info | `sw_vers` |
| Package manager | `brew install/uninstall` |
| Services | `launchctl list` |

## Linux-Specific

| Task | Command |
|------|---------|
| Package manager (Debian) | `apt-get install` |
| Package manager (RHEL) | `dnf install` |
| Services | `systemctl status/start/stop` |
| System info | `cat /etc/os-release` |

## Guidelines

- Use `&&` to chain commands (second runs only if first succeeds)
- Use `||` for fallbacks (`command1 || command2`)
- Redirect stderr: `command 2>&1`
- Background with `&` does NOT work in process_manager -- use a separate `start` call instead
- Use `set -e` at the start of multi-line scripts to fail fast
- Prefer `$()` over backticks for command substitution
