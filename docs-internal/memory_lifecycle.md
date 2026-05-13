# Memory Lifecycle

Canonical home for how conversation memory is loaded, appended, trimmed, archived, cleared, and resumed across every MachinaOs agent. Replaces the partial explanations previously scattered across `agent_architecture.md`, `deep_agent.md`, `cli_agent_framework.md`, `rlm_service.md`, and `memory_compaction.md`.

> **Related docs:**
> - [memory_compaction.md](./memory_compaction.md) — `CompactionService`, token thresholds, pricing
> - [agent_architecture.md](./agent_architecture.md) — LangGraph agent loop that calls these helpers
> - [cli_agent_framework.md](./cli_agent_framework.md) — `claude_code_agent` native session bridge
> - [deep_agent.md](./deep_agent.md) / [rlm_service.md](./rlm_service.md) — engine-specific adapters

## 1. Two storage formats

| Format | File | Used by | Wire shape |
|---|---|---|---|
| **Markdown transcript** | [`services/memory/markdown.py`](../server/services/memory/markdown.py) | aiAgent, chatAgent, deep_agent, rlm_agent, claude_code_agent (mirror) | `### **Human/Assistant** (timestamp)` blocks under a top-level `# Conversation History` heading |
| **Anthropic-Messages JSONL** | [`services/memory/jsonl.py`](../server/services/memory/jsonl.py) | Standalone primitive — not used by any agent today | One JSON object per line: `{"role": "user"|"assistant", "content": str \| [ContentBlock], ...metadata}` |

The markdown format is the visible UI surface — `simpleMemory.memory_content` is rendered as a markdown editor in the parameter panel. The JSONL helpers were prepared for a future SDK migration; the live `claude_code_agent` bridge uses claude's own native session JSONL on disk (see §6) and mirrors the transcript into `memory_content` via the **markdown** helpers for user visibility.

## 2. Markdown helper API

Three pure functions, no I/O, locked by `tests/services/memory/`:

```python
from services.memory import (
    parse_memory_markdown,      # str -> List[BaseMessage]
    append_to_memory_markdown,  # (content, role: "human"|"ai", message) -> str
    trim_markdown_window,       # (content, window_size_pairs) -> (trimmed, removed_texts)
)
```

- **`parse_memory_markdown`** — regex-extracts `### **(Human|Assistant)**[^\n]*\n(body)` blocks; returns LangChain `HumanMessage` / `AIMessage` instances ready to feed an agent loop.
- **`append_to_memory_markdown`** — drops the empty-state placeholder (`*No messages yet.*`), appends a `### **{label}** (YYYY-MM-DD HH:MM:SS)\n{message}\n` entry. Timestamp comes from `datetime.now()`.
- **`trim_markdown_window`** — keeps the last `window_size * 2` blocks (N user/assistant pairs); returns `(trimmed_content, removed_texts)`. Removed bodies are returned so the caller can hand them to the long-term vector store (§4).

### Empty-state placeholder

```
# Conversation History

*No messages yet.*
```

This is also the value `clear_agent_session_state` resets `memory_content` to (see §5). Defined as `DEFAULT_MEMORY_CONTENT` in [`services/memory/state.py`](../server/services/memory/state.py).

## 3. Known duplication: ai.py inlines its own copies

[`services/ai.py:158-234`](../server/services/ai.py) carries `_parse_memory_markdown`, `_append_to_memory_markdown`, `_trim_markdown_window`, and `_get_memory_vector_store` as private helpers — byte-equivalent reimplementations of the public ones in `services/memory/`. The `services.memory` package was carved out later; existing call sites inside `ai.py` were never switched to import from it. Future cleanup will consolidate; for now both paths exist and produce identical output. Pytest doesn't lock cross-equivalence — treat the `services.memory` package as the canonical surface for **new** code.

Newer call sites (already on the public helpers):

| File | Imports |
|---|---|
| `services/temporal/agent_activities.py:251` — `agent.persist_turn.v1` activity (F4.B) | `append_to_memory_markdown`, `trim_markdown_window` |
| `services/cli_agent/service.py:490` — `_persist_memory` for claude_code_agent | `append_to_memory_markdown`, `trim_markdown_window` |
| `routers/websocket.py:2167` — `clear_memory` handler | `clear_agent_session_state` |

Older call sites (still on the underscore-prefixed copies inside ai.py):

