# Browser AI Agent (`browser_agent`)

| Field | Value |
|---|---|
| **Implementation** | [`BrowserAgentNode`](../../../server/nodes/agent/browser_agent/__init__.py), inherited [`SpecializedAgentBase`](../../../server/nodes/agent/_specialized.py) |
| **Operation** | `execute` |
| **Tool** | `delegate_to_browser_agent` (agent delegation) |
| **Tests** | [`test_browser_agent_creation.py`](../../../server/tests/nodes/test_browser_agent_creation.py) |

## Purpose and configuration

A native agent for browser work with exactly one connected, saved `browser`
tool. It inherits the native agent provider, model, prompt, system message,
temperature, and max-token fields. Its default system message requests current
page observation, approved credential binding IDs, human help for login/MFA,
and verification before repeating an uncertain action. The creation-only
`runtime_behavior_version` is server controlled; editing/importing a node is
not permission to change its runtime identity.

The standard agent handles accept main input, skills, Context, tools and task
results. `validate_graph` refuses zero or multiple connected Browser tools
with `BROWSER_TOOL_REQUIRED`; a model credential alone is insufficient.

## Execution paths

- **Direct canvas Run / graph:** requires the saved graph and Browser tool.
  `browser_tool_id` identifies that tool; owner routing resolves its binding.
  The operation claims a stable browser task, supplies that binding to the
  native agent, and releases the claim in `finally`.
- **Agent delegation:** uses the native delegation contract and depth/control
  limits, with the same connected-tool ownership restrictions.
- **Temporal:** native agent turns and browser effects use their recorded
  workflow/activity path. Browser effects route to the Browser owner's queue;
  a remote worker is not free to open another copy of the profile.
- **Independent Workspace task:** supported (`workspace_task=True`), with its
  own task identity and lifecycle; it is separate from deployment Stop/Resume.

## Effects, outputs and failures

The browser ownership service reads saved bindings and records/releases task
claims. The native agent invokes the connected tools and model; their network,
file and approval effects remain their own contracts. Output uses the common
native-agent response envelope. Missing tools, invalid ownership and unavailable
runtime/credentials fail through the existing node/tool error path.

Cooperative Stop closes new admitted actions and drains admitted work; Resume
continues that execution. Explicit cancellation and Reset are separate. The
node's Reset hook releases an execution's browser task when exactly one tool
is identifiable; it does not invent a binding for an invalid graph.

## Rollout and limits

Default legacy agents may retain the existing resource recipe. With
`NEW_AGENT_RUNTIME_V1_ENABLED` enabled, authorized fresh creation stamps v1
and reserves private, non-transferable Browser resources. Existing/imported
agents keep their recorded identity. This is implemented rollout behavior,
rather than a promise that every Browser tool can be shared.

See [Browser deployment](../../browser_agent_deployment.md),
[Browser runtime](../../browser.md), and the
[shared agent contract](./_pattern.md).
