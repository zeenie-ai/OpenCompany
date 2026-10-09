# Mobile Workspace

Mobile Workspace runs the open-source **mobile-use** agent against one persistent, local Android emulator. Mobile Agent and the Android tool are plugin nodes with a Workspace capability; its task execution uses the saved workflow, node configuration, and authenticated principal. The emulator belongs to the OpenCompany installation and is shared across Mobile nodes and workflows. Tasks queue for this device.

## Supported deployment

The initial implementation supports **Android on a Windows host**. The OpenCompany backend, Android SDK, emulator, device broker, and mobile worker must run on the same host. A remote browser can display the Workspace, but does not supply the emulator. Only the installation owner can access Mobile Workspace.

iOS is not enabled. Future iOS support requires a same-host macOS backend and Apple's simulator tooling; installing this plugin on Windows does not provide an iOS simulator. Physical devices, remote device farms, multiple concurrent emulators, and distributed mobile workers are outside this implementation.

The runtime singleton and its leases are process-local. Run one backend process with its local Temporal activity worker; multiple API processes or an independently deployed Android activity worker do not share this device broker. Merely placing separate processes on the same machine does not make their runtime state shared. A future multiprocess deployment needs a dedicated broker service and explicit authenticated routing to it.

## Setup and use

1. Install `uv` for the optional Python environment and enable the host virtualization support required by Android Emulator. Setup downloads pinned Android command-line tools and a private Temurin Java runtime when needed. An existing SDK can be selected with `ANDROID_SDK_ROOT` or `ANDROID_HOME`; the standard Windows Android SDK location is also detected.
2. Add **Mobile Agent** for direct tasks or employee delegation, or **Android** for an existing AI agent to call as a tool. Save the workflow and open **Workspace → Mobile**. The setup diagnostics inspect SDK tools, emulator acceleration, the system image, engine, and video server.
3. Review and accept the Android SDK license in setup. Setup installs the optional engine and Android packages and creates the persistent `OpenCompany` AVD using a Pixel 7 device profile. Network access and sufficient disk space are required for these downloads.
4. Click **Start phone** and wait for **Ready**. The emulator runs without a separate desktop window; the Workspace shows its video. Device storage persists across stopping and starting the emulator.
5. Click **Use phone** to sign in to the Play Store, install applications, or upload an APK. APK uploads are limited to 256 MB and require the current manual-control lease. Play Store availability comes from the selected Google Play system image; account sign-in is performed on the device.
6. Connect a provider in OpenCompany Settings, then choose a supported global model in the toolbar. Phone nodes use it by default. For an override, choose **Custom** under the node’s **Model selection**, or connect exactly one model to its **Model** input. Save the workflow. The resolved model is used for every mobile-use stage. OpenAI, Anthropic, and Gemini credentials are supported. Choose a model with image input and tool use, then enter a request under **Ask AI to use the phone** and click **Run task**, or call the node through its agent connection.

The setup is optional. Ordinary application startup does not install the mobile engine or boot an emulator. Runtime data lives under the configured OpenCompany data directory's `mobile` subdirectory, including the engine environment, AVD, and device resource metadata. Stopping the emulator does not reset its apps or account sessions.

Setup shows the current component, download or SDK output, elapsed time, last update age, and recent activity. Complete compatible SDK packages are reused. Progress is saved in `mobile/setup-status.json`; reopening Workspace retains it. A backend shutdown or restart during setup is shown as interrupted with a retry action, rather than silently returning to idle. Retry reuses completed components; an incomplete download may need to restart. Setup does not continue while the backend is stopped. Timeout failures include the command and its latest output.

## Choosing a node

| Node | Use it for | Connection |
| --- | --- | --- |
| **Mobile Agent** (`mobile_use_agent`) | Direct Workspace requests or delegated phone tasks | Global model by default; optional **Model** override; **Delegate** output for employee delegation |
| **Android** (`android_tool`) | Giving an existing AI agent a phone tool | **Tool** output into the agent’s **Tools** input; global model by default, with an optional **Model** override |

