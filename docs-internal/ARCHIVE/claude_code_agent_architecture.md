# Claude Code Agent Architecture

> **ARCHIVED 2026-09-04 — Superseded.** LangGraph-era design material. Its one current-tense claim was wrong: the claude path is a plain subprocess over stdio pipes, not a PTY (`nodes/agent/claude_code_agent/_pool.py`); Codex now uses piped `exec --json` in `AICliSession`; its remaining PTY branch is legacy Claude code, and Gemini remains a stub. See the current [Codex guide](../codex_agent.md).
>
> For current state see:
>
> - [claude_code_interactive_mode.md](../claude_code_interactive_mode.md) — the shipped stdio protocol
> - [cli_agent_framework.md](../cli_agent_framework.md) — the generic runtime
> - [claude_code_agent.md](../claude_code_agent.md) — documentation hub

> **Current implementation note:** The production `claude_code_agent` is an
> interactive Claude Code CLI/PTY integration implemented under
> `server/nodes/agent/claude_code_agent/`. It does not use LangGraph or
> LangChain. The LangGraph diagrams and dependency snapshot below are retained
> only as historical design material and must not be used as implementation
> guidance.

## Table of Contents
1. [Memory & Context Compaction Architecture](#1-memory--context-compaction-architecture)
2. [Sub-Agent Creation & Handling](#2-sub-agent-creation--handling)
3. [Execution Flow Diagrams](#3-execution-flow-diagrams)
4. [Data Structures Reference](#4-data-structures-reference)

---

# 1. Memory & Context Compaction Architecture

## 1.1 Overview Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CONTEXT WINDOW MANAGEMENT                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                 │
│   │   Message    │    │   Message    │    │   Message    │                 │
│   │   History    │───▶│   Counter    │───▶│  Compaction  │                 │
│   │   Buffer     │    │   (Tokens)   │    │   Decision   │                 │
│   └──────────────┘    └──────────────┘    └──────┬───────┘                 │
│                                                   │                          │
│                              ┌────────────────────┴────────────────────┐    │
│                              │                                         │    │
│                              ▼                                         ▼    │
│                    ┌──────────────────┐                    ┌────────────┐   │
│                    │  BELOW THRESHOLD │                    │   ABOVE    │   │
│                    │   (< 100K tokens)│                    │ THRESHOLD  │   │
│                    └────────┬─────────┘                    └─────┬──────┘   │
│                             │                                    │          │
│                             ▼                                    ▼          │
│                    ┌──────────────────┐              ┌───────────────────┐  │
│                    │    Continue      │              │    COMPACTION     │  │
│                    │    Normal        │              │    PIPELINE       │  │
│                    │    Execution     │              │                   │  │
│                    └──────────────────┘              └───────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1.2 Token Counting Algorithm

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TOKEN COUNTING FORMULA                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   total_tokens = input_tokens                                                │
│                + cache_creation_input_tokens (or 0)                          │
│                + cache_read_input_tokens (or 0)                              │
│                + output_tokens                                               │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                     TOKEN SOURCES                                    │   │
│   ├─────────────────────┬───────────────────────────────────────────────┤   │
│   │ input_tokens        │ Tokens in all messages sent to model          │   │
│   │ cache_creation      │ Tokens written to prompt cache (first time)   │   │
│   │ cache_read          │ Tokens read from prompt cache (subsequent)    │   │
│   │ output_tokens       │ Tokens in model's response                    │   │
│   └─────────────────────┴───────────────────────────────────────────────┘   │
│                                                                              │
│   DEFAULT_THRESHOLD = 100,000 tokens                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1.3 Compaction Pipeline (Detailed)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        COMPACTION PIPELINE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  STEP 1: CHECK THRESHOLD                                                     │
│  ════════════════════════                                                    │
│                                                                              │
│      if compaction_control is None or not enabled:                           │
│          return False  ──────────────────────────────▶ [SKIP COMPACTION]     │
│                                                                              │
│      total_tokens = calculate_tokens(last_message)                           │
│      threshold = config.get("context_token_threshold", 100_000)              │
│                                                                              │
│      if total_tokens < threshold:                                            │
│          return False  ──────────────────────────────▶ [SKIP COMPACTION]     │
│                                                                              │
│                                  │                                           │
│                                  ▼                                           │
│  STEP 2: PREPARE MESSAGES                                                    │
│  ═════════════════════════                                                   │
│                                                                              │
│      messages = copy(current_messages)                                       │
│                                                                              │
│      ┌─────────────────────────────────────────────────────────────────┐    │
│      │  CRITICAL: Remove tool_use blocks from last assistant message   │    │
│      │                                                                  │    │
│      │  WHY? tool_use requires tool_result, which we don't have yet    │    │
│      │       This prevents API 400 validation errors                   │    │
│      └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│      if messages[-1]["role"] == "assistant":                                 │
│          non_tool_blocks = [b for b in content if b.type != "tool_use"]     │
│          if non_tool_blocks:                                                 │
│              messages[-1]["content"] = non_tool_blocks                       │
│          else:                                                               │
│              messages.pop()  # Remove entire message if only tool_use        │
│                                                                              │
│                                  │                                           │
│                                  ▼                                           │
│  STEP 3: APPEND SUMMARY PROMPT                                               │
│  ══════════════════════════════                                              │
│                                                                              │
│      messages.append({                                                       │
│          "role": "user",                                                     │
│          "content": SUMMARY_PROMPT  # See Section 1.4                        │
│      })                                                                      │
│                                                                              │
│                                  │                                           │
│                                  ▼                                           │
│  STEP 4: CALL MODEL FOR SUMMARY                                              │
│  ══════════════════════════════                                              │
│                                                                              │
│      response = client.beta.messages.create(                                 │
│          model = config.get("model", current_model),                         │
│          messages = messages,                                                │
│          max_tokens = params["max_tokens"],                                  │
│          extra_headers = {"X-Stainless-Helper": "compaction"}                │
│      )                                                                       │
│                                                                              │
│                                  │                                           │
│                                  ▼                                           │
│  STEP 5: REPLACE MESSAGE HISTORY                                             │
│  ════════════════════════════════                                            │
│                                                                              │
│      summary_text = response.content[0].text                                 │
│                                                                              │
│      ┌─────────────────────────────────────────────────────────────────┐    │
│      │  ENTIRE HISTORY REPLACED WITH SINGLE MESSAGE                    │    │
│      └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│      self.set_messages_params({                                              │
│          "messages": [{                                                      │
│              "role": "user",                                                 │
│              "content": [{"type": "text", "text": summary_text}]             │
│          }]                                                                  │
│      })                                                                      │
│                                                                              │
│      return True  ───────────────────────────────────▶ [COMPACTION DONE]     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1.4 Summary Prompt Structure

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DEFAULT SUMMARY PROMPT                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  "You have been working on the task described above but have not yet         │
│   completed it. Write a continuation summary that will allow you (or         │
│   another instance of yourself) to resume work efficiently in a future       │
│   context window where the conversation history will be replaced with        │
│   this summary.                                                              │
│                                                                              │
│   Your summary should be structured, concise, and actionable. Include:       │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  1. TASK OVERVIEW                                                    │   │
│   │     • The user's core request and success criteria                   │   │
│   │     • Any clarifications or constraints they specified               │   │
│   ├─────────────────────────────────────────────────────────────────────┤   │
│   │  2. CURRENT STATE                                                    │   │
│   │     • What has been completed so far                                 │   │
│   │     • Files created, modified, or analyzed (with paths if relevant)  │   │
│   │     • Key outputs or artifacts produced                              │   │
│   ├─────────────────────────────────────────────────────────────────────┤   │
│   │  3. IMPORTANT DISCOVERIES                                            │   │
│   │     • Technical constraints or requirements uncovered                │   │
│   │     • Decisions made and their rationale                             │   │
│   │     • Errors encountered and how they were resolved                  │   │
│   │     • What approaches were tried that didn't work (and why)          │   │
│   ├─────────────────────────────────────────────────────────────────────┤   │
│   │  4. NEXT STEPS                                                       │   │
│   │     • Specific actions needed to complete the task                   │   │
│   │     • Any blockers or open questions to resolve                      │   │
│   │     • Priority order if multiple steps remain                        │   │
│   ├─────────────────────────────────────────────────────────────────────┤   │
│   │  5. CONTEXT TO PRESERVE                                              │   │
│   │     • User preferences or style requirements                         │   │
│   │     • Domain-specific details that aren't obvious                    │   │
│   │     • Any promises made to the user                                  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   Be concise but complete—err on the side of including information that      │
│   would prevent duplicate work or repeated mistakes.                         │
│                                                                              │
│   Wrap your summary in <summary></summary> tags."                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1.5 Tool Runner Main Loop with Compaction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TOOL RUNNER EXECUTION LOOP                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                           ┌─────────────┐                                    │
│                           │    START    │                                    │
│                           └──────┬──────┘                                    │
│                                  │                                           │
│                                  ▼                                           │
│                    ┌─────────────────────────┐                               │
│           ┌───────▶│  Check: should_stop()?  │                               │
│           │        │  (max_iterations check) │                               │
│           │        └───────────┬─────────────┘                               │
│           │                    │                                             │
│           │         ┌──────────┴──────────┐                                  │
│           │         │                     │                                  │
│           │         ▼ NO                  ▼ YES                              │
│           │  ┌──────────────┐      ┌──────────────┐                          │
│           │  │  Make API    │      │    EXIT      │                          │
│           │  │  Request     │      │    LOOP      │                          │
│           │  └──────┬───────┘      └──────────────┘                          │
│           │         │                                                        │
│           │         ▼                                                        │
│           │  ┌──────────────────────┐                                        │
│           │  │   yield message      │  ◀─── Emit to caller                   │
│           │  │   iteration_count++  │                                        │
│           │  └──────────┬───────────┘                                        │
│           │             │                                                    │
│           │             ▼                                                    │
│           │  ┌──────────────────────────────┐                                │
│           │  │   _check_and_compact()       │                                │
│           │  │                              │                                │
│           │  │   Compaction performed?      │                                │
│           │  └──────────────┬───────────────┘                                │
│           │                 │                                                │
│           │      ┌──────────┴──────────┐                                     │
│           │      │                     │                                     │
│           │      ▼ YES                 ▼ NO                                  │
│           │  ┌───────────┐    ┌────────────────────────┐                     │
│           │  │ SKIP tool │    │ generate_tool_call_    │                     │
│           │  │ generation│    │ response()             │                     │
│           │  │           │    └───────────┬────────────┘                     │
│           │  │           │                │                                  │
│           │  │           │     ┌──────────┴──────────┐                       │
│           │  │           │     │                     │                       │
│           │  │           │     ▼ None                ▼ Response              │
│           │  │           │  ┌────────────┐   ┌───────────────────┐           │
│           │  │           │  │ No tool    │   │ append_messages() │           │
│           │  │           │  │ calls      │   │ (message + tool   │           │
│           │  │           │  │ EXIT LOOP  │   │  results)         │           │
│           │  │           │  └────────────┘   └─────────┬─────────┘           │
│           │  │           │                             │                     │
│           │  └─────┬─────┘                             │                     │
│           │        │                                   │                     │
│           │        └───────────────┬───────────────────┘                     │
│           │                        │                                         │
│           │                        ▼                                         │
│           │             ┌──────────────────────┐                             │
│           │             │ Reset flags:         │                             │
│           │             │ • _messages_modified │                             │
│           │             │ • _cached_response   │                             │
│           │             └──────────┬───────────┘                             │
│           │                        │                                         │
│           └────────────────────────┘                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1.6 Memory Tool Operations

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     MEMORY TOOL COMMAND DISPATCH                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         ┌─────────────────┐                                  │
│                         │  Tool Input     │                                  │
│                         │  (command obj)  │                                  │
│                         └────────┬────────┘                                  │
│                                  │                                           │
│                                  ▼                                           │
│              ┌───────────────────────────────────────┐                       │
│              │      command.command == ?             │                       │
│              └───────────────────┬───────────────────┘                       │
│                                  │                                           │
│     ┌────────┬────────┬─────────┼─────────┬────────┬────────┐               │
│     ▼        ▼        ▼         ▼         ▼        ▼        ▼               │
│  ┌──────┐┌──────┐┌─────────┐┌──────┐┌──────┐┌──────┐┌───────────┐           │
│  │"view"││"crea-││"str_    ││"inse-││"dele-││"rena-││ unknown   │           │
│  │      ││ te"  ││replace" ││ rt"  ││ te"  ││ me"  ││           │           │
│  └──┬───┘└──┬───┘└────┬────┘└──┬───┘└──┬───┘└──┬───┘└─────┬─────┘           │
│     │       │         │        │       │       │          │                 │
│     ▼       ▼         ▼        ▼       ▼       ▼          ▼                 │
│  ┌──────────────────────────────────────────────────────────────────┐       │
│  │                    ABSTRACT METHODS                               │       │
│  │         (Must be implemented by subclass)                         │       │
│  ├──────────────────────────────────────────────────────────────────┤       │
│  │                                                                   │       │
│  │  view(command)        │ View contents at memory path              │       │
│  │                       │ Returns: file content or directory list   │       │
│  │  ─────────────────────┼──────────────────────────────────────────│       │
│  │  create(command)      │ Create new memory file                    │       │
│  │                       │ Params: path, file_text                   │       │
│  │  ─────────────────────┼──────────────────────────────────────────│       │
│  │  str_replace(command) │ Replace text in memory file               │       │
│  │                       │ Params: path, old_str, new_str            │       │
│  │  ─────────────────────┼──────────────────────────────────────────│       │
│  │  insert(command)      │ Insert text at line number                │       │
│  │                       │ Params: path, insert_line, new_str        │       │
│  │  ─────────────────────┼──────────────────────────────────────────│       │
│  │  delete(command)      │ Delete memory file or directory           │       │
│  │                       │ Params: path                              │       │
│  │  ─────────────────────┼──────────────────────────────────────────│       │
│  │  rename(command)      │ Rename/move memory file or directory      │       │
│  │                       │ Params: old_path, new_path                │       │
│  │                                                                   │       │
│  └──────────────────────────────────────────────────────────────────┘       │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────┐       │
│  │  IMPLEMENTATION EXAMPLE (Custom Backend)                          │       │
│  ├──────────────────────────────────────────────────────────────────┤       │
│  │                                                                   │       │
│  │  class DatabaseMemoryTool(BetaAbstractMemoryTool):                │       │
│  │      def __init__(self, db_connection):                           │       │
│  │          self.db = db_connection                                  │       │
│  │                                                                   │       │
│  │      def view(self, command):                                     │       │
│  │          path = command.path                                      │       │
│  │          return self.db.query(f"SELECT content FROM memory       │       │
│  │                                 WHERE path = ?", path)            │       │
│  │                                                                   │       │
│  │      def create(self, command):                                   │       │
│  │          self.db.execute(f"INSERT INTO memory (path, content)    │       │
│  │                           VALUES (?, ?)",                         │       │
│  │                          command.path, command.file_text)         │       │
│  │          return "Created successfully"                            │       │
│  │      # ... other methods                                          │       │
│  │                                                                   │       │
│  └──────────────────────────────────────────────────────────────────┘       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 2. Sub-Agent Creation & Handling

## 2.1 Historical LangGraph Sub-Agent Architecture (non-production)

> Historical design snapshot only. Production sub-agent execution uses the
> CLI-backed agent framework and Temporal delegation workflows; no production
> import or dependency on LangGraph is implied by this section.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     SUB-AGENT ARCHITECTURE OVERVIEW                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                        PARENT GRAPH                                  │   │
│   │                                                                      │   │
│   │   ┌──────────┐    ┌──────────┐    ┌──────────────────────────────┐  │   │
│   │   │  START   │───▶│  Agent   │───▶│  Conditional Edge            │  │   │
│   │   └──────────┘    │  Node    │    │  (router function)           │  │   │
│   │                   └──────────┘    └──────────────┬───────────────┘  │   │
│   │                                                  │                   │   │
│   │                   ┌──────────────────────────────┼──────────────┐   │   │
│   │                   │                              │              │   │   │
│   │                   ▼                              ▼              ▼   │   │
│   │         ┌─────────────────┐           ┌──────────────┐    ┌─────┐  │   │
│   │         │   Tool Node     │           │  Sub-Agent   │    │ END │  │   │
│   │         │   (parallel)    │           │  (nested     │    │     │  │   │
│   │         │                 │           │   graph)     │    │     │  │   │
│   │         └────────┬────────┘           └──────┬───────┘    └─────┘  │   │
│   │                  │                           │                      │   │
│   │                  │    ┌──────────────────────┘                      │   │
│   │                  │    │                                             │   │
│   │                  ▼    ▼                                             │   │
│   │         ┌─────────────────────────────────────────────────────┐    │   │
│   │         │              State Reducer                           │    │   │
│   │         │   (aggregates results from parallel executions)      │    │   │
│   │         └─────────────────────────────────────────────────────┘    │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.2 Send API - Parallel Sub-Task Spawning

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SEND API MECHANISM                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PURPOSE: Spawn multiple parallel tasks with independent state               │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Send(node: str, arg: Any)                                          │    │
│  │                                                                      │    │
│  │  • node: Target node name to invoke                                  │    │
│  │  • arg:  Custom state/input for that invocation                      │    │
│  │          (can differ from main graph state!)                         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  EXAMPLE: Map-Reduce Pattern                                                 │
│  ───────────────────────────                                                 │
│                                                                              │
│     class OverallState(TypedDict):                                           │
│         subjects: list[str]                                                  │
│         results: Annotated[list[str], operator.add]  # Reducer!             │
│                                                                              │
│     def fan_out(state: OverallState) -> list[Send]:                          │
│         """Conditional edge that spawns parallel tasks."""                   │
│         return [                                                             │
│             Send("worker", {"subject": s})                                   │
│             for s in state["subjects"]                                       │
│         ]                                                                    │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  EXECUTION FLOW:                                                             │
│                                                                              │
│      subjects = ["A", "B", "C"]                                              │
│                    │                                                         │
│                    ▼                                                         │
│       ┌────────────────────────┐                                             │
│       │    fan_out(state)      │                                             │
│       │    Returns:            │                                             │
│       │    [Send("worker", A), │                                             │
│       │     Send("worker", B), │                                             │
│       │     Send("worker", C)] │                                             │
│       └───────────┬────────────┘                                             │
│                   │                                                          │
│     ┌─────────────┼─────────────┐                                            │
│     │             │             │                                            │
│     ▼             ▼             ▼                                            │
│  ┌──────┐     ┌──────┐     ┌──────┐                                          │
│  │worker│     │worker│     │worker│    ◀── PARALLEL EXECUTION                │
│  │ (A)  │     │ (B)  │     │ (C)  │                                          │
│  └──┬───┘     └──┬───┘     └──┬───┘                                          │
│     │            │            │                                              │
│     ▼            ▼            ▼                                              │
│  result_A    result_B    result_C                                            │
│     │            │            │                                              │
│     └────────────┼────────────┘                                              │
│                  │                                                           │
│                  ▼                                                           │
│       ┌────────────────────────┐                                             │
│       │   State Reducer        │                                             │
│       │   operator.add         │                                             │
│       │                        │                                             │
│       │   results = [A, B, C]  │                                             │
│       └────────────────────────┘                                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.3 create_react_agent - Sub-Agent Factory

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    create_react_agent ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  SIGNATURE:                                                                  │
│  ──────────                                                                  │
│  create_react_agent(                                                         │
│      model: LanguageModelLike | Callable,    # Static or dynamic model       │
│      tools: Sequence[BaseTool] | ToolNode,   # Tools for the agent           │
│      prompt: Prompt | None = None,           # System prompt                 │
│      response_format: Schema | None = None,  # Structured output             │
│      pre_model_hook: Runnable | None = None, # Before model call             │
│      post_model_hook: Runnable | None = None,# After model call              │
│      state_schema: Type | None = None,       # Custom state type             │
│      checkpointer: Checkpointer = None,      # For persistence               │
│      interrupt_before: list[str] | None,     # HITL breakpoints              │
│      interrupt_after: list[str] | None,      # HITL breakpoints              │
│      version: "v1" | "v2" = "v2",            # Execution mode                │
│      name: str | None = None,                # For sub-graph usage           │
│  ) -> CompiledStateGraph                                                     │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  GENERATED GRAPH STRUCTURE:                                                  │
│                                                                              │
│                    ┌──────────────────────────────────────────────┐          │
│                    │              StateGraph                      │          │
│                    │                                              │          │
│                    │  ┌─────────────────────────────────────────┐ │          │
│                    │  │           pre_model_hook                │ │          │
│                    │  │  (optional: message trimming, etc.)     │ │          │
│                    │  └────────────────┬────────────────────────┘ │          │
│                    │                   │                          │          │
│                    │                   ▼                          │          │
│  ┌─────────┐       │  ┌─────────────────────────────────────────┐ │          │
│  │  START  │──────▶│  │              agent                      │ │          │
│  └─────────┘       │  │   • Applies prompt                      │ │          │
│                    │  │   • Calls model                         │ │          │
│                    │  │   • Returns AIMessage                   │ │          │
│                    │  └────────────────┬────────────────────────┘ │          │
│                    │                   │                          │          │
│                    │                   ▼                          │          │
│                    │  ┌─────────────────────────────────────────┐ │          │
│                    │  │          should_continue                │ │          │
│                    │  │  (conditional edge)                     │ │          │
│                    │  └────────┬────────────────┬───────────────┘ │          │
│                    │           │                │                 │          │
│                    │   tool_calls?          no tool_calls         │          │
│                    │           │                │                 │          │
│                    │           ▼                ▼                 │          │
│                    │  ┌────────────────┐  ┌──────────────────┐   │          │
│                    │  │     tools      │  │ post_model_hook  │   │          │
│                    │  │  (ToolNode)    │  │ (optional)       │   │          │
│                    │  └───────┬────────┘  └────────┬─────────┘   │          │
│                    │          │                    │              │          │
│                    │          │                    ▼              │          │
│                    │          │           ┌───────────────────┐  │          │
│                    │          │           │ structured_output │  │          │
│                    │          │           │ (if response_     │  │          │
│                    │          │           │  format set)      │  │          │
│                    │          │           └─────────┬─────────┘  │          │
│                    │          │                     │             │          │
│                    │          │                     ▼             │          │
│                    │          └─────────▶   ┌─────────┐          │          │
│                    │                        │   END   │          │          │
│                    │                        └─────────┘          │          │
│                    │                                              │          │
│                    └──────────────────────────────────────────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.4 Version v1 vs v2 Tool Execution

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TOOL EXECUTION VERSIONS                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  VERSION v1 (Legacy)                                                         │
│  ═══════════════════                                                         │
│                                                                              │
│    ┌─────────────┐                                                           │
│    │   agent     │                                                           │
│    │  (AIMsg     │                                                           │
│    │   w/ 3      │                                                           │
│    │   tool_calls│                                                           │
│    └──────┬──────┘                                                           │
│           │                                                                  │
│           ▼                                                                  │
│    ┌─────────────────────────────────────────────┐                           │
│    │              tools (ToolNode)               │                           │
│    │                                             │                           │
│    │   ┌────────┐  ┌────────┐  ┌────────┐       │                           │
│    │   │ tool_1 │  │ tool_2 │  │ tool_3 │       │  ◀── All in SAME node     │
│    │   └────────┘  └────────┘  └────────┘       │      (parallel within)    │
│    │                                             │                           │
│    │   Single message with all results           │                           │
│    └─────────────────────────────────────────────┘                           │
│                                                                              │
│                                                                              │
│  VERSION v2 (Current - Uses Send API)                                        │
│  ════════════════════════════════════                                        │
│                                                                              │
│    ┌─────────────┐                                                           │
│    │   agent     │                                                           │
│    │  (AIMsg     │                                                           │
│    │   w/ 3      │                                                           │
│    │   tool_calls│                                                           │
│    └──────┬──────┘                                                           │
│           │                                                                  │
│           ▼                                                                  │
│    ┌─────────────────────────────────────────────┐                           │
│    │          should_continue                    │                           │
│    │                                             │                           │
│    │    Returns: [                               │                           │
│    │      Send("tools", ToolCallWithContext(     │                           │
│    │          tool_call=call_1, state=state)),   │                           │
│    │      Send("tools", ToolCallWithContext(     │                           │
│    │          tool_call=call_2, state=state)),   │                           │
│    │      Send("tools", ToolCallWithContext(     │                           │
│    │          tool_call=call_3, state=state)),   │                           │
│    │    ]                                        │                           │
│    └───────────────────┬─────────────────────────┘                           │
│                        │                                                     │
│          ┌─────────────┼─────────────┐                                       │
│          │             │             │                                       │
│          ▼             ▼             ▼                                       │
│    ┌──────────┐  ┌──────────┐  ┌──────────┐                                  │
│    │  tools   │  │  tools   │  │  tools   │   ◀── SEPARATE Send instances   │
│    │ (call_1) │  │ (call_2) │  │ (call_3) │       (true parallelism)        │
│    └──────────┘  └──────────┘  └──────────┘                                  │
│                                                                              │
│                                                                              │
│  ADVANTAGES OF v2:                                                           │
│  • Human-in-the-loop can pause between individual tool calls                 │
│  • Better interrupt/resume granularity                                       │
│  • State preserved per-tool-call via ToolCallWithContext                     │
│  • More resilient to long-running tools                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.5 ToolNode Execution Algorithm

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     TOOLNODE EXECUTION ALGORITHM                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  INITIALIZATION:                                                             │
│  ═══════════════                                                             │
│                                                                              │
│    def __init__(self, tools, handle_tool_errors, messages_key, ...):         │
│        # Build tool registry                                                 │
│        self._tools_by_name = {tool.name: tool for tool in tools}            │
│                                                                              │
│        # Pre-compute injection specs (ONCE, not per-call)                    │
│        self._injected_args = {                                               │
│            tool.name: _get_all_injected_args(tool)                           │
│            for tool in tools                                                 │
│        }                                                                     │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  EXECUTION FLOW (_func / _afunc):                                            │
│  ═════════════════════════════════                                           │
│                                                                              │
│        ┌─────────────────────────────┐                                       │
│        │          INPUT              │                                       │
│        │  (state dict or message     │                                       │
│        │   list or ToolCallWithCtx)  │                                       │
│        └─────────────┬───────────────┘                                       │
│                      │                                                       │
│                      ▼                                                       │
│        ┌─────────────────────────────┐                                       │
│        │      _parse_input()         │                                       │
│        │                             │                                       │
│        │  Extract tool_calls from:   │                                       │
│        │  • Last AIMessage           │                                       │
│        │  • Direct tool_call list    │                                       │
│        │  • ToolCallWithContext      │                                       │
│        └─────────────┬───────────────┘                                       │
│                      │                                                       │
│                      ▼                                                       │
│        ┌─────────────────────────────┐                                       │
│        │  Build ToolRuntime for      │                                       │
│        │  each tool call:            │                                       │
│        │                             │                                       │
│        │  ToolRuntime(               │                                       │
│        │    state=extracted_state,   │                                       │
│        │    tool_call_id=call["id"], │                                       │
│        │    config=cfg,              │                                       │
│        │    context=runtime.context, │                                       │
│        │    store=runtime.store,     │                                       │
│        │    stream_writer=...,       │                                       │
│        │  )                          │                                       │
│        └─────────────┬───────────────┘                                       │
│                      │                                                       │
│                      ▼                                                       │
│        ┌─────────────────────────────────────────────────────────────┐       │
│        │                PARALLEL EXECUTION                            │       │
│        │                                                              │       │
│        │    with get_executor_for_config(config) as executor:         │       │
│        │        outputs = executor.map(                               │       │
│        │            self._run_one,                                    │       │
│        │            tool_calls,                                       │       │
│        │            input_types,                                      │       │
│        │            tool_runtimes                                     │       │
│        │        )                                                     │       │
│        │                                                              │       │
│        └─────────────────────────────────────────────────────────────┘       │
│                      │                                                       │
│                      ▼                                                       │
│        ┌─────────────────────────────┐                                       │
│        │   _combine_tool_outputs()   │                                       │
│        │                             │                                       │
│        │  • Regular: {messages: [...]}│                                       │
│        │  • Command: [Command(...)]  │                                       │
│        │  • Mixed: both types        │                                       │
│        └─────────────────────────────┘                                       │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  SINGLE TOOL EXECUTION (_run_one):                                           │
│  ══════════════════════════════════                                          │
│                                                                              │
│        ┌─────────────────────────────┐                                       │
│        │   Build ToolCallRequest     │                                       │
│        │                             │                                       │
│        │   request = ToolCallRequest(│                                       │
│        │     tool_call=call,         │                                       │
│        │     tool=tools_by_name[...],│                                       │
│        │     state=state,            │                                       │
│        │     runtime=tool_runtime,   │                                       │
│        │   )                         │                                       │
│        └─────────────┬───────────────┘                                       │
│                      │                                                       │
│           ┌──────────┴──────────┐                                            │
│           │                     │                                            │
│     wrap_tool_call?         No wrapper                                       │
│           │                     │                                            │
│           ▼                     ▼                                            │
│   ┌───────────────┐    ┌───────────────────┐                                 │
│   │ Call wrapper  │    │ _execute_tool_    │                                 │
│   │ with execute  │    │ sync() directly   │                                 │
│   │ callback      │    │                   │                                 │
│   └───────────────┘    └───────────────────┘                                 │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  ARGUMENT INJECTION (_inject_tool_args):                                     │
│  ═══════════════════════════════════════                                     │
│                                                                              │
│        ┌─────────────────────────────────────────────────────────────┐       │
│        │  Pre-computed _InjectedArgs structure:                       │       │
│        │                                                              │       │
│        │  _InjectedArgs(                                              │       │
│        │    state = {"param_name": "state_field" or None},           │       │
│        │    store = "store_param_name" or None,                       │       │
│        │    runtime = "runtime_param_name" or None,                   │       │
│        │  )                                                           │       │
│        └─────────────────────────────────────────────────────────────┘       │
│                                                                              │
│        INJECTION PROCESS:                                                    │
│                                                                              │
│        1. STATE INJECTION                                                    │
│           ├─ InjectedState("field") → inject state["field"]                  │
│           └─ InjectedState() → inject entire state                           │
│                                                                              │
│        2. STORE INJECTION                                                    │
│           └─ InjectedStore() → inject runtime.store                          │
│                                                                              │
│        3. RUNTIME INJECTION                                                  │
│           └─ ToolRuntime param → inject full ToolRuntime object              │
│                                                                              │
│        # Injected args HIDDEN from LLM schema                                │
│        tool_call["args"] = {**original_args, **injected_args}               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.6 Command-Based Flow Control

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COMMAND-BASED FLOW CONTROL                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Command(                                                                    │
│      graph: str | None = None,      # Target graph (None=current)            │
│      update: Any | None = None,     # State update                           │
│      resume: dict | Any = None,     # Resume from interrupt                  │
│      goto: Send | Sequence | str,   # Navigation target                      │
│  )                                                                           │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  PATTERN 1: Tool Controls Navigation                                         │
│  ────────────────────────────────────                                        │
│                                                                              │
│    @tool                                                                     │
│    def decision_tool(query: str) -> Command:                                 │
│        if "urgent" in query:                                                 │
│            return Command(                                                   │
│                update={"priority": "high"},                                  │
│                goto="urgent_handler",  # Jump to specific node               │
│            )                                                                 │
│        return Command(goto=END)        # End the graph                       │
│                                                                              │
│                                                                              │
│  PATTERN 2: Delegate to Parent Graph                                         │
│  ────────────────────────────────────                                        │
│                                                                              │
│    @tool                                                                     │
│    def spawn_workers(tasks: list[str]) -> Command:                           │
│        return Command(                                                       │
│            graph=Command.PARENT,       # Send to parent                      │
│            goto=[                                                            │
│                Send("worker", {"task": t})                                   │
│                for t in tasks                                                │
│            ]                                                                 │
│        )                                                                     │
│                                                                              │
│                                                                              │
│  PATTERN 3: Resume from Interrupt                                            │
│  ────────────────────────────────                                            │
│                                                                              │
│    # In node:                                                                │
│    answer = interrupt("What should I do?")                                   │
│                                                                              │
│    # Client resumes with:                                                    │
│    graph.stream(Command(resume="proceed"), config)                           │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  COMMAND FLOW DIAGRAM:                                                       │
│                                                                              │
│       ┌───────────────────────────────────────────────────────────────┐     │
│       │                    TOOL NODE                                   │     │
│       │                                                                │     │
│       │   tool execution returns Command                               │     │
│       └───────────────────────────┬───────────────────────────────────┘     │
│                                   │                                          │
│                                   ▼                                          │
│       ┌───────────────────────────────────────────────────────────────┐     │
│       │              _validate_tool_command()                          │     │
│       │                                                                │     │
│       │   • Ensure ToolMessage exists for current graph                │     │
│       │   • Process Command.update                                     │     │
│       │   • Validate Command.goto targets                              │     │
│       └───────────────────────────┬───────────────────────────────────┘     │
│                                   │                                          │
│                    ┌──────────────┴──────────────┐                           │
│                    │                             │                           │
│           graph == None              graph == PARENT                         │
│                    │                             │                           │
│                    ▼                             ▼                           │
│       ┌────────────────────────┐    ┌────────────────────────┐              │
│       │   Apply to CURRENT     │    │   Bubble up to         │              │
│       │   graph                │    │   PARENT graph         │              │
│       │                        │    │                        │              │
│       │   • Update state       │    │   • ParentCommand      │              │
│       │   • Navigate via goto  │    │     exception raised   │              │
│       └────────────────────────┘    └────────────────────────┘              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.7 Interrupt & Resume Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      INTERRUPT & RESUME FLOW                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  INTERRUPT MECHANISM:                                                        │
│  ════════════════════                                                        │
│                                                                              │
│       ┌────────────────────────────────────────────────────────────────┐    │
│       │                        NODE EXECUTION                           │    │
│       │                                                                 │    │
│       │    def approval_node(state):                                    │    │
│       │        # First call: raises GraphInterrupt                      │    │
│       │        # Resume call: returns stored value                      │    │
│       │        answer = interrupt("Approve action?")                    │    │
│       │        return {"approved": answer == "yes"}                     │    │
│       │                                                                 │    │
│       └─────────────────────────────┬──────────────────────────────────┘    │
│                                     │                                        │
│                          FIRST CALL │                                        │
│                                     ▼                                        │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │                    interrupt() INTERNALS                         │   │
│       │                                                                  │   │
│       │   1. Get config from context                                     │   │
│       │   2. Increment interrupt_counter (scratchpad.idx)                │   │
│       │   3. Check for resume values (scratchpad.resume)                 │   │
│       │      ├─ Found at idx → return resume[idx]                        │   │
│       │      └─ Not found → raise GraphInterrupt                         │   │
│       │                                                                  │   │
│       │   raise GraphInterrupt((                                         │   │
│       │       Interrupt(                                                 │   │
│       │           value="Approve action?",                               │   │
│       │           id=xxh3_hash(checkpoint_ns)                            │   │
│       │       ),                                                         │   │
│       │   ))                                                             │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│                                     │                                        │
│                                     ▼                                        │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │                    EXECUTION HALTS                               │   │
│       │                                                                  │   │
│       │   • State checkpointed                                           │   │
│       │   • Interrupt info returned to client:                           │   │
│       │     {"__interrupt__": (Interrupt(value="...", id="..."),)}      │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  RESUME MECHANISM:                                                           │
│  ═════════════════                                                           │
│                                                                              │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │                    CLIENT RESUMES                                │   │
│       │                                                                  │   │
│       │   # Option 1: Resume with value                                  │   │
│       │   graph.stream(Command(resume="yes"), config)                    │   │
│       │                                                                  │   │
│       │   # Option 2: Resume specific interrupt by ID                    │   │
│       │   graph.stream(Command(resume={"abc123": "yes"}), config)        │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│                                     │                                        │
│                                     ▼                                        │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │                    NODE RE-EXECUTES                              │   │
│       │                                                                  │   │
│       │   • Node runs FROM THE START                                     │   │
│       │   • interrupt() called again                                     │   │
│       │   • This time: scratchpad.resume[idx] exists                     │   │
│       │   • Returns "yes" instead of raising                             │   │
│       │                                                                  │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  MULTIPLE INTERRUPTS IN SAME NODE:                                           │
│  ══════════════════════════════════                                          │
│                                                                              │
│       def multi_approval_node(state):                                        │
│           step1 = interrupt("Approve step 1?")    # idx=0                    │
│           step2 = interrupt("Approve step 2?")    # idx=1                    │
│           step3 = interrupt("Approve step 3?")    # idx=2                    │
│           return {"steps": [step1, step2, step3]}                            │
│                                                                              │
│       # Resume with list:                                                    │
│       graph.stream(Command(resume=["yes", "no", "yes"]), config)             │
│                                                                              │
│       # Or resume one at a time (3 separate calls):                          │
│       graph.stream(Command(resume="yes"), config)  # step1                   │
│       graph.stream(Command(resume="no"), config)   # step2                   │
│       graph.stream(Command(resume="yes"), config)  # step3                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 3. Execution Flow Diagrams

## 3.1 Complete Agent Execution Cycle

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COMPLETE AGENT EXECUTION CYCLE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│    ┌─────────┐                                                               │
│    │  USER   │                                                               │
│    │  INPUT  │                                                               │
│    └────┬────┘                                                               │
│         │                                                                    │
│         ▼                                                                    │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │                         SUPERSTEP LOOP                               │  │
│    │                                                                      │  │
│    │  ┌─────────────────────────────────────────────────────────────┐    │  │
│    │  │  1. LOAD CHECKPOINT (if exists)                             │    │  │
│    │  │     • Restore channel values                                │    │  │
│    │  │     • Restore versions_seen                                 │    │  │
│    │  │     • Check for pending interrupts                          │    │  │
│    │  └─────────────────────────────────────────────────────────────┘    │  │
│    │                              │                                       │  │
│    │                              ▼                                       │  │
│    │  ┌─────────────────────────────────────────────────────────────┐    │  │
│    │  │  2. DETERMINE NEXT TASKS                                    │    │  │
│    │  │     • Check conditional edges                               │    │  │
│    │  │     • Process Send objects                                  │    │  │
│    │  │     • Filter by versions_seen                               │    │  │
│    │  └─────────────────────────────────────────────────────────────┘    │  │
│    │                              │                                       │  │
│    │               no tasks?      │      tasks to run                     │  │
│    │              ┌───────────────┴───────────────┐                       │  │
│    │              │                               │                       │  │
│    │              ▼                               ▼                       │  │
│    │       ┌────────────┐            ┌─────────────────────────────┐     │  │
│    │       │    EXIT    │            │  3. EXECUTE TASKS           │     │  │
│    │       │    LOOP    │            │     (parallel)              │     │  │
│    │       └────────────┘            │                             │     │  │
│    │                                 │  ┌─────┐ ┌─────┐ ┌─────┐   │     │  │
│    │                                 │  │task1│ │task2│ │task3│   │     │  │
│    │                                 │  └──┬──┘ └──┬──┘ └──┬──┘   │     │  │
│    │                                 │     │      │      │        │     │  │
│    │                                 └─────┼──────┼──────┼────────┘     │  │
│    │                                       │      │      │              │  │
│    │                                       ▼      ▼      ▼              │  │
│    │  ┌─────────────────────────────────────────────────────────────┐    │  │
│    │  │  4. APPLY UPDATES TO CHANNELS                               │    │  │
│    │  │     • Run reducers (e.g., add_messages)                     │    │  │
│    │  │     • Update channel_versions                               │    │  │
│    │  │     • Handle Overwrite values                               │    │  │
│    │  └─────────────────────────────────────────────────────────────┘    │  │
│    │                              │                                       │  │
│    │                              ▼                                       │  │
│    │  ┌─────────────────────────────────────────────────────────────┐    │  │
│    │  │  5. SAVE CHECKPOINT                                         │    │  │
│    │  │     • Serialize channel_values                              │    │  │
│    │  │     • Store metadata (step, source, parents)                │    │  │
│    │  │     • Persist pending_writes                                │    │  │
│    │  └─────────────────────────────────────────────────────────────┘    │  │
│    │                              │                                       │  │
│    │                              ▼                                       │  │
│    │  ┌─────────────────────────────────────────────────────────────┐    │  │
│    │  │  6. YIELD STATE UPDATE                                      │    │  │
│    │  │     • stream_mode="values": full state                      │    │  │
│    │  │     • stream_mode="updates": node outputs                   │    │  │
│    │  │     • stream_mode="messages": LLM tokens                    │    │  │
│    │  └─────────────────────────────────────────────────────────────┘    │  │
│    │                              │                                       │  │
│    │                              └──────────────┐                        │  │
│    │                                             │                        │  │
│    │                                    LOOP BACK TO STEP 2               │  │
│    │                                                                      │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 3.2 Nested Sub-Agent Execution

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     NESTED SUB-AGENT EXECUTION                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         PARENT GRAPH                                   │  │
│  │                                                                        │  │
│  │    ┌────────────┐                                                      │  │
│  │    │ coordinator│                                                      │  │
│  │    │    node    │                                                      │  │
│  │    └─────┬──────┘                                                      │  │
│  │          │                                                             │  │
│  │          │  Returns: [Send("coding_agent", task1),                     │  │
│  │          │            Send("research_agent", task2)]                   │  │
│  │          │                                                             │  │
│  │    ┌─────┴─────────────────────────────────────────┐                   │  │
│  │    │                                               │                   │  │
│  │    ▼                                               ▼                   │  │
│  │  ┌─────────────────────────────┐  ┌─────────────────────────────┐     │  │
│  │  │       coding_agent          │  │      research_agent         │     │  │
│  │  │   (compiled sub-graph)      │  │   (compiled sub-graph)      │     │  │
│  │  │                             │  │                             │     │  │
│  │  │  ┌────────────────────────┐│  │  ┌────────────────────────┐ │     │  │
│  │  │  │  Has own:              ││  │  │  Has own:              │ │     │  │
│  │  │  │  • State schema        ││  │  │  • State schema        │ │     │  │
│  │  │  │  • Checkpointer (opt)  ││  │  │  • Checkpointer (opt)  │ │     │  │
│  │  │  │  • Tools               ││  │  │  • Tools               │ │     │  │
│  │  │  │  • Model               ││  │  │  • Model               │ │     │  │
│  │  │  └────────────────────────┘│  │  └────────────────────────┘ │     │  │
│  │  │                             │  │                             │     │  │
│  │  │    ┌─────┐   ┌─────┐       │  │    ┌─────┐   ┌─────┐       │     │  │
│  │  │    │agent│──▶│tools│──┐    │  │    │agent│──▶│tools│──┐    │     │  │
│  │  │    └─────┘   └─────┘  │    │  │    └─────┘   └─────┘  │    │     │  │
│  │  │                       │    │  │                       │    │     │  │
│  │  │         loop──────────┘    │  │         loop──────────┘    │     │  │
│  │  │                             │  │                             │     │  │
│  │  └─────────────┬───────────────┘  └─────────────┬───────────────┘     │  │
│  │                │                                │                      │  │
│  │                │  code_result                   │  research_result     │  │
│  │                │                                │                      │  │
│  │                └────────────┬───────────────────┘                      │  │
│  │                             │                                          │  │
│  │                             ▼                                          │  │
│  │               ┌──────────────────────────────────┐                     │  │
│  │               │         State Reducer            │                     │  │
│  │               │   (aggregates sub-agent results) │                     │  │
│  │               │                                  │                     │  │
│  │               │   results: [code_result,         │                     │  │
│  │               │             research_result]     │                     │  │
│  │               └──────────────────────────────────┘                     │  │
│  │                                                                        │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  CHECKPOINTING INHERITANCE:                                                  │
│  ══════════════════════════                                                  │
│                                                                              │
│    checkpointer = None   │ Inherit from parent (recommended)                 │
│    checkpointer = True   │ Enable independent checkpointing                  │
│    checkpointer = False  │ Disable even if parent has checkpointer           │
│    checkpointer = saver  │ Use specific checkpointer instance                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 4. Data Structures Reference

## 4.1 Core Types

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CORE DATA STRUCTURES                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Checkpoint                                                          │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  v: int                      # Format version (currently 1)          │    │
│  │  id: str                     # Unique, monotonically increasing      │    │
│  │  ts: str                     # ISO 8601 timestamp                    │    │
│  │  channel_values: dict        # Serialized state per channel          │    │
│  │  channel_versions: dict      # Version string per channel            │    │
│  │  versions_seen: dict         # Node -> channel versions it saw       │    │
│  │  updated_channels: list      # Channels updated in this checkpoint   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Send                                                                │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  node: str                   # Target node to invoke                 │    │
│  │  arg: Any                    # Custom state for that invocation      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Command                                                             │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  graph: str | None           # Target (None=current, PARENT=parent)  │    │
│  │  update: Any | None          # State update to apply                 │    │
│  │  resume: dict | Any          # Resume value for interrupt            │    │
│  │  goto: Send | Sequence | str # Navigation target(s)                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Interrupt                                                           │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  value: Any                  # Value surfaced to client              │    │
│  │  id: str                     # Unique ID for targeted resume         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ToolRuntime                                                         │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  state: StateT               # Current graph state                   │    │
│  │  context: ContextT           # Runtime context                       │    │
│  │  config: RunnableConfig      # Runnable configuration                │    │
│  │  stream_writer: StreamWriter # For custom streaming                  │    │
│  │  tool_call_id: str | None    # Current tool call ID                  │    │
│  │  store: BaseStore | None     # Persistent storage                    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  CompactionControl                                                   │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  enabled: Required[bool]     # Must be explicitly enabled            │    │
│  │  context_token_threshold: int # Default: 100,000                     │    │
│  │  model: str                  # Model for summarization               │    │
│  │  summary_prompt: str         # Custom summary prompt                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  _InjectedArgs (internal)                                            │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │  state: dict[str, str|None]  # param -> state_field (None=full)     │    │
│  │  store: str | None           # Param name for store injection        │    │
│  │  runtime: str | None         # Param name for runtime injection      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 4.2 State Channel Types

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STATE CHANNEL TYPES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  LastValue                                                           │    │
│  │  ──────────                                                          │    │
│  │  Default channel type. Last write wins.                              │    │
│  │                                                                      │    │
│  │  class State(TypedDict):                                             │    │
│  │      current_task: str  # LastValue (implicit)                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  BinaryOperatorAggregate                                             │    │
│  │  ────────────────────────                                            │    │
│  │  Uses a reducer function to combine values.                          │    │
│  │                                                                      │    │
│  │  class State(TypedDict):                                             │    │
│  │      messages: Annotated[list, add_messages]    # Append             │    │
│  │      total: Annotated[int, operator.add]        # Sum                │    │
│  │      all_items: Annotated[list, operator.add]   # Concat             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  EphemeralValue                                                      │    │
│  │  ──────────────                                                      │    │
│  │  Value exists only within a single superstep.                        │    │
│  │  Reset to default after each step.                                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  NamedBarrierValue                                                   │    │
│  │  ─────────────────                                                   │    │
│  │  Synchronization primitive. Waits for all named writers.             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Managed Values (Special)                                            │    │
│  │  ────────────────────────                                            │    │
│  │  Not channels - computed/managed by the runtime.                     │    │
│  │                                                                      │    │
│  │  class State(TypedDict):                                             │    │
│  │      remaining_steps: RemainingSteps  # Auto-decremented             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Historical Dependency Snapshot (non-production)

These were the versions considered by the superseded LangGraph design. They
are not dependencies of the current server runtime.

| Package | Version |
|---------|---------|
| anthropic | 0.77.0 |
| langgraph | 1.0.7 |
| langgraph-sdk | 0.3.3 |
| langgraph-checkpoint | 4.0.0 |
| langchain-core | 1.2.7 |
| langchain-anthropic | 1.3.1 |

---

## Quick Reference

### Memory Compaction Trigger
```python
if total_tokens >= 100_000:  # Default threshold
    trigger_compaction()
```

### Create Sub-Agent
```python
sub_agent = create_react_agent(model, tools, name="sub_agent")
parent_graph.add_node("sub_agent", sub_agent)
```

### Spawn Parallel Tasks
```python
def router(state):
    return [Send("worker", {"task": t}) for t in state["tasks"]]
```

### Control Flow from Tool
```python
return Command(update={"status": "done"}, goto=END)
```

### Human-in-the-Loop
```python
answer = interrupt("Need approval")  # Pauses execution
# Client: graph.stream(Command(resume="approved"), config)
```
