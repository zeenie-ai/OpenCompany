# Pi Bash (`piBash`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_bash`](../../../server/nodes/filesystem/pi_bash/__init__.py) |
| Agent tool | `pi_bash` |
| SDK tool | Pi 1.1.0 `bash` |
| Approval class | send; unsafe to repeat after interruption |

Runs a Bash command with the workflow workspace as its working directory.
New [Coding Agents](../specialized_agents/coding_agent.md) include this node connected to their Tools input.
Connect its tool output to an agent's Tools input, or execute it as a graph node.
Commands can access the host filesystem and external services; this is an
open-world command runner, not a filesystem sandbox.

| Parameter | Meaning |
|---|---|
| `command` | Required Bash command |
| `timeout` | Seconds; default 30, minimum 1, maximum 600 |

On Windows, Pi resolves Git Bash or another `bash.exe` on PATH. Linux and macOS
use `/bin/bash`, then Bash on PATH, then `sh`.
The node runs through
[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py), which invokes
the SDK's actual shell implementation in a separate Bun process.

Pi streams output internally, attempts process-tree termination on timeout or
cancellation, and releases quiet inherited output pipes after the shell exits.
The Python bridge also enforces a wall deadline and bounded host cleanup. The
node's 660-second Temporal deadline leaves cleanup time beyond the maximum
command timeout. Output and SDK exit/truncation details are returned through
the standard node/tool path; a nonzero exit is a tool failure.
Full output for truncated results is saved under `.pi-output/` in the workflow
workspace. Returned paths can be read with [Pi Read File](./piRead.md); the
terminal shows the SDK's bounded output tail.

The normal command approval and interrupted-call guard remain active. Retrying
an activity does not authorize rerunning an uncertain command. The maintenance
driver keeps its built-in effects disabled. Existing `shell` nodes and automatic
workspace defaults retain their current implementation.

Dependencies: shipped Bun, bundled Pi host, and an available Bash-compatible
shell. Nothing is downloaded automatically when the tool runs.
