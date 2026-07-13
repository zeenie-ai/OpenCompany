---
name: wsl-skill
description: Windows Subsystem for Linux (WSL) commands for running Linux tools on Windows.
allowed-tools: "process_manager shell"
metadata:
  author: opencompany
  version: "1.0"
  category: terminal

---

# WSL Skill (Windows Subsystem for Linux)

Use this skill when the host is **Windows** but you need Linux tools. WSL provides a full Linux environment alongside Windows.

## Detect WSL

```json
{"operation": "start", "name": "wsl-check", "command": "wsl --status"}
```
If this succeeds, WSL is available.

## Running Linux Commands from Windows

Prefix any Linux command with `wsl` to run it inside the default WSL distro:

```json
{"operation": "start", "name": "wsl-cmd", "command": "wsl bash -c 'uname -a && cat /etc/os-release'"}
```

### Install Linux packages
```json
{"operation": "start", "name": "wsl-apt", "command": "wsl sudo apt-get update && wsl sudo apt-get install -y curl jq"}
```

### Run a Linux server
```json
{"operation": "start", "name": "wsl-server", "command": "wsl python3 -m http.server 8080"}
```

### Access Windows files from WSL
Windows drives are mounted at `/mnt/`:
```json
{"operation": "start", "name": "wsl-ls", "command": "wsl ls -la /mnt/c/Users/"}
```

### Access WSL files from Windows
```json
{"operation": "start", "name": "wsl-path", "command": "wsl wslpath -w ~"}
```

## WSL Management

| Task | Command |
|------|---------|
| List distros | `wsl --list --verbose` |
| Default distro | `wsl --set-default Ubuntu` |
| Shutdown all | `wsl --shutdown` |
| Run in specific distro | `wsl -d Ubuntu bash -c "command"` |
| Check status | `wsl --status` |

## Path Conversion

| Direction | Command |
|-----------|---------|
| Windows -> WSL | `wsl wslpath -u 'C:\Users\me\file.txt'` |
| WSL -> Windows | `wsl wslpath -w '/home/me/file.txt'` |

## Networking

WSL2 shares the Windows network stack. Services started in WSL are accessible on `localhost` from Windows:
- WSL server on port 8080 -> `http://localhost:8080` from Windows browser

## Guidelines

- Always prefix with `wsl` when running from a Windows process_manager
- Use `wsl bash -c "..."` for multi-command pipelines
- Windows paths use `\`, WSL paths use `/` -- use `wslpath` to convert
- WSL has its own package manager (apt) separate from Windows
- For long-running servers in WSL, use `process_manager start` (handles lifecycle)
