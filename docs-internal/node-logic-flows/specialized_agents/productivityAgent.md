# Productivity Agent (`productivity_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/productivity_agent/__init__.py`](../../../server/nodes/agent/productivity_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.cyan` |
| **Icon** | clock (U+23F0) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for Google Workspace productivity flows: email,
calendar, drive, sheets, tasks, contacts.

## What is unique to this node

- **Intended tool set**: `googleGmail`, `googleCalendar`, `googleDrive`, `googleSheets`, `googleTasks`,
  `googleContacts`, plus scheduling tools (`timer`, `cronScheduler`).
- **Intended skills**: `server/skills/productivity_agent/` (gmail-skill,
  calendar-skill, drive-skill, sheets-skill, tasks-skill, contacts-skill).
- **Frontend theming**: cyan dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/productivity_agent/`](../../../server/skills/productivity_agent/)