Both nodes use the same phone. Adding another node does not create another emulator. The existing `android_agent` and relay service nodes are separate integrations; they do not set up this local phone.

## Everyday controls

The live phone fills the available Workspace height in normal view, inside a phone frame (a dark bezel) shaped by the screen's own width and height, so it turns when the phone rotates; wide panels have side bars. Above it the server says which phone it is (`status.device`, "Pixel 7 · local emulator · up to 30 fps", from `DEVICE_NAME` and `VIDEO_MAX_FPS` in `_install.py`, which also set the AVD profile and scrcpy's `max_fps`). Below it the device bar has **Back**, **Home**, **Recent apps** and **Rotate** (on its side, and back), each sent through `/input` and enabled while you use the phone, and **Screenshot to Canvas** where the employee has a Canvas: `POST /screenshot` saves the screen as a PNG in the workflow's workspace (`write_media`, a read that needs no control) and `canvas_add` puts it on the board.

Home's **Take over** (in the Workspace footer) takes the phone's lease the way **Use phone** does, and stops the employee. **Hand back** releases the lease with `resume: true`, so a task waiting for you goes on; when this view no longer holds the lease (it was hidden meanwhile), `POST /resume` lets a waiting task go on without it. `/resume` is refused while someone uses the phone and does nothing when no task waits. Each action an AI task takes on the phone ("Tapped the screen", "Went back"; `TASK_STEPS` and `KEY_STEPS` in `_runtime.py`), and you taking the phone over and handing it back, adds a line to the employee's Workspace timeline on Home (`services/workspace_steps.py`); a line never holds typed text or what the screen showed. Expand **Ask AI to use the phone** to enter a task; collapsing it keeps your draft. The **Phone controls** toolbar toggle exposes Stop phone, refresh, APK installation while controlling the phone, completed setup history, and help. Status and takeover stay visible.

Choose **Full view** in the toolbar to fill the screen. The task panel is hidden, and secondary controls remain available through the toolbar toggle. Controls stay outside the picture so they do not cover Android buttons. Use **Exit full view** or **Esc** to return. Entering full view keeps the current connection and does not restart the phone or grant manual control. Browser Workspace uses the same layout, with address and navigation under **Browser controls**. If the browser does not support fullscreen, use the existing Workspace expand button instead.

| Label | Meaning |
| --- | --- |
| **Phone is off / Start phone** | The phone is installed but needs to be started before tasks can run. |
| **Starting phone…** | Android and its automation connection are initializing. Wait for **Ready**. |
| **Ready** | The phone can accept a task or manual control. |
| **Use phone** | Pause AI control and use the screen yourself. |
| **Finish using phone** | Release manual control without explicitly resuming a paused task. |
| **Let AI continue** | Release manual control and resume a waiting task from the current screen. |
| **AI is working** | An agent is using the shared phone. |
| **Stop phone** | Shut down the phone while preserving apps and sign-ins. Cancel an active task first. |

Connection warnings clear after a successful status check. Action errors have a **Dismiss** button. Task submission is disabled while the phone is off. Download details, completed setup history, and technical diagnostics are collapsed by default.

## Model selection

Both Mobile Agent and Android use this order:

1. A connected **Model** node overrides all node/global settings. Multiple connections or a disabled/invalid connector produce an error rather than falling back silently.
2. With **Model selection = custom**, the saved phone provider/model is used. An empty model uses that provider’s configured default.
3. With **Model selection = global** (the default), the current toolbar selection is read for every new task. Changing it applies to the next task, not a worker already running.

The selected model serves every mobile-use stage. OpenAI, Anthropic and Gemini are supported. An unsupported or missing global selection gives an actionable error; it never silently selects a different provider. Credentials always come from stored provider settings. Android’s AI-visible tool schema remains prompt-only, so an agent cannot override model selection or saved execution limits through tool arguments.

