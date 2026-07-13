# Payments Agent (`payments_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/payments_agent/__init__.py`](../../../server/nodes/agent/payments_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.green` |
| **Icon** | credit card (U+1F4B3) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for payment processing and financial workflows.
No first-party payment tools ship with OpenCompany yet, so users typically
wire `httpRequest` against a payment provider (Stripe, Razorpay) as a tool.

## What is unique to this node

- **Intended tool set**: `httpRequest` against payment APIs.
- **Intended skills**: no dedicated `server/skills/payments_agent/` folder
  ships today -- the node is a naming / theming shell around
  `SpecializedAgentBase` -> `execute_chat_agent`.
- **Frontend theming**: green dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
