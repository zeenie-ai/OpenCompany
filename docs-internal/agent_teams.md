# Agent Teams Pattern

This document describes the Claude SDK Agent Teams pattern implementation in OpenCompany, enabling multi-agent coordination through AI Employee and Orchestrator nodes.

## Overview

The Agent Teams pattern allows a "team lead" agent to coordinate multiple specialized agents. Instead of blindly delegating to all connected agents, the team lead uses its AI capabilities to decide when and how to delegate based on the user's request.

## Team Lead Node Types

Two node types support the Agent Teams pattern:

| Node Type | Display Name | Description |
|-----------|--------------|-------------|
| `orchestrator_agent` | Orchestrator Agent | Coordinates multiple agents for complex workflows |
| `ai_employee` | AI Employee | Team lead for intelligent task delegation |

Both are defined in `TEAM_LEAD_TYPES` in [`server/nodes/agent/_inline.py`](../server/nodes/agent/_inline.py).

## Architecture

```
                    ┌─────────────────┐
                    │   AI Employee   │
                    │  (Team Lead)    │
                    └────────┬────────┘
                             │ input-teammates
           ┌─────────────────┼─────────────────┐
           │                 │                 │
    ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
    │ Web Agent   │   │Coding Agent │   │ Task Agent  │
    └─────────────┘   └─────────────┘   └─────────────┘
```

### How It Works

1. **User sends prompt** to AI Employee (e.g., "Write a Python script and search the web for documentation")

2. **Team lead collects teammates** via the `input-teammates` handle using `collect_teammate_connections()` ([`server/services/plugin/edge_walker.py`](../server/services/plugin/edge_walker.py)), invoked from `prepare_agent_call` in [`_inline.py`](../server/nodes/agent/_inline.py)

3. **Teammates become delegation tools** - each connected agent becomes a `delegate_to_*` tool:
   - `coding_agent` → `delegate_to_coding_agent` tool
   - `web_agent` → `delegate_to_web_agent` tool
   - `task_agent` → `delegate_to_task_agent` tool

4. **AI decides whether to delegate** - the team lead's LLM analyzes the request and decides:
   - For "hi" or simple questions → responds directly (no delegation)
   - For complex tasks → uses appropriate delegation tools

