# Coding Agent (`coding_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/coding_agent/__init__.py`](../../../server/nodes/agent/coding_agent/__init__.py) defines the node; new executions route to [`PiCodingAgentWorkflow`](../../../server/services/temporal/pi_workflow.py). [`SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) remains the legacy native operation. |
| **Theme color** | `dracula.cyan` |
| **Icon** | laptop (U+1F4BB) |
| **Tests** | [`test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py), [`test_pi_tools.py`](../../../server/tests/nodes/test_pi_tools.py), [`test_agent_creation.py`](../../../server/tests/routers/test_agent_creation.py), [`test_agent_builder.py`](../../../server/tests/nodes/test_agent_builder.py), [`test_team_recipe.py`](../../../server/tests/services/employees/test_team_recipe.py) |

## Purpose

Coding Agent uses the pinned Pi SDK for newly admitted executions, with
Temporal retaining model calls, tool effects, approvals, Context and durable
Stop/Resume. Existing native generations retain their recorded runtime.
`aiAgent` and the other specialist runtimes are unchanged.

## What is unique to this node

- **Tool mode**: `connected` (default) exposes connected tools and scoped
  progressive skills, including the Pi nodes created with a new agent.
  `workspace` additionally fills missing legacy `fileRead`,
  `fileModify`, `fsSearch` and `shell` capabilities in the workflow workspace.
  A connected node of the same type overrides its automatic counterpart;
  `piRead` and `piBash` are distinct types and do not suppress `fileRead` or
  `shell`. Connect executors or `processManager` explicitly when needed.
- **Runtime**: `PiCodingAgentWorkflow` with a private Bun SDK driver. New
  executions require Temporal, including manual runs and delegated work.
- **Intended skills**: `server/skills/coding_agent/` (python-skill,
  javascript-skill, file-read-skill, file-modify-skill, fs-search-skill).
- **Frontend theming**: cyan dracula accent.

## Default connected tools

Adding a Coding Agent creates its own Context and the following saved tool
nodes, wired from `output-tool` to its `input-tools`:

| Node | Callable | Default platform |
|---|---|---|
| [Pi Read File](../code_fs_process/piRead.md) (`piRead`) | `pi_read` | All |
| [Pi Write File](../code_fs_process/piWrite.md) (`piWrite`) | `pi_write` | All |
| [Pi Edit File](../code_fs_process/piEdit.md) (`piEdit`) | `pi_edit` | All |
| [Pi Bash](../code_fs_process/piBash.md) (`piBash`) | `pi_bash` | All; Windows requires Bash installed |
| [Pi Search Text](../code_fs_process/piGrep.md) (`piGrep`) | `pi_grep` | All |
| [Pi Find Files](../code_fs_process/piFind.md) (`piFind`) | `pi_find` | All |
| [Pi List Directory](../code_fs_process/piLs.md) (`piLs`) | `pi_ls` | All |
| [Pi PowerShell](../code_fs_process/piPowerShell.md) (`piPowerShell`) | `pi_powershell` | Windows only |

The editor uses the backend `createsAgentBundle` hint to call `/api/agents`.
That API, Agent Builder's `add_subagent`, and Hire's coding specialist all
use [`coding_agent_additions`](../../../server/services/coding_agent_recipe.py).
Saved graph additions commit nodes, edges and parameter rows together; retried
creation requests retain their original identities. Agent Builder preserves
explicitly requested tool parameters and omits a duplicate default of the same
type. Its employee permission scope includes the complete tool bundle. Required
tools must pass operator policy, and Hire also enforces its positive allowlist.

The default Pi bundle is installed at creation. Loading or importing an
existing graph preserves its saved connections, including any removals.
On a managed team, the owner can later ask Talk to equip the same Coding
Agent. Builder reuses its node ID
and applies the requested tools, settings or skills. For example, asking for
web search on the coder adds a private search node. After Apply the lead can
assign work to the equipped coder through its Task Manager.

For an optional planning checklist, connect the existing
[Write Todos](../ai_tools/writeTodos.md) node (`write_todos`). It uses the same
connected-tool path and provides a live editable checklist scoped to that
workflow and node. Its state is held in server memory. It is not part of this
default bundle, and the pinned Pi core tool set has no Todo tool.

## Behaviour

Preparation, provider settings, handles, skills and the output envelope follow
the **[Generic Specialized Agent Pattern](./_pattern.md)**. Pi owns each
assistant/tool round; existing Temporal leaf Activities own all business I/O.
The SDK receives no credentials and discovers no ambient project/home tools.
Pi tool nodes execute the SDK's actual implementations in a separate Bun leaf
host under the normal node activity, approval and interruption rules. The Pi
agent driver keeps built-in effects disabled and invokes these connected nodes
through Temporal. Existing `shell` nodes and legacy workspace tools retain
their implementation.

See **[Pi Coding Agent](../../pi_coding_agent.md)** for protocol, recovery,
packaging and rollout. Models continue through OpenCompany's existing native
providers and gateway. Pi has its own retries, compaction and background cache
warming disabled; OpenCompany retains those policies.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/coding_agent/`](../../../server/skills/coding_agent/)
