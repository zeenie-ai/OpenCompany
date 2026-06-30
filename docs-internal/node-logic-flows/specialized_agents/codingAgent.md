# Coding Agent (`coding_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/coding_agent/__init__.py`](../../../server/nodes/agent/coding_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.cyan` |
| **Icon** | laptop (U+1F4BB) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for code execution workflows. Typical tool
connections: `pythonExecutor`, `javascriptExecutor`, `typescriptExecutor`,
`shell`, `fileRead`, `fileModify`, `fsSearch`, `processManager`.

## What is unique to this node

- **Intended tool set**: code executors, filesystem tools, shell, process
  manager.
- **Intended skills**: `server/skills/coding_agent/` (python-skill,
  javascript-skill, file-read-skill, file-modify-skill, fs-search-skill).
- **Frontend theming**: cyan dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/coding_agent/`](../../../server/skills/coding_agent/)