| File | Symbols |
|---|---|
| `services/ai.py:execute_agent` / `execute_chat_agent` | `_parse_memory_markdown`, `_append_to_memory_markdown`, `_trim_markdown_window`, `_memory_vector_stores` |

## 4. Long-term vector store

[`services/memory/vector_store.py`](../server/services/memory/vector_store.py) caches one `InMemoryVectorStore` per `session_id`. Embeddings come from `langchain_huggingface.HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")` — lazy-imported; missing dep returns `None` with a warning (long-term archival is best-effort).

```python
from services.memory import get_memory_vector_store
store = get_memory_vector_store("user-123")
if store:
    store.add_texts(removed_texts)         # archive trimmed messages
    results = store.similarity_search(q)   # retrieve for the next turn
```

**Two caches exist.** The live one used by `services/ai.py:execute_agent` is `services.ai._memory_vector_stores`. The one in `services.memory.vector_store` is a package-private second copy referenced by `clear_agent_session_state` for forward-cleanup. Same caveat as §3: consolidation is future work. The state-clear orchestration deletes from the live `services.ai` cache.

## 5. State clear orchestration

[`services/memory/state.py`](../server/services/memory/state.py) exposes one function:

```python
await clear_agent_session_state(
    session_id: str,
    workflow_id: str = None,
    clear_long_term: bool = False,     # True → drop vector store
    memory_node_id: str | None = None, # When set → reset memory_content + wipe last_session_id
) -> {cleared_vector_store, cleared_todo_keys, cleared_memory_node}
```

Three stores are cleared in one call:

1. **Vector store** (when `clear_long_term=True`) — `del services.ai._memory_vector_stores[session_id]`.
2. **TodoService** — every candidate key (`workflow_id`, `session_id`, `"default"`) is cleared because [`server/nodes/tool/write_todos.py`](../server/nodes/tool/write_todos.py) uses `ctx.workflow_id or ctx.node_id or "default"` as the storage key and we want to clear whichever fallback the write path actually used.
3. **simpleMemory node fields** (when `memory_node_id` provided) — `memory_content` → `DEFAULT_MEMORY_CONTENT`; `last_session_id` → `None` (so claude_code_agent starts fresh next run instead of `--resume`-ing into a wiped transcript); orphan `memory_jsonl` field popped if present.

Frontend `clear_memory` WS handler ([`routers/websocket.py:2167`](../server/routers/websocket.py)) calls this; UI presents a Reset Memory button on the simpleMemory parameter panel.

## 6. claude_code_agent native session resume bridge

`claude_code_agent` does **not** inject `memory_content` as a system prompt. It calls claude's CLI with `--resume <UUID>` so claude reads its own native JSONL transcript on disk (`<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_id>.jsonl`). The bridge has three coupled mechanisms; full plumbing in [cli_agent_framework.md → Memory bridge](./cli_agent_framework.md#memory-bridge--simplememory--claude_code_agent).

| Mechanism | Where | Why |
|---|---|---|
| **Stable `cwd=repo_root`** | `AICliSession.cwd()` when `memory_bound=True` | Keeps claude's `project_key = re.sub(r"[^a-zA-Z0-9.-]", "-", str(cwd))` constant so `--resume <UUID>` finds the prior JSONL |
| **UUID5 first-run / `--resume` thereafter** | `claude_code_agent.execute()` | First run: `uuid5(NAMESPACE_OID, f"{memory_node_id}:{session_id}")` → `--session-id <UUID5>`. Subsequent runs: `simpleMemory.last_session_id` → `--resume <UUID>` |
| **Auto-clear stale `last_session_id`** | `_persist_memory` in `services/cli_agent/service.py` | When claude returns `No conversation found with session ID: <UUID>`, wipe `last_session_id` (preserve `memory_content`); next run self-heals |

`memory_content` is the UI mirror, not the resume channel. `_persist_memory` ([`services/cli_agent/service.py:446-513`](../server/services/cli_agent/service.py)) appends every successful run's user prompt + assistant response to it via the **same markdown helpers** aiAgent uses, and saves the most recent run's `r.session_id` to `simpleMemory.last_session_id` in one DB write. Always broadcasts `node_parameters_updated` (CloudEvents v1.0 envelope, `source_hint="cli"`) so the simpleMemory parameter panel auto-refetches mid-run.