5. **Delegated agents execute** via the standard `_execute_delegated_agent()` flow in `handlers/tools.py` (legacy / F4.A path), OR — when the parent runs as a Temporal `AgentWorkflow` under F4.B and the child type is in `AGENT_WORKFLOW_TYPES` — as a child `AgentWorkflow` spawned via `workflow.execute_child_workflow` with `parent_node_id` plumbed through `child_context` so the parent canvas badge mirrors child progress. See [agent_delegation.md](./agent_delegation.md#overview) for both paths.

## Input Handles

Team lead agents have an additional handle compared to standard specialized agents:

| Handle | Position | Description |
|--------|----------|-------------|
| `input-main` | Left 30% | Main data input |
| `input-memory` | Left 55% | Memory node connection |
| `input-task` | Left 85% | Task completion events |
| `input-skill` | Bottom 25% | Skill nodes |
| `input-teammates` | Bottom 50% | **Specialized agents for delegation** |
| `input-tools` | Bottom 75% | Tool nodes |
| `output-top` | Top | Output |

## Backend Implementation

Wave 11 deleted `server/services/handlers/ai.py` (and `handle_chat_agent`). Agent execution now flows through each agent plugin's `execute_op` under `server/nodes/agent/<plugin>/__init__.py` (`ai_agent`, `chat_agent`) or [`server/nodes/agent/_specialized.py`](../server/nodes/agent/_specialized.py) (the 13 specialized agents share one execution body). All of them call the shared pre-dispatch helper `prepare_agent_call` in [`server/nodes/agent/_inline.py`](../server/nodes/agent/_inline.py), which (1) collects standard connections via `collect_agent_connections`, (2) injects task context, (3) auto-prompts from upstream input, and (4) for team-lead types, appends teammates as delegation tools. The prepared kwargs are then splatted into `AIService.execute_chat_agent(...)`.

### Pre-dispatch Logic ([`server/nodes/agent/_inline.py`](../server/nodes/agent/_inline.py))

```python
# Team-lead agent types where teammates become delegation tools.
TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})

async def prepare_agent_call(*, node_id, node_type, parameters, context, database, ...):
    # 5-tuple standard connection collection
    memory_data, skill_data, tool_data, input_data, task_data = (
        await collect_agent_connections(node_id, context, database, log_prefix=log_prefix)
    )

    # ... task-context injection + auto-prompt fallback ...

    # Team-lead detection - add teammates as delegation tools
    if node_type in TEAM_LEAD_TYPES:
        teammates = await collect_teammate_connections(node_id, context, database)
        if teammates:
            tool_data = tool_data or []
            for tm in teammates:
                # Each teammate's own input-tools edges are walked into
                # `child_tools` so the delegation tool's description lists
                # what that teammate can actually do.
                tool_data.append({
                    "node_id": tm["node_id"],
                    "node_type": tm["node_type"],
                    "label": tm["label"],
                    "parameters": tm.get("parameters", {}),
                    "child_tools": child_tools,
                })
            logger.info(f"[Teams] Added {len(teammates)} teammates as delegation tools")

    return {"parameters": parameters, "tool_data": tool_data or None, ...}
```

Note: `collect_agent_connections` ([`server/services/plugin/edge_walker.py`](../server/services/plugin/edge_walker.py)) returns a **5-tuple** — `(memory_data, skill_data, tool_data, input_data, task_data)`.

### Teammate Collection (`collect_teammate_connections`)

Lives in [`server/services/plugin/edge_walker.py`](../server/services/plugin/edge_walker.py):

```python
async def collect_teammate_connections(node_id, context, database):
    """Walk input-teammates edges and return connected agents.

    Used by orchestrator_agent / ai_employee.
    """
    nodes = context.get("nodes", [])
    edges = context.get("edges", [])
    teammates = []

    for edge in edges:
        if edge.get("target") != node_id or edge.get("targetHandle") != "input-teammates":
            continue
        source_id = edge.get("source")
        source_node = next((n for n in nodes if n.get("id") == source_id), None)
        if not source_node:
            continue
        node_type = source_node.get("type", "")
        if node_type not in AI_AGENT_TYPES:
            continue
        params = await database.get_node_parameters(source_id) or {}
        teammates.append({
            "node_id": source_id,
            "node_type": node_type,
            "label": source_node.get("data", {}).get("label", node_type),
            "parameters": params,
        })

    return teammates
```

## Delegation Tools

When teammates are connected, the AI service builds delegation tools via `_build_tool_from_node()` in [`server/services/ai.py`](../server/services/ai.py).

The `delegate_to_<type>` tool name is **auto-derived**, not maintained as a static map. `BaseNode.__init_subclass__` ([`server/services/plugin/base.py`](../server/services/plugin/base.py)) sets `cls.tool_name = f"delegate_to_{cls.type}"` for every plugin whose `component_kind == "agent"` that doesn't declare its own (e.g. `coding_agent` -> `delegate_to_coding_agent`). Subclasses with a distinct delegation contract (`autonomous_agent`, `orchestrator_agent`, `ai_employee`, `rlm_agent`, `claude_code_agent`) override `tool_description` on the class.

When `_build_tool_from_node` sees an agent node type listed in `_AGENT_DELEGATION_TYPES`, it exposes the `(task, context)` delegation schema instead of the agent's own Params (which would leak provider/model/prompt into the parent LLM):

```python
# server/services/ai.py
class DelegateToAgentSchema(BaseModel):
    task: str = Field(description="The mission directive for the agent (becomes its system message)")
    context: Optional[str] = Field(default=None, description="Input data / details the agent needs (becomes its user prompt)")
```

The AI receives tools like:
- `delegate_to_coding_agent(task="Write a Python script...")`
- `delegate_to_web_agent(task="Search for React documentation...")`

## Team Tracking Service

The `AgentTeamService` (`server/services/agent_team.py`) provides optional team tracking:

```python
class AgentTeamService:
    async def create_team(team_lead_node_id, teammate_node_ids, workflow_id, config)
    async def add_task(team_id, title, description, created_by, priority)
    async def claim_task(team_id, task_id, agent_node_id)
    async def complete_task(team_id, task_id, result)
    async def fail_task(team_id, task_id, error)
    async def get_team_status(team_id)
```

### Database Tables

| Table | Description |
|-------|-------------|
| `agent_teams` | Team metadata (id, workflow_id, team_lead_node_id, status) |
| `team_members` | Team membership (agent_node_id, role, status) |
| `team_tasks` | Shared task list with dependencies |
| `agent_messages` | Inter-agent communication |

## Team Monitor Node

The `teamMonitor` node displays real-time team operations:

- **Stats Grid**: Member count, task counts (total/done/active/pending)
- **Active Tasks Panel**: Currently executing tasks
- **Event Stream**: Real-time log of task completions and messages

Connect Team Monitor to a team lead's output to visualize team activity.

## Example Workflow

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│Chat Trigger │────▶│   AI Employee   │────▶│Team Monitor │
└─────────────┘     └────────┬────────┘     └─────────────┘
                             │ input-teammates
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐
       │ Coding Agent│ │ Web Agent │ │ Task Agent  │
       │ + Python    │ │ + HTTP    │ │             │
       └─────────────┘ └───────────┘ └─────────────┘
```

**User says**: "Search for React best practices and write a summary script"

**AI Employee decides**:
1. Calls `delegate_to_web_agent(task="Search for React best practices")`
2. Waits for result via `check_delegated_tasks`
3. Calls `delegate_to_coding_agent(task="Write a Python script to summarize: {web_result}")`
4. Synthesizes final response

**User says**: "Hello!"

**AI Employee decides**: Responds directly with greeting (no delegation needed)

## Key Files

| File | Description |
|------|-------------|
| [`server/nodes/agent/_inline.py`](../server/nodes/agent/_inline.py) | `TEAM_LEAD_TYPES`, `prepare_agent_call()` pre-dispatch (teammate -> delegation-tool injection) |
| [`server/services/plugin/edge_walker.py`](../server/services/plugin/edge_walker.py) | `collect_teammate_connections()` (input-teammates walk) + `collect_agent_connections()` (5-tuple) |
| [`server/services/plugin/base.py`](../server/services/plugin/base.py) | `BaseNode.__init_subclass__` auto-derives `tool_name = f"delegate_to_{type}"` for `component_kind=="agent"` |
| [`server/services/agent_team.py`](../server/services/agent_team.py) | `AgentTeamService` for team tracking |
| [`server/services/handlers/tools.py`](../server/services/handlers/tools.py) | `_execute_delegated_agent()` for actual delegation |
| [`server/services/ai.py`](../server/services/ai.py) | `_build_tool_from_node()` builds delegate_to_* tools, `DelegateToAgentSchema` |
| [`client/src/components/AIAgentNode.tsx`](../client/src/components/AIAgentNode.tsx) | Agent node rendering with `input-teammates` handle |
| [`client/src/components/TeamMonitorNode.tsx`](../client/src/components/TeamMonitorNode.tsx) | Team monitoring UI |

## Design Decisions

1. **AI-Driven Delegation**: The team lead's AI decides when to delegate, not automatic forwarding to all teammates

2. **Tool-Based Pattern**: Teammates become `delegate_to_*` tools, leveraging the existing agent-loop tool calling

3. **Fire-and-Forget**: Delegated agents run as background tasks; team lead can check status via `check_delegated_tasks`

4. **API Key Inheritance**: Teammates inherit provider/model from team lead if not configured; API keys injected via the standard `_inject_api_keys` flow in [`server/services/node_executor.py`](../server/services/node_executor.py)

5. **Standard Execution Path**: Team leads use the same `execute_chat_agent` flow as other agents, just with additional delegation tools
