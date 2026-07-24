# Memory Lifecycle

Canonical home for how conversation memory is loaded, appended, trimmed, archived, cleared, and resumed across every OpenCompany agent. Replaces the partial explanations previously scattered across `agent_architecture.md`, `cli_agent_framework.md`, `rlm_service.md`, and `memory_compaction.md`.

> **Related docs:**
> - [memory_compaction.md](./memory_compaction.md) — `CompactionService`, token thresholds, pricing
> - [agent_architecture.md](./agent_architecture.md) — agent loop that calls these helpers
> - [cli_agent_framework.md](./cli_agent_framework.md) — `claude_code_agent` native session bridge
> - [rlm_service.md](./rlm_service.md) — `rlm_agent` engine-specific adapter

## 1. Two storage formats

| Format | File | Used by | Wire shape |
|---|---|---|---|
| **Markdown transcript** | [`services/memory/markdown.py`](../server/services/memory/markdown.py) | aiAgent, chatAgent, rlm_agent, claude_code_agent (mirror) | `### **Human/Assistant** (timestamp)` blocks under a top-level `# Conversation History` heading |
| **Anthropic-Messages JSONL** | [`services/memory/jsonl.py`](../server/services/memory/jsonl.py) | Standalone primitive — not used by any agent today | One JSON object per line: `{"role": "user"|"assistant", "content": str \| [ContentBlock], ...metadata}` |

The markdown format is the visible UI surface — `simpleMemory.memory_content`
is rendered as a markdown editor in the parameter panel. The normalized JSONL
helpers remain a standalone primitive; the live `claude_code_agent` bridge
uses claude's own native session JSONL on disk (see §6) and mirrors the
transcript into `memory_content` via the **markdown** helpers for visibility.

## 2. Markdown helper API

Three pure functions, no I/O, locked by `tests/services/memory/`:

```python
from services.memory import (
    parse_memory_markdown,      # str -> List[services.llm.protocol.Message]
    append_to_memory_markdown,  # (content, role: "human"|"ai", message) -> str
    trim_markdown_window,       # (content, window_size_pairs) -> (trimmed, removed_texts)
)
```

- **`parse_memory_markdown`** — regex-extracts `### **(Human|Assistant)**[^\n]*\n(body)` blocks; returns normalized native `Message(role="user"|"assistant", content=...)` instances ready for `ChatUnifier`.
- **`append_to_memory_markdown`** — drops the empty-state placeholder (`*No messages yet.*`), appends a `### **{label}** (YYYY-MM-DD HH:MM:SS)\n{message}\n` entry. Timestamp comes from `datetime.now()`.
- **`trim_markdown_window`** — keeps the last `window_size * 2` blocks (N user/assistant pairs); returns `(trimmed_content, removed_texts)`. Removed bodies are returned so the caller can hand them to the long-term vector store (§4).

### Empty-state placeholder

```
# Conversation History

*No messages yet.*
```

This is also the value `clear_agent_session_state` resets `memory_content` to (see §5). Defined as `DEFAULT_MEMORY_CONTENT` in [`services/memory/state.py`](../server/services/memory/state.py).

## 3. Canonical implementation and call sites

`services.memory` is the single implementation. `services.ai` keeps only a
thin `_parse_memory_markdown` compatibility wrapper and aliases the canonical
vector-store cache so older internal imports continue to resolve without
creating duplicate state. Runtime appends use
`services.memory.runtime.append_memory_turns_atomic`, which performs the
append/trim mutation transactionally and idempotently.

| File | Canonical surface |
|---|---|
| `services/ai.py` — native in-process agents | `parse_memory_markdown`, `append_memory_turns_atomic`, canonical vector store |
| `services/temporal/agent_activities.py` — `agent.persist_turn.v1` | `append_memory_turns_atomic` |
| `services/cli_agent/service.py` — claude_code_agent mirror | markdown/runtime helpers and canonical vector store |
| `services/memory/state.py` — reset orchestration | canonical vector-store cache + atomic parameter mutation |

## 4. Long-term vector store

[`services/memory/vector_store.py`](../server/services/memory/vector_store.py)
provides a shared async embedder factory for direct `openai.AsyncOpenAI`,
`ollama.AsyncClient`, and optional `sentence-transformers` adapters, then
ranks entries locally by cosine similarity. Hugging Face model load and
encoding run in a worker thread. When its `local-embeddings` extra is absent,
the factory returns `None` with a warning, so long-term archival remains
best-effort.

```python
from services.memory import get_memory_vector_store
store = await get_memory_vector_store(
    "user-123",
    provider="openai",
    model="text-embedding-3-small",
    auth_service=auth_service,
)
if store:
    await store.add_texts(removed_texts)         # archive trimmed messages
    results = await store.similarity_search(q)   # retrieve next-turn context
```

There is one cache. `services.ai._memory_vector_stores` is a compatibility
alias to `services.memory.vector_store._memory_vector_stores`, so state clear,
native agents, and the CLI bridge observe the same stores. Cache identity is
session + provider + model + endpoint + credential fingerprint; plaintext
credentials are never stored in a key. Clearing a session removes and closes
every matching configuration.

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