Gemini backend selection follows the main AI agent: Vertex AI Express credentials use Vertex, and Developer API credentials use the Gemini Developer API. The isolated phone worker receives an explicit backend setting derived from the stored credential; it does not inherit an unrelated host setting. Terminal events include `model_backend=vertex_express` or `model_backend=gemini_developer`, without printing the credential. A rejected Android tool result is shown as a failed node, even though the handled error is returned to its calling AI agent.

OpenAI and Anthropic (Claude) use their own saved API credentials and official SDK endpoints by default. A saved OpenAI or Anthropic custom endpoint is passed unchanged to the phone engine, matching the main agent's provider configuration. All three model-selection modes support these providers. Choose a model that supports image input and tool use; a valid API key alone does not establish access to every model. Endpoint values and credentials are excluded from diagnostic logs. Client construction is verified with the installed engine SDKs; this does not constitute a live provider-generation test.

## Runtime files and Git

`mobile_root()` currently resolves a relative `DATA_DIR` against the backend process’s working directory. With source development launched from `server/` and `DATA_DIR=.opencompany`, the installed phone is therefore under `server/.opencompany/mobile/`. This differs from the application’s canonical repository-relative data resolver. An absolute `DATA_DIR` avoids this ambiguity. Do not move an existing installation or switch its data directory while the backend is running.

The repository ignores `server/.opencompany/`. Emulator disks, snapshots, downloaded dependencies and logs are runtime data, not source changes; deleting them is not Git cleanup. Preserve `avd/` to retain the phone’s apps and sign-ins. Test fixtures and research checkouts under `.tmp/` can be removed once their checks finish.

## Control and task lifecycle

### Android tool for AI agents

Add the **Android** tool node (`android_tool`) and connect its **Tool** output to an AI agent's **Tools** input. The tool follows the saved global model by default. Optionally choose a custom model in its settings or connect an OpenAI, Anthropic, or Gemini model node to Android’s **Model** input; a connected model takes priority. Save the workflow, then set up and start the phone in Workspace → Mobile. The agent calls `android` with one argument, `prompt`, such as “Open Settings and turn on dark mode.” The tool uses the same mobile-use engine, owner checks, task queue, manual takeover, and saved time/step limits as Mobile Agent. It does not install software or accept licenses automatically.

### Troubleshooting and logs

Workspace shows plain-language phone status and clears connection warnings after a successful status check. **Use phone** enables manual interaction; **Let AI continue** returns control to a waiting task. Technical checks and recent events are under **Help & diagnostics**.

Operational events are written to `mobile/mobile.log` relative to the active data directory, rotating at 1 MiB with three backups. They include startup, driver/video failures, task lifecycle and command durations. Errors include exception type, error code where available, and stack locations. Prompts, typed text, screenshots, credentials, and command parameters are excluded. Existing emulator output remains in `mobile/emulator.log`.

| Symptom | Check or recovery |
| --- | --- |
| Setup appears slow | Expand download details and recent setup activity; large system images take time. Complete compatible SDK packages are reused. |
| Setup interrupted after a backend restart | Use **Retry setup** after accepting the SDK terms. Completed components are reused; partial downloads may restart. |
| Phone will not start | Open **Help & diagnostics** for the acceleration check, then inspect `mobile/mobile.log` and `mobile/emulator.log`. |
| `readuntil() called while another coroutine is already waiting` during startup | Older code let status polling read the driver’s initialization pipe concurrently. The fix shares `driver_lock` across initialization and commands and skips geometry polling during lifecycle transitions. Restart the backend after updating. |
| Phone works but an old error remains | Current UI clears recovered connection warnings; dismiss action errors. Refresh the frontend after updating. |
| New Android node is absent | Restart the backend to register the plugin and refresh the frontend. Search for **Android**, type `android_tool`. |
| Task asks for a model | Select a supported global model, choose a custom provider/model in the phone’s settings, or connect a model to the phone’s **Model** input. A parent agent’s separate custom model is not inherited automatically. |
| Live view cannot connect | Check recent video events. Only one viewer is supported; close another preview and use **Reconnect**. |

