# Pi PowerShell (`piPowerShell`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_powershell`](../../../server/nodes/filesystem/pi_powershell/__init__.py) |
| Agent tool | `pi_powershell` |
| SDK tool | Pi 1.1.0 `powershell` |
| Approval class | send; unsafe to repeat after interruption |

Runs PowerShell on a **native Windows worker**, using the workflow workspace
as its working directory. Pi prefers `pwsh.exe`, then `powershell.exe`; this
SDK tool is unavailable on Linux or macOS. Connect its tool output to an
agent's Tools input, or execute it as a graph node.
New [Coding Agents](../specialized_agents/coding_agent.md) created on Windows
include this connection automatically; execution still requires a Windows worker.

| Parameter | Meaning |
|---|---|
| `command` | Required PowerShell command |
| `timeout` | Seconds; default 30, minimum 1, maximum 600 |

The shell starts without a profile and runs noninteractively. Commands are
open-world and can affect the host or external services, so the ordinary
command approval class applies. The trusted workspace selects only the initial
working directory; it does not sandbox the command.

[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py) invokes Pi's
actual shell tool in a separate Bun process. Timeout and cancellation request
process-tree cleanup; the Python bridge independently bounds the host's wait
and cleanup. The node's 660-second Temporal deadline leaves headroom above the
maximum command timeout. SDK output, exit and truncation information flow
through the standard node result, with nonzero exits reported as tool failures.
Full output for truncated results is retained under the workflow's `.pi-output/`
directory; returned paths can be read with [Pi Read File](./piRead.md).

An interrupted command is not automatically run again. Pi's maintenance driver
keeps built-in effects disabled; this effect belongs to the node activity.
Dependencies are shipped Bun, bundled Pi tool host, and Windows PowerShell or
PowerShell 7. No runtime package downloads occur. Existing shell nodes and
automatic workspace-tool defaults are unchanged.
