# Network Monitor (`networkMonitor`)

| Field | Value |
|------|-------|
| **Category** | android / monitoring |
| **Backend handler** | plugin [`server/nodes/android/network_monitor/__init__.py`](../../../server/nodes/android/network_monitor/__init__.py); dispatch via `BaseNode.execute()` -> shared [`AndroidServiceBase.invoke`](../../../server/nodes/android/_base.py) (`@Operation("invoke")`) |
| **Tests** | [`server/tests/nodes/test_android.py`](../../../server/tests/nodes/test_android.py) |
| **Skill (if any)** | none |
| **Direct agent tool** | connectable directly to any agent's `input-tools` |

## Purpose

Report network connectivity state (online / offline), connection type
(wifi / cellular / ethernet), and internet availability.

## Backend service mapping

| Field | Value |
|------|-------|
| `SERVICE_ID_MAP[networkMonitor]` | `network` |
| Default action | `status` |

## Parameters

Shared parameter set only. See [`_pattern.md`](./_pattern.md#shared-parameter-set).

## Logic Flow (node-specific slice)

```mermaid
flowchart TD
  A[Dispatch] --> B[service_id = 'network']
  B --> C[POST /api/network {action, parameters}]
  C --> D[Flatten data: network_type, connected, internet_available]
```

## Edge cases & known limits

- Same shared edge cases as [`_pattern.md`](./_pattern.md#known-inconsistencies--edge-cases).

## Related

- Shared pattern: [`_pattern.md`](./_pattern.md)
- Sibling monitors: [`batteryMonitor`](./batteryMonitor.md), [`systemInfo`](./systemInfo.md), [`location`](./location.md)
