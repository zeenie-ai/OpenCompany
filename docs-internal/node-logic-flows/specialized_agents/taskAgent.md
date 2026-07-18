# Task Management Agent (`task_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/task_agent/__init__.py`](../../../server/nodes/agent/task_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.purple` |
| **Icon** | clipboard (U+1F4CB) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for task scheduling and planning. Typical tool
connections: `timer`, `cronScheduler`, `taskManager`, `writeTodos`.

## What is unique to this node

- **Intended tool set**: scheduling, reminders, todo planning.
- **Intended skills**: `server/skills/task_agent/` (timer-skill,
  cron-scheduler-skill and write-todos-skill). Task Manager guidance now
  lives in the AI Assistant skill catalog as `task-manager`.
- **Frontend theming**: purple dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/task_agent/`](../../../server/skills/task_agent/)