`setup-status.json` retains setup progress across backend restarts. Recent diagnostic events in status are bounded to 40 records in memory; `mobile.log` and its rotated backups retain operational history on disk. Logs exclude user content, but still include workflow/run identifiers: review them before sharing. The startup race explains a failed request and phone cleanup; it does not by itself prove a whole-backend crash.

Video is a read-only scrcpy transport. Mouse, touch, text, navigation, rotation, APK installation, and agent mutations go through the server's device-control broker rather than the video socket. The initial preview supports one browser tab at a time.

Taking control revokes the agent's lease and capability before stopping its worker, then waits for an already admitted device mutation to drain. A monotonically increasing epoch rejects delayed inputs from an old controller. Operation identifiers deduplicate successful mutations within the current lease. An uncertain device failure fences control and requires recovery rather than replaying the mutation automatically.

Resuming starts a fresh agent worker against the current device state with a new capability. This is not continuation of an in-memory model conversation. The remaining active time and step budgets are carried forward. A task can wait for human control for up to 30 minutes. Cancelling a queued task prevents it from starting; cancelling a task while a person controls the device preserves that person's lease.

Workspace submissions use a client-generated submission UUID and a durable Temporal workflow identity scoped to principal, saved workflow, and node. Repeating a submission returns the existing task; reusing its UUID with a different prompt is rejected. Mobile task activities have automatic retries disabled. This is not a guarantee that arbitrary device actions are globally exactly-once across host failure.

### Resetting phone tasks

Use the workflow toolbar's **Reset** for phone tasks submitted through **Ask AI to use the phone**, as well as phone work invoked by the workflow. Direct Workspace submissions are tracked by a per-workflow Temporal `WorkspaceTaskControllerWorkflow`, so Reset is available even when the regular workflow has never started.

Reset blocks new Workspace submissions, cancels queued and running tasks for this workflow, and waits for cancellation and phone cleanup before reporting completion. Other workflows' phone tasks are unaffected. If cleanup or Temporal status is unavailable, Reset remains unconfirmed and can be retried; an already-ready workflow alone is not proof that its phone task stopped. The workflow remains stopped afterward.

The phone stays running, with its apps, sign-ins, and existing human-control lease preserved. Reset clears this workflow's task and activity panel and browser submission recovery IDs, then loads a fresh phone status. If you were controlling the phone, choose **Use phone** again to reconnect this view's manual controls. Completed task history remains in Temporal.

After upgrading to this implementation, restart the backend and its Temporal worker so they register `WorkspaceTaskControllerWorkflow` and its cleanup activity. The process-local device broker still requires the backend and mobile activity worker to share the supported single-process deployment described above.

## Engine isolation and credentials

OpenCompany installs mobile-use into its own Python 3.12 environment. The main server does not import its agent dependencies. Each task receives a separate worker process; a trusted device process owns the explicitly selected emulator serial. The worker's injected controller talks to a loopback capability broker. It does not choose an ADB serial or receive an arbitrary host-command API.

A maintained source patch injects the controller before mobile-use's normal device initialization and redirects screenshot and platform-helper calls. It blocks SDK-side app installation and disables dotenv loading. The patch fails closed when its expected source structure changes. The upstream SDK's cancellation method alone is not used as proof that device work has stopped.

The node resolves the selected provider's existing default-account credentials on the server and passes only that provider credential in the worker configuration. Credentials are not supplied by the browser and are not included in task status. Device processes receive a filtered environment. Agent telemetry and trace recording are disabled by this integration. Task screenshots and prompts still go to the configured model provider as part of inference. Custom provider base URLs are not currently exposed by the Mobile Agent configuration.

## Dependency pins and attribution

The optional installer verifies downloaded artifacts before extraction or execution:

