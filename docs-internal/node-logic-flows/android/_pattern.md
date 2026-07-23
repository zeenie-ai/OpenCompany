# Android Service Nodes - Shared Pattern

All 16 Android service nodes share a single backend handler, a single frontend
factory, and a single execution path. This document is the authoritative
description of that shared behaviour. Individual per-node docs link back here
for everything that is not node-specific.

## Single plugin base, registry-driven dispatch

Wave 11.C migrated all 16 Android nodes to self-contained plugin folders
under `server/nodes/android/<service>/__init__.py`. Each is a trivial
subclass of the shared `AndroidServiceBase` (`server/nodes/android/_base.py`)
that sets `type` / `display_name` / `description` / `tool_name` only. The old
`server/services/handlers/android.py::handle_android_service` and
`server/services/android_service.py` files are **deleted**. Dispatch flows
through `BaseNode.execute()` -> the `@Operation("invoke")` method on
`AndroidServiceBase`.

| Concern | Location |
|---------|----------|
| Plugin base | [`server/nodes/android/_base.py::AndroidServiceBase.invoke`](../../../server/nodes/android/_base.py) (`@Operation("invoke")`) |
| Underlying service | [`server/nodes/android/_dispatcher.py::AndroidService.execute_service`](../../../server/nodes/android/_dispatcher.py) |
| Registry binding | plugins self-register via `BaseNode.__init_subclass__`; merged into `NodeExecutor` via `_PLUGIN_HANDLERS` ([`server/services/node_executor.py`](../../../server/services/node_executor.py) line 124) |
| Output flatten | [`server/services/node_executor.py`](../../../server/services/node_executor.py) lines 173-181 |

The `@Operation("invoke")` method maps the incoming `node_type` (camelCase) to a
backend `service_id` (snake_case) via the `SERVICE_ID_MAP` dict in `_base.py`:

```python
SERVICE_ID_MAP = {
    'batteryMonitor': 'battery',
    'networkMonitor': 'network',
    'systemInfo': 'system_info',
    'location': 'location',
    'appLauncher': 'app_launcher',
    'appList': 'app_list',
    'wifiAutomation': 'wifi_automation',
    'bluetoothAutomation': 'bluetooth_automation',
    'audioAutomation': 'audio_automation',
    'deviceStateAutomation': 'device_state',
    'screenControlAutomation': 'screen_control',
    'airplaneModeControl': 'airplane_mode',
    'motionDetection': 'motion_detection',
    'environmentalSensors': 'environmental_sensors',
    'cameraControl': 'camera_control',
    'mediaControl': 'media_control',
}
```

The frontend hidden `service_id` param is **ignored** for known node types;
the plugin's own map wins (`SERVICE_ID_MAP.get(self.type, ...)`). See "Known
inconsistencies" below.

## Shared parameter set

| Name | Type | Default | Required | Description |
|------|------|---------|----------|-------------|
| `service_id` | hidden | per-node (see frontend) | auto | Unused for dispatch; handler uses its own map |
| `android_host` | hidden | `localhost` | no | Target host for local ADB mode |
| `android_port` | hidden | `8888` | no | Target port for local ADB mode |
| `label` | string | display name | no | Custom label, cosmetic only |
| `action` | options | per-node default | **yes** | Action to perform - loaded dynamically from `/api/android/services/<service_id>/actions` |
| `parameters` | dict or JSON string | `{}` | no | Per-action nested parameters |
| `package_name` | string | `""` | conditional | Only surfaced by `appLauncher`; handler promotes it into `parameters` automatically |

Root-level `package_name` is hoisted into `parameters` before dispatch by the
`invoke` method (the only key promoted today).

If `parameters` is received as a string it is coerced via `json.loads()` —
first in `AndroidServiceParams._coerce_parameters` (a `field_validator(mode="before")`)
and again defensively inside `invoke`; on `JSONDecodeError` the value is
silently replaced with `{}`.

## Execution paths

`AndroidService.execute_service` picks one of two transports at call time:

- **Relay path**: if `get_current_relay_client().is_paired()` returns a paired
  client, the call is forwarded to the remote device over the relay WebSocket
  via `_execute_via_relay(...)`.
- **Local HTTP path**: otherwise `httpx.AsyncClient` posts
  `{"action": action, "parameters": parameters}` to
  `http://{android_host}:{android_port}/api/{service_id}` with the service's
  configured `default_timeout`.

