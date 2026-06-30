# Tool Agent (`tool_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/tool_agent/__init__.py`](../../../server/nodes/agent/tool_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.yellow` |
| **Icon** | wrench (U+1F527) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

Domain-agnostic AI agent for mixing arbitrary tools. Use when the intended
tool set spans multiple categories and no other specialized agent fits.

## What is unique to this node

- **Intended tool set**: any combination of workflow tool nodes.
- **Intended skills**: none pre-assigned; user wires whatever fits.
- **Frontend theming**: yellow dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
