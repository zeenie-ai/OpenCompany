---
name: vertex-agent-skill
description: Delegate heavy tasks to a Google cloud-hosted managed agent (Antigravity) with its own sandboxed Linux environment - code execution, file management, Google Search, and URL fetching all run in Google's cloud. Use for multi-step research or compute jobs that should not run locally.
allowed-tools: "vertex_managed_agent"
metadata:
  author: machina
  version: "1.0"
  category: agents

---

# Vertex Managed Agent Skill

Delegate work to Google's **managed agent** (the Antigravity harness on
the Gemini Enterprise Agent Platform). The agent runs entirely in
Google's cloud inside an isolated Linux sandbox with Python 3.11,
Node.js, bash, file management, Google Search, and URL fetching.

## Tool: delegate_to_vertex_managed_agent

Non-blocking delegation. Two fields:

| Field | Purpose |
|---|---|
| `task` | What the cloud agent should accomplish — its system instruction for the run |
| `context` | Optional background, inputs, or data the agent needs |

The call returns immediately with `{"status": "delegated", "task_id": ...}`.
Use `check_delegated_tasks` to poll for the result — do NOT re-delegate
the same task while one is in flight.

## When to delegate to the cloud agent

- Multi-step research: search the web, fetch pages, synthesize a report.
- Compute jobs: run Python/Node code, process data, generate files —
  all inside the cloud sandbox, not on this machine.
- Long-running work you want isolated from the local workflow.

Prefer LOCAL tools (python_executor, shell, file tools) when the work
needs files in this workflow's workspace — the cloud sandbox cannot see
local files unless their content is passed in `context`.

## Behavior notes

- The cloud agent keeps its own multi-turn state: when a simpleMemory
  node is connected to the Vertex Agent, conversation and sandbox
  (installed packages, created files) persist across runs for up to
  7 days.
- Cloud-side tool usage (sandbox commands, searches) appears on the
  canvas as Cloud Tool nodes wired to the Vertex Agent — they are
  display-only indicators, safe to delete.
- Task descriptions should be self-contained: the cloud agent cannot
  ask you follow-up questions mid-run.
