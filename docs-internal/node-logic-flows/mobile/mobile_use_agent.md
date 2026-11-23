# Mobile Agent (`mobile_use_agent`)

| Field | Value |
| --- | --- |
| **Category** | agent |
| **Backend handler** | [`MobileUseAgent.execute_op`](../../../server/nodes/mobile/_node.py) via `ActionNode` |
| **Tests** | [`test_mobile_integration.py`](../../../server/tests/test_mobile_integration.py), [`test_mobile_runtime.py`](../../../server/tests/test_mobile_runtime.py) |
| **Delegation** | Explicit `supports_delegation=True` |

## Purpose

Carry out direct Workspace requests or delegated employee tasks on one persistent local Android phone, using the pinned mobile-use engine. The agent-style card is intentional. Use the separate [Android tool](android_tool.md) when connecting to an existing agent's Tools input.

## Inputs (handles)

| Handle | Required | Purpose |
| --- | --- | --- |
| `input-main` | No | Task/main workflow input |
| `input-model` | No | Optional single enabled OpenAI, Anthropic, or Gemini model connector overriding node/global selection |
| `input-context` | No | Context connection |

## Parameters

| Name | Default | Limits |
| --- | --- | --- |
| `prompt` | Empty | Required at execution; maximum 20,000 characters |
| `max_steps` | 40 | 1–200 |
| `timeout_s` | 900 | 30–3600 seconds of active task time |

Model selection defaults to `model_source="global"`, reading the toolbar’s current saved provider/model on each task. `model_source="custom"` uses the node’s `provider` and `model`; blank custom model uses the provider default. One connected model takes priority over both modes. Missing or unsupported global providers produce actionable errors. Supported providers are OpenAI, Anthropic and Gemini.

## Outputs (handles)

`output-main` returns the action result; `output-top` is the **Delegate** connection. The result payload contains `response`, `outcome`, `run_id`, and `artifacts` (currently empty), within the standard action envelope.

## Logic Flow

1. Require local installation owner access and a saved workflow/execution identity.
2. Validate the request and resolve one model from the saved connector and provider credentials.
3. Derive a scoped task identity and connect to the private loopback capability broker.
4. Queue against the shared phone, claim a control lease, and start an isolated worker with the selected model and remaining budget.
5. On manual takeover, revoke worker access and drain admitted device actions. Resume uses a fresh worker and the remaining budget.
6. When a tap by text matches nothing in the UI tree, the worker asks the broker to `locate` the element on a screenshot; a sure box from the judgment service is tapped at its centre, and anything else stays "Element not found" ([Mobile Workspace](../../../docs/mobile-workspace.md#engine-isolation-and-credentials)).
7. Return the result; revoke capability and clean up the worker on completion, cancellation or failure.

## Decision Logic

Empty prompts, missing models, non-owner access and stopped devices fail explicitly. Setup and SDK license acceptance remain UI actions. There is one shared runtime per backend process. Direct Workspace calls use durable Temporal invocation identities, and repeated submission IDs cannot change the prompt. Activity retries are disabled.

## Side Effects

Model-provider requests, screenshot/hierarchy reads, phone mutations through the broker, task worker processes and operational logs. With a TypeSafe or OpenRouter key connected, a `locate` sends that screenshot to TypeSafe or OpenRouter and books a `judge` usage row ([Judgment Service](../../judgment_service.md#where-it-is-used)). Apps and sign-ins persist in the AVD. No hosted Minitap account is required; no hosted run reports are produced.

## External Dependencies

Optional installed mobile-use environment, managed Android SDK/AVD, scrcpy video, supported model credentials, and the backend's embedded worker. Model choice applies to every mobile-use stage.

## Edge cases & known limits

Windows x64 only; one shared device and video viewer. No current iOS implementation or arbitrary emulator attachment. Multiple API processes or a separate worker process cannot share the process-local broker. Device startup serializes the driver handshake with commands; status polling reports startup without reading the initialization pipe.

## Related

- [Android tool](android_tool.md)
- [Mobile Workspace guide and iOS plan](../../../docs/mobile-workspace.md)
