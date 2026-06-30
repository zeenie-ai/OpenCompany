# WiFi Automation (`wifiAutomation`)

| Field | Value |
|------|-------|
| **Category** | android / automation |
| **Backend handler** | plugin [`server/nodes/android/wifi_automation/__init__.py`](../../../server/nodes/android/wifi_automation/__init__.py); dispatch via `BaseNode.execute()` -> shared [`AndroidServiceBase.invoke`](../../../server/nodes/android/_base.py) (`@Operation("invoke")`) |
| **Tests** | [`server/tests/nodes/test_android.py`](../../../server/tests/nodes/test_android.py) |
| **Skill (if any)** | [`server/skills/android_agent/wifi-skill/SKILL.md`](../../../server/skills/android_agent/wifi-skill/SKILL.md) |
| **Dual-purpose tool** | sub-node of `androidTool`; connectable directly to any agent's `input-tools` |

## Purpose

WiFi state control and scanning: enable, disable, get status, scan for
networks.

## Backend service mapping

| Field | Value |
|------|-------|
| `SERVICE_ID_MAP[wifiAutomation]` | `wifi_automation` |
| Default action | `status` |

## Parameters

Shared parameter set only.

## Logic Flow (node-specific slice)

```mermaid
flowchart TD
  A[Dispatch] --> B[service_id = 'wifi_automation']
  B --> C{action}
  C -- status --> D1[POST /api/wifi_automation {action: status}]
  C -- enable --> D2[POST /api/wifi_automation {action: enable}]
  C -- disable --> D3[POST /api/wifi_automation {action: disable}]
  C -- scan --> D4[POST /api/wifi_automation {action: scan}]
  D1 --> E[Flatten data]
  D2 --> E
  D3 --> E
  D4 --> E
```

## Edge cases & known limits

- Recent Android versions restrict programmatic WiFi state changes; the
  service-side action may silently no-op on Android 10+ unless a system
  signature app is used.
- Shared edge cases only otherwise.

## Related

- Skill: [`wifi-skill/SKILL.md`](../../../server/skills/android_agent/wifi-skill/SKILL.md)
- Siblings: [`bluetoothAutomation`](./bluetoothAutomation.md), [`airplaneModeControl`](./airplaneModeControl.md)
- Shared pattern: [`_pattern.md`](./_pattern.md)
