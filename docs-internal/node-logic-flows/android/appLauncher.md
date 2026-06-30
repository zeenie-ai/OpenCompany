# App Launcher (`appLauncher`)

| Field | Value |
|------|-------|
| **Category** | android / apps |
| **Backend handler** | plugin [`server/nodes/android/app_launcher/__init__.py`](../../../server/nodes/android/app_launcher/__init__.py); dispatch via `BaseNode.execute()` -> shared [`AndroidServiceBase.invoke`](../../../server/nodes/android/_base.py) (`@Operation("invoke")`) |
| **Tests** | [`server/tests/nodes/test_android.py`](../../../server/tests/nodes/test_android.py) |
| **Skill (if any)** | [`server/skills/android_agent/app-launcher-skill/SKILL.md`](../../../server/skills/android_agent/app-launcher-skill/SKILL.md) |
| **Dual-purpose tool** | sub-node of `androidTool`; connectable directly to any agent's `input-tools` |

## Purpose

Launch an Android application by package name (e.g. `com.whatsapp`).

## Backend service mapping

| Field | Value |
|------|-------|
| `SERVICE_ID_MAP[appLauncher]` | `app_launcher` |
| Default action | `launch` |

## Parameters

In addition to the shared set:

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `package_name` | string | `""` | **conditional** | `action: ['launch']` | Target package name. Handler promotes this from the root-level params into the nested `parameters` dict before dispatch (see [`_pattern.md`](./_pattern.md#shared-parameter-set)). |

## Logic Flow (node-specific slice)

```mermaid
flowchart TD
  A[Dispatch] --> B[service_id = 'app_launcher']
  B --> C[Promote package_name into parameters dict]
  C --> D{action}
  D -- launch --> E[POST /api/app_launcher<br/>{action: launch, parameters: {package_name}}]
  D -- other --> E
  E --> F[Flatten data: launched, package_name, success]
```

## Edge cases & known limits

- The handler does **not** validate that `package_name` is non-empty; an empty
  value is forwarded verbatim and the device reports the error.
- If the app is not installed the device returns `success=false` with the
  error surfaced in the envelope.
- Shared edge cases from [`_pattern.md`](./_pattern.md#known-inconsistencies--edge-cases).

## Related

- Skill: [`app-launcher-skill/SKILL.md`](../../../server/skills/android_agent/app-launcher-skill/SKILL.md)
- Sibling: [`appList`](./appList.md) - to enumerate installed packages
- Shared pattern: [`_pattern.md`](./_pattern.md)