| Component | Pin |
| --- | --- |
| mobile-use source | `62913c933e21b27da353a89316a09f60525af496` |
| Source archive SHA-256 | `e2bac1f1d47e895d8c9ec840b0e896391ffd664ed2d4149a1ff1744252ca8384` |
| Python dependencies | Upstream lockfile, installed with `uv sync --frozen --no-dev` |
| scrcpy server | `4.1` |
| scrcpy artifact SHA-256 | `deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae` |
| Android command-line tools | Windows revision `15859902` |
| Command-line tools SHA-256 | `90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a` |
| Private Java runtime | Temurin JRE Windows x64 `21.0.12.1+1` |
| Java archive SHA-256 | `d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636` |
| Android system image | `system-images;android-36;google_apis_playstore;x86_64` |

The Android package identifier selects an SDK package, not an immutable revision checksum. Updating the engine pin requires reviewing the patch, its bypass coverage, and the upstream lockfile together. Source archive extraction preserves upstream legal files; mobile-use is Apache-2.0 licensed, and its attribution and NOTICE must remain with redistribution.

## Local integration versus Minitap services

[mobile-use](https://github.com/minitap-ai/mobile-use) is the open-source Python agent and CLI reused here. The embedded preview, lease broker, takeover behavior, optional installation, and Workspace task routing are OpenCompany integration code.

Minitap's [platform](https://www.minitap.ai/platform#slack-cli-mcp) and [miniTest documentation](https://www.minitap.ai/docs/minitest) describe separate hosted testing products, including managed runs and integrations. This local plugin does not automatically provide their device fleet, hosted test reports, Slack integration, or hosted MCP service, and does not require a Minitap account for local engine execution.

## Validation and limitations

Android tool calls publish live step counts and phases to the normal canvas node-status channel and Workspace's phone header, including calls made by another AI agent. These are internal phone steps; the parent agent advances its own turn when the tool finishes. Step counts retain the already-consumed budget after manual takeover/resume. Model waits are labelled separately from device work. The Android tool's registered `android-phone-skill` teaches goal-based calls, manual handoff, and error handling; existing workflows can add it through their Master Skill node, while new tool connections use the normal auto-add-skill policy.

Manual control avoids an extra geometry/status round trip after every input. Device geometry uses the automation server's current display dimensions, falling back to the SDK size query when missing; taps and swipes still validate current size and rotation before executing. Non-coordinate commands avoid unnecessary geometry reads, and status polling does not queue behind a busy driver. Device read/action timings remain in Terminal to distinguish device latency from model inference time. These changes do not eliminate the phone engine's multiple model calls per task.

Android lifecycle and task events appear in the application's **Terminal** under source `mobile`, prefixed `Android:`. They are also retained in the bounded `mobile.log` and Workspace's recent phone activity. For task debugging, look for `engine_starting`, `engine_stage`, `engine_failed`, and `engine_exited`; correlate them by execution ID, run ID, workflow, and node. Model events include the selected provider/model and failures include the HTTP status and engine stage. HTTP 400 means the provider rejected the request, 401/403 indicates credentials or permissions, 404 indicates a missing model, 429 indicates quota/rate limits, and 5xx indicates a provider service failure. Invalid-key responses reported as HTTP 400 are classified as authentication failures.

Raw engine stderr and provider response bodies are not forwarded because they can contain prompts, phone screenshots, or API keys. The structured worker protocol supplies diagnostic metadata instead. Earlier generic `ClientError` failures cannot be reconstructed from logs that discarded this metadata; restart the backend after updating, then retry the task to capture a diagnostic event. This logging does not include a continuous Android OS `logcat` stream.

Phone startup first checks for an existing emulator. Recovery requires the console-reported AVD path to match this installation and the process to belong to its Android SDK. A process-wide device lock prevents two updated backends from owning the same writable phone. A disconnected automation driver can be restarted without launching a second emulator. Stop waits for the emulator and cleans up its owned child processes; it does not wipe phone data or stop unrelated emulators.

If startup fails, the message remains in Workspace after a refresh. A duplicate-AVD error means another emulator still holds the phone; if verified reconnection is unavailable, close that OpenCompany emulator and retry. Do not delete AVD lock files or use `-read-only` to bypass ownership. `emulator.log` retains launcher output and rotates to `emulator.previous.log` before a new launch when it exceeds 2 MiB. Error classification reads only the current attempt; `mobile.log` records recovery and boot-probe timeout events without model prompts or credentials.

Run backend validation with a boolean `DEBUG` environment value (for example, `$env:DEBUG='false'` in PowerShell). Values such as `release` fail settings validation in subprocess tests. Source-boundary checks exclude installed runtime data in `.opencompany` and legacy `.machina`; the mobile engine runs in its own dependency environment. The Android tool intentionally receives canvas edges to resolve its connected model, and the device driver's JSON wire replies are protocol output rather than application logs.

Automated tests exercise fencing, successful-operation deduplication, cancellation while draining, takeover and resume with fake subprocesses, queued cancellation, source-patch compatibility, and Python syntax compilation of the patched upstream package. Additional tests use the actual loopback HTTP broker with a fake driver, reject unsafe archives and checksum mismatches, and verify that SDK tools run through Java arguments without a command shell. Patch tests require `.tmp/mobile-use-upstream` or `MOBILE_USE_UPSTREAM_SOURCE` pointing to the pinned checkout; they skip when that fixture is absent.

An earlier local smoke test executed `worker.main` profile construction, SDK initialization, a fake task, and cleanup using the actual pinned dependency environment in `.tmp/engine-smoke` and a fake controller. It made no model request and connected to no emulator.

A separate Windows host check confirmed usable WHPX acceleration, a real Android boot, a 1080 × 2400 automation connection, and screenshot/UI inspection, followed by clean shutdown. That sequential check did not cover concurrent Workspace polling; dedicated regression tests now cover the startup race. The temporary smoke-test fixtures were removed after use.

Automated tests also cover the Android tool’s schema and saved execution limits, privacy-filtered bounded diagnostic logging, setup interruption recovery, and connection-warning recovery in the UI. These checks do not establish successful Play Store login, APK execution, live model inference, or a complete browser-video acceptance run. Validate those on a supported Windows host before treating this as production device automation. Current task results contain a response and run identity; the artifact list is empty, and hosted-style videos or reports are not generated.

## Planned iOS implementation

This is an implementation plan, not currently supported functionality. Keep the Workspace capability, task submission, ownership checks, and lease protocol shared, and add platform-specific lifecycle, driver, installation, and video adapters.

1. Add a macOS-only setup diagnostic for Xcode selection, accepted Apple licenses, installed simulator runtimes, and the chosen simulator device type. Setup must ask for the required Apple license acceptance and must not promise unattended Xcode installation.
2. Create or select one managed simulator by persisted UDID using `xcrun simctl`; explicitly boot it, wait for readiness, and shut down only the simulator owned by this installation. Never infer the target from whichever simulator happens to be booted. Preserve simulator data across normal restarts.
3. Implement an iOS driver in a separate trusted process on the same Mac. Evaluate and pin the upstream simulator controller's IDB companion requirements. Route its screenshots, hierarchy reads, gestures, text, app lifecycle, and platform helpers through the same lease checks as Android. Do not allow the agent worker to discover devices or spawn its own companion.
4. Add a simulator video adapter with a defined framing and geometry contract. Android scrcpy is not an iOS transport. Validate encoding, browser decoding, rotation, coordinate scaling, disconnect cleanup, and backpressure on the supported macOS and browser versions before enabling live preview.
5. Accept simulator-compatible application builds and install them through a lease-protected `simctl` operation. Do not describe arbitrary physical-device IPA files or iOS App Store installation as supported simulator workflows. Display platform-specific app prerequisites and controls.
6. Extend the pinned upstream patch and smoke tests for the iOS context, then execute the acceptance matrix below on a real Mac. Keep the iOS Workspace entry disabled until the lifecycle, automation, and takeover checks pass together.

## Acceptance matrix

| Area | Automated coverage now | Required device acceptance |
| --- | --- | --- |
| Setup | Checksums, archive safety, license refusal, Java argument boundary | Clean Windows x64 setup, acceleration diagnostics, SDK download failure and retry |
| Agent adapter | Pinned-source patch compilation and real-dependency fake-task smoke | Image-capable model completes a task through the broker |
| Device lifecycle | Runtime tests use fake processes | Start, stop, crash, restart, persistent apps and session state |
| Ownership | Lease fencing, drain, deduplication, queue cancellation, scoped identities | Two browser tabs, delayed input, takeover during a real device mutation |
| Human resume | Fake subprocess termination and new capability | Agent resumes from the manually changed screen within remaining budgets |
| Video and input | Transport code is separate from mutation broker | H.264 decoding, rotation, scaled pointer coordinates, typing, disconnect cleanup |
| Android apps | APK admission and installation are broker-owned | Install a simulator-compatible APK; manually authenticate and install from Play Store |
| iOS | Not implemented | Same-Mac lifecycle, IDB adapter, simulator app install, preview, gestures, fenced takeover |

Passing a source or fake-process test does not count as passing the corresponding device acceptance check.


### Live Android activity

Workspace shows the active phone task's model/provider, step budget, total elapsed time,
current phase duration, and time since the last reported activity. The main activity panel
shows the SDK's subtask plan and current subtask, followed by the latest 40 plan/action
updates. Actions include the selected app or screen target, the SDK tool's explicit intent,
duration, and confirmed tool outcome. A tool completing an action does not mark a subtask
complete: subtask status comes from the SDK's planner/orchestrator updates. Interrupted
actions whose outcomes were not confirmed are shown as unconfirmed.

The counter is labeled **Engine step** because it measures LangGraph execution steps,
including planning and coordination, rather than taps or completed subtasks. Nested chain
callbacks and graph branches at the same step do not generate duplicate counter events.
The current subtask stays visible across model waits and device reads. A resumed worker
creates a fresh plan from the current screen; earlier activity remains in the timeline.

A separate collapsed **Technical activity** section retains the latest 40 phase/device
events: model requests/responses, screen/accessibility-tree reads, and device operations.
Consecutive identical phase messages are coalesced. Completed operations include duration.
The last task remains visible after completion, failure, or cancellation until another task
runs; history is in memory and clears on restart. Older runs cannot recover details that
were not captured at the time.

A 30-second quiet period shows a notice, not a claim that the engine is stuck. Use the last
phase to distinguish a model wait from device I/O; take manual control using Use phone if needed.
The panel is outside the phone canvas and hidden in full-screen mode. Detailed summaries
are bounded and available only through the owner-only mobile status response. They can
contain task-specific plan descriptions and screen labels. Typed tool payloads and input
tools' echoed field contents are omitted, and URL strings are redacted. These summaries
are not sent to diagnostic logs or the generic workflow status broadcast. Raw model
responses, provider thinking blocks, cumulative agent thoughts, screenshots, and injected
graph state are never forwarded to the activity panel.

This integration follows the SDK's [Agent configuration](https://www.minitap.ai/docs/mobile-use-sdk/sdk-reference/agent)
and the pinned source's `AgentConfigBuilder.with_graph_config_callbacks(...)`: graph node
results expose `subgoal_plan`, and tool callbacks expose intent/arguments plus
`Command.update.executor_messages` with `ToolMessage.status`. The SDK's internal
`on_plan_changes` hook is not exposed by this pinned Agent configuration. No additional
upstream patch or dependency is needed. [Local trace recording](https://www.minitap.ai/docs/mobile-use-sdk/core-concepts/observability)
is a separate feature that creates screenshots and step artifacts; `record_trace=False`
remains intentional for the live activity panel.

Regression coverage includes callback filtering, SDK-shaped plans, tool error results,
input-content filtering, interruption, bounded histories, and preserving subtask context
during quiet waits. An optional test executes a mock graph with the installed pinned SDK
and its real executor tool node, without a model request or device connection.
