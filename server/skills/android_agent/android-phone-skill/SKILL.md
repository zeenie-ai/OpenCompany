---
name: android-phone-skill
description: Use the shared local Android phone through the Android tool, with visible progress and safe manual handoff.
allowed-tools: android
metadata:
  author: opencompany
  version: "1.0"
  category: android
---

# Shared Android phone

Call `android` with a single `prompt` describing one clear goal, the relevant app, and what result to verify. The tool controls the phone visible in Workspace > Mobile. It returns the result after its internal phone steps finish.

- Use one call for a coherent task; avoid one model-driven call per tap. Each call starts a phone agent and has model latency.
- Do not invent low-level operations or pass model, credential, timeout, or step-budget arguments. These come from the saved phone settings.
- Global model selection is the default. An optional connected model overrides it. OpenAI, Claude and Gemini are supported; Gemini Express credentials automatically route to Vertex.
- A phone task is one tool call in the parent agent's turn. Its internal steps appear on the Android node and in Workspace; they are not extra parent-agent turns. Do not claim completion while the tool is still running.
- If the owner takes control, wait for them to resume the task. Ask them to enter passwords and handle interactive login themselves. Never request passwords in chat.
- If the phone is off, direct the owner to Workspace > Mobile > Start phone. Do not install another emulator or attempt raw ADB access.
- Read returned errors. Authentication, quota, unsupported-model, and connection failures need their stated remedy; do not repeat failed calls in a loop or treat an error payload as success.
- Terminal source `mobile` shows the execution ID, provider, model backend, model wait, and device timing. Report the error and relevant phase without asking the owner to reveal API keys.

Example: `{"prompt":"Open Settings, find the Android version, and report it without changing settings."}`


### Live Android activity

Workspace shows the active phone task's model/provider, step budget, total elapsed time,
current phase duration, and time since the last reported activity. The expandable activity
panel retains the latest 40 events: model requests/responses and actual device operations
(screen plus accessibility-tree reads, taps, scrolling, typing, navigation, and app actions).
Completed operations include duration. The last task remains visible after completion,
failure, or cancellation until another task runs; history is in memory and clears on restart.
A 30-second quiet period shows a notice, not a claim that the engine is stuck. Use the last
phase to distinguish a model wait from device I/O; take manual control using Take over if needed.
The panel is outside the phone canvas and hidden in full-screen mode. Activity contains
operational summaries, never private model reasoning, typed text, URLs, or prompt content.