**Parallel-batch guard.** When memory is wired AND `len(tasks) > 1`, `claude_code_agent` raises `NodeUserError` — concurrent `--resume <UUID>` spawns against one JSONL would race.

## 7. The lifecycle in motion (aiAgent example)

```
Turn N starts
    │
    ▼
1. handle_ai_agent / handle_chat_agent reads simpleMemory.memory_content from DB
    │
    ▼
2. _parse_memory_markdown(content) → List[BaseMessage]
    │
    ▼
3. (Optional) get_memory_vector_store(session_id).similarity_search(prompt)
   → prepend retrieved long-term context as a system message
    │
    ▼
4. LangGraph agent loop runs (ainvoke + tool calls)
    │
    ▼
5. _track_token_usage(...) — see memory_compaction.md
   → if threshold tripped: CompactionService.compact_context(...) → reset messages
    │
    ▼
6. _append_to_memory_markdown(content, "human", prompt)
   _append_to_memory_markdown(content, "ai", response)
    │
    ▼
7. trimmed_content, removed_texts = _trim_markdown_window(content, window_size)
    │
    ▼
8. (Optional) store.add_texts(removed_texts) — long-term archival
    │
    ▼
9. save_node_parameters(memory_node_id, {"memory_content": trimmed_content, ...})
   broadcast_node_parameters_updated(...)  # CloudEvents source_hint="agent"
```

F4.B `AgentWorkflow` runs steps 4-9 as separate Temporal activities (`execute_llm_step.v1` / `persist_turn.v1` / `compact_memory.v1`); per-turn persistence means a mid-loop failure doesn't lose progress. F4.A and legacy paths run steps 4-9 inline inside one activity.

## 8. Engine-specific adapters

| Engine | Memory shape on input | Memory shape on output | Notes |
|---|---|---|---|
| `aiAgent` / `chatAgent` | LangChain `BaseMessage` list parsed from markdown | Appended pair + trim | Standard path; see §7 |
| `deep_agent` | Same as aiAgent — `DeepAgentService` accepts `BaseMessage` list | Same | `FilesystemBackend(virtual_mode=True, root_dir=workspace_dir)` is independent of memory; see [deep_agent.md](./deep_agent.md) |
| `rlm_agent` | Markdown injected as REPL pre-context | Last `FINAL(...)` call's payload | RLM internal recursion uses its own state machine; only entry/exit cross the memory boundary |
| `claude_code_agent` | `--resume <UUID>` reads native JSONL; `memory_content` ignored on input | Bridge writes markdown mirror + saves `last_session_id` | See §6 |

## 9. Compaction trigger

Compaction is a separate concern documented in [memory_compaction.md](./memory_compaction.md). The trigger point is step 5 in §7. Threshold = `model_context_length * agent.compaction.ratio` (default 0.5; configured in [`server/config/llm_defaults.json`](../server/config/llm_defaults.json)).

The compaction service is the SSOT for thresholds, native-API integration (Anthropic `compact-2026-01-12` beta, OpenAI `context_management.compact_threshold`), and client-side summarization fallback. Memory lifecycle (this doc) is the SSOT for storage shape and helper signatures.

## 10. Pytest invariants

```
server/tests/services/memory/
├── test_markdown.py    # parse → append → trim round-trips
├── test_jsonl.py       # parse_jsonl + append_message + trim_window
└── (state.py / vector_store.py have no dedicated test files;
    coverage rides on test_ai_agents.py + test_status_broadcasts.py)
```

The CloudEvents `node_parameters_updated` envelope emitted on every memory write is locked by `tests/test_status_broadcasts.py:test_node_parameters_updated_envelope_shape` — see [status_broadcaster.md](./status_broadcaster.md) for the contract.

## 11. What NOT to do

- Don't write to `simpleMemory.memory_content` without broadcasting `node_parameters_updated` — the parameter panel won't refetch and users will see stale content.
- Don't add new local copies of the markdown helpers. Import from `services.memory` (or from `services.memory.markdown` / `.jsonl` directly).
- Don't bypass `clear_agent_session_state` to "just delete the markdown" — TodoService and the vector store will leak across sessions.
- Don't inject `memory_content` as a system prompt for `claude_code_agent` — `--resume <UUID>` is the resume channel; `memory_content` is for the human reader.
- Don't run claude_code_agent with `memory_bound=True` in parallel batches — the `--resume` guard raises `NodeUserError` and you'll burn a credit.
