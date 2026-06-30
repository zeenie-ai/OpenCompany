# Autonomous Agent (`autonomous_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/autonomous_agent/__init__.py`](../../../server/nodes/agent/autonomous_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.purple` |
| **Icon** | target (U+1F3AF) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for autonomous multi-step operations using the
Code Mode patterns (agentic loops, progressive discovery, error recovery,
multi-tool orchestration). Claims 81-98% token savings when paired with
the autonomous skill pack.

## What is unique to this node

- **Intended tool set**: code executors + filesystem + HTTP, typically
  combined so the agent writes code to orchestrate other tools.
- **Intended skills**: `server/skills/autonomous/` (code-mode-skill,
  agentic-loop-skill, progressive-discovery-skill, error-recovery-skill,
  multi-tool-orchestration-skill).
- **Frontend theming**: purple dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**. Routes through the
same `SpecializedAgentBase.execute_op` -> `execute_chat_agent` -- the
"autonomous" behaviour comes entirely from the attached skill content, not a
different execution engine. (The plugin declares a `tool_description` for when
it is delegated to by a team lead.)

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/autonomous/`](../../../server/skills/autonomous/)
- **Architecture**: [Autonomous Agent Creation](../../autonomous_agent_creation.md)
