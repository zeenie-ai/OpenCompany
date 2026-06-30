# Consumer Agent (`consumer_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/consumer_agent/__init__.py`](../../../server/nodes/agent/consumer_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.purple` |
| **Icon** | shopping cart (U+1F6D2) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for customer support and consumer interactions
(order status, product recommendations). Like `payments_agent`, this is a
naming / theming shell around `SpecializedAgentBase` -> `execute_chat_agent`.

## What is unique to this node

- **Intended tool set**: `httpRequest` against product / order APIs,
  messaging nodes for customer replies.
- **Intended skills**: no dedicated `server/skills/consumer_agent/` folder
  ships today.
- **Frontend theming**: purple dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