1. **Vector store** (when `clear_long_term=True`) — delete and close every provider/model cache entry for the session (the `services.ai` alias observes the same deletion).
2. **TodoService** — every candidate key (`workflow_id`, `session_id`, `"default"`) is cleared because [`server/nodes/tool/write_todos/__init__.py`](../server/nodes/tool/write_todos/__init__.py) uses `ctx.workflow_id or ctx.node_id or "default"` as the storage key and we want to clear whichever fallback the write path actually used.
3. **simpleMemory node fields** (when `memory_node_id` provided) — `memory_content` → `DEFAULT_MEMORY_CONTENT`; `last_session_id` → `None` (so claude_code_agent starts fresh next run instead of `--resume`-ing into a wiped transcript); orphan `memory_jsonl` field popped if present.

Frontend `clear_memory` WS handler ([`routers/websocket.py:2167`](../server/routers/websocket.py)) calls this; UI presents a Reset Memory button on the simpleMemory parameter panel.

### Workflow Reset archives then clears Memory

Temporal workflow **Reset** and the Simple Memory panel's **Clear Memory** are
separate lifecycle operations:

| Operation | `memory_content` / `memory_jsonl` | session metadata | vector cache | compaction/token state |
|---|---|---|---|---|
| Workflow Reset | archived, then reset | cleared | cleared | reset for connected sessions |
| Clear Memory | reset | cleared | optional (`clear_long_term`) | cleared for the selected session |

`workflow_runtime_reset` removes the editor's disposable execution projection
and invalidates memory/compaction projections. Before clearing, Reset copies
the authoritative Simple Memory parameters into the archived generation's
`runtime_data.simple_memory` map. It then resets the live memory-node fields,
connected agent sessions, vector caches, direct memory-store sessions,
conversation rows, and token counters. The next Temporal generation therefore
starts with the empty conversation placeholder.

The archived copy is immutable history; the live `node_parameters` row remains
the source of truth and is reset to the empty-state value for the next Start.

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
2. _parse_memory_markdown(content) → List[native Message]
    │
    ▼
3. (Optional) await get_memory_vector_store(config);
   await store.similarity_search(prompt)
   → prepend retrieved long-term context as a system message
    │
    ▼
4. Agent loop runs (run_native_agent_loop: ChatUnifier + native tool calls)
    │
    ▼
5. _track_token_usage(...) — see memory_compaction.md
   → if threshold tripped: CompactionService.compact_context(...) → reset messages
    │
    ▼
6. append_memory_turns_atomic(prompt, response, window_size)
   → append markdown pair + trim in one idempotent DB transaction
    │
    ▼
7. (Optional) await store.add_texts(removed_texts) — long-term archival
```

F4.B `AgentWorkflow` runs steps 4-9 as separate Temporal activities.
`prepare_payload.v1` records `llm_engine` and the message wire version;
new executions use native Message Wire V2, preserving Gemini thought
signatures, Anthropic signed/redacted thinking, and OpenAI continuation
metadata. Histories recorded before cutover lack that engine field and
deterministically use the temporary LangChain compatibility branch.
`persist_turn.v1` appends per turn, so a mid-loop failure does not lose
progress.

## 8. Engine-specific adapters

| Engine | Memory shape on input | Memory shape on output | Notes |
|---|---|---|---|
| `aiAgent` / `chatAgent` | Native `Message` list parsed from markdown | Atomic appended pair + trim | Native SDK path; see §7 |
| `rlm_agent` | Markdown injected as REPL pre-context | Last `FINAL(...)` call's payload | RLM internal recursion uses its own state machine; only entry/exit cross the memory boundary |
| `claude_code_agent` | `--resume <UUID>` reads native JSONL; `memory_content` ignored on input | Bridge writes markdown mirror + saves `last_session_id` | See §6 |

## 9. Compaction trigger

Compaction is a separate concern documented in [memory_compaction.md](./memory_compaction.md). The trigger point is step 5 in §7. Threshold = `model_context_length * compaction_ratio` (default **0.8** = 80%, env `COMPACTION_RATIO` via `Settings`; per-user override in `UserSettings.compaction_ratio`; JSON `llm_defaults.json:agent.compaction.ratio` is the last-resort fallback).

The compaction service is the SSOT for thresholds, native-API integration (Anthropic `compact-2026-01-12` beta, OpenAI `context_management.compact_threshold`), and client-side summarization fallback. Memory lifecycle (this doc) is the SSOT for storage shape and helper signatures.

## 10. Pytest invariants

```
server/tests/services/memory/
├── test_markdown.py    # parse → append → trim round-trips
├── test_jsonl.py       # parse_jsonl + append_message + trim_window
├── test_state.py       # cross-store reset behavior
├── test_memory_store.py # legacy/canonical role normalization
└── test_vector_store.py # native cosine ranking and empty operations
```

The CloudEvents `node_parameters_updated` envelope emitted on every memory write is locked by `tests/test_status_broadcasts.py:test_node_parameters_updated_envelope_shape` — see [status_broadcaster.md](./status_broadcaster.md) for the contract.

## 11. What NOT to do

- Don't write to `simpleMemory.memory_content` without broadcasting `node_parameters_updated` — the parameter panel won't refetch and users will see stale content.
- Don't add new local copies of the markdown helpers. Import from `services.memory` (or from `services.memory.markdown` / `.jsonl` directly).
- Don't bypass `clear_agent_session_state` to "just delete the markdown" — TodoService and the vector store will leak across sessions.
- Don't inject `memory_content` as a system prompt for `claude_code_agent` — `--resume <UUID>` is the resume channel; `memory_content` is for the human reader.
- Don't run claude_code_agent with `memory_bound=True` in parallel batches — the `--resume` guard raises `NodeUserError` and you'll burn a credit.