```mermaid
flowchart TD
  A[AndroidServiceBase.invoke @Operation] --> B[Look up service_id via SERVICE_ID_MAP self.type<br/>fallback to parameters.service_id then 'battery']
  B --> C[Parse parameters<br/>coerce JSON string, promote package_name]
  C --> D[android_service.execute_service<br/>node_id, service_id, action, parameters, host, port]
  D --> E{relay client paired?}
  E -- yes --> F[_execute_via_relay]
  E -- no --> G[POST /api/{service_id} on android_host:android_port]
  G -- 200 --> H[Return success + data]
  G -- non-200 --> I[Return success=false + HTTP error]
  G -- httpx.ConnectError --> J[Return success=false + 'Cannot connect...']
  G -- httpx.TimeoutException --> K[Return success=false + 'Request timeout...']
  G -- other Exception --> L[Return success=false + str(e)]
  H --> M[NodeExecutor flattens result.data to top level]
```

## Output envelope

`AndroidService.execute_service` returns:

```ts
{
  success: boolean,
  node_id: string,
  node_type: 'androidService',
  result: {
    service_id: string,
    action: string,
    data: object,                // raw service response under 'data' key
    response_time: number,       // httpx response elapsed seconds
    android_host: string,
    android_port: number,
    timestamp: string            // ISO8601
  },
  execution_time: number,
  timestamp: string
}
```

`NodeExecutor` then **flattens** the `result.data` contents to the top level of
`result` before writing to the output store, so downstream templates can use
`{{batteryMonitor.battery_level}}` instead of
`{{batteryMonitor.data.battery_level}}`. The original `data` key is preserved
alongside the promoted fields. Metadata keys (`service_id`, `action`,
`response_time`, `android_host`, `android_port`, `timestamp`) are also
preserved.

## Side effects

- **External HTTP**: `POST http://{android_host}:{android_port}/api/{service_id}` (local path)
- **External WebSocket**: relay RPC call when a paired client exists
- **Database writes**: none in the handler itself; the `NodeExecutor` writes
  three output-store keys (`output_main`, `output_top`, `output_0`) with the
  flattened payload.
- **Broadcasts**: none direct from the handler. Status broadcasts are emitted
  by the outer executor (not by this handler).
- **File I/O**: none.
- **Subprocess**: none direct (any ADB port forwarding is set up out-of-band
  via the Android router, not by this handler).

## External dependencies

- **Transport selection**: `services.android.get_current_relay_client` at call time
- **Local mode**: `httpx.AsyncClient` with `self.default_timeout` (see
  `AndroidService.__init__`)
- **Connection state**: Android service node execution requires `paired=true`
  in relay mode. The relay `connected` state alone is not sufficient.
- **Environment variables**: none; host/port come from hidden node params.

## Known inconsistencies & edge cases

1. **Frontend/backend service_id mismatch for four nodes**. The frontend
   `createAndroidServiceNode` factory stores the following serviceIds as hidden
   params:
   - `deviceStateAutomation` -> `device_state_automation`
   - `screenControlAutomation` -> `screen_control_automation`
   - `airplaneModeControl` -> `airplane_mode_control`

   The handler's `SERVICE_ID_MAP` overrides those with:
   - `deviceStateAutomation` -> `device_state`
   - `screenControlAutomation` -> `screen_control`
   - `airplaneModeControl` -> `airplane_mode`

   The plugin map wins because it is looked up first, but any consumer that
   reads the hidden param directly would see a different value. The same
   discrepancy exists in the AI-tool path
   `server/nodes/android/_base.py::execute_android_service_tool`.
2. **JSON parse failure is silent**. A `parameters` string that fails to parse
   is replaced with `{}` and logged only at debug level.
3. **No validation of required action params**. `appLauncher` needs
   `package_name`, but the plugin never checks for it - an empty value is
   passed through and the Android side is expected to error.
4. **`default_timeout` is service-instance-wide**. All sensor / camera / media
   calls share the same timeout; there is no per-action override.
5. **Output flatten can shadow metadata**. If the device returns a `data`
   dict whose keys collide with `result` metadata keys (`action`,
   `service_id`, `timestamp`, ...), the flatten merges `data` **after**
   metadata, so the data wins and the metadata is lost from the promoted view.
6. **`execute_service` never raises, but `invoke` does on failure**.
   `AndroidService.execute_service` returns a `success=false` envelope for
   every exception path. The `@Operation("invoke")` wrapper then re-raises a
   `RuntimeError` when `result["success"] is False` (using `result["error"]`),
   so `BaseNode.execute()` produces the standard error envelope. On success
   `invoke` returns `result["result"]` (or the whole dict if no `result` key).

## Related

- Android service infrastructure: [`server/nodes/android/`](../../../server/nodes/android/)
- Status broadcaster (two-state model): [Status Broadcaster](../../status_broadcaster.md)
- Android-agent skills: [`server/skills/android_agent/`](../../../server/skills/android_agent/)
- AI-tool dispatch (direct service tools; the `androidTool` aggregator was
  retired): `execute_android_service_tool` in
  [`server/nodes/android/_base.py`](../../../server/nodes/android/_base.py),
  called from
  [`server/services/handlers/tools.py::execute_tool`](../../../server/services/handlers/tools.py)
