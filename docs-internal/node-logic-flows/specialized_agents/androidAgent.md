# Android Control Agent (`android_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/android_agent/__init__.py`](../../../server/nodes/agent/android_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Subtitle** | Device Control |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for Android device control. Users typically connect
Android service nodes (`batteryMonitor`, `wifiAutomation`, `appLauncher`,
etc.) directly to `input-tools`, or attach
the `android_agent` folder via a Master Skill.

## What is unique to this node

- **Intended tool set**: Android service nodes (16 total) and the
  independent tool capabilities.
- **Intended skills**: `server/skills/android_agent/` (12 skills:
  personality, battery, wifi, bluetooth, location, etc.).
- **Frontend theming**: green dracula accent, phone emoji in component
  palette.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)** for the full
contract: inputs, parameters, outputs, logic flow, decision logic, side
effects, edge cases. This node routes through `SpecializedAgentBase.execute_op`
-> `execute_chat_agent` with no behavioural differences from the other 12
variants.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/android_agent/`](../../../server/skills/android_agent/)
