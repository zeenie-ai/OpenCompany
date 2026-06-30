# Battery Monitor (`batteryMonitor`)

| Field | Value |
|------|-------|
| **Category** | android / monitoring |
| **Backend handler** | plugin [`server/nodes/android/battery_monitor/__init__.py`](../../../server/nodes/android/battery_monitor/__init__.py); dispatch via `BaseNode.execute()` -> shared [`AndroidServiceBase.invoke`](../../../server/nodes/android/_base.py) (`@Operation("invoke")`) |
| **Tests** | [`server/tests/nodes/test_android.py`](../../../server/tests/nodes/test_android.py) |
| **Skill (if any)** | [`server/skills/android_agent/battery-skill/SKILL.md`](../../../server/skills/android_agent/battery-skill/SKILL.md) |
| **Dual-purpose tool** | direct sub-node of `androidTool` - can also connect straight to any agent's `input-tools` |

## Purpose

Monitor battery status on the connected Android device: level percentage,
charging state, temperature, and health.

## Backend service mapping

| Field | Value |
|------|-------|
| Handler dispatch | uniform - see [`_pattern.md`](./_pattern.md) |
| `SERVICE_ID_MAP[batteryMonitor]` | `battery` |
| Default action | `status` |

All transport, parameter promotion, and output flattening behaviour is
identical to every other Android node. See [`_pattern.md`](./_pattern.md).

## Parameters

Only the shared parameter set; no node-specific additions. See
[`_pattern.md`](./_pattern.md#shared-parameter-set).

## Logic Flow (node-specific slice)

```mermaid
flowchart TD
  A[Dispatch to handle_android_service] --> B[service_id = 'battery']
  B --> C{action}
  C -- status --> D[POST /api/battery {action: status, parameters}]
  C -- other --> D
  D --> E[AndroidService.execute_service envelope]
  E --> F[NodeExecutor flattens data.battery_level,<br/>data.charging, data.temperature, data.health<br/>to top level of output]
```

## Output payload (typical, after flatten)

```ts
{
  service_id: 'battery',
  action: 'status',
  battery_level: number,     // promoted from data
  charging: boolean,         // promoted from data
  temperature: number,       // promoted from data
  health: string,            // promoted from data
  data: { ... },             // original nested block still present
  response_time: number,
  android_host: string,
  android_port: number,
  timestamp: string
}
```

## Edge cases & known limits

- Exact `data` keys depend on the device-side handler; the flatten logic
  blindly promotes whatever the device returns.
- Relay mode returns the same shape but via `_execute_via_relay`.
- See [`_pattern.md`](./_pattern.md#known-inconsistencies--edge-cases) for
  shared edge cases.

## Related

- Skill: [`battery-skill/SKILL.md`](../../../server/skills/android_agent/battery-skill/SKILL.md)
- Shared pattern: [`_pattern.md`](./_pattern.md)
