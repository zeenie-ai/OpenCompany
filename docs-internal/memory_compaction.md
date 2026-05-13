# Memory Compaction, Token Tracking, and Cost Calculation Service

> **Related docs:** [memory_lifecycle.md](./memory_lifecycle.md) for the markdown / vector-store / state-clear surface. This doc is the SSOT for the **service** (CompactionService API, thresholds, native-API integration, pricing). memory_lifecycle.md is the SSOT for the **flow** (how the markdown moves through an agent turn).

## Overview

The compaction service enables automatic memory compaction, token tracking, and **cost calculation** for MachinaOs specialized agents. It uses a hybrid approach leveraging native provider APIs (Anthropic, OpenAI) when available, with comprehensive token and cost tracking for all providers.

**Inspired by:** Claude Code's compaction pattern from the Anthropic SDK
**Threshold Strategy:** per-session `custom_threshold` > model-aware (50% of context window) > global default (100K)
**Cost Calculation:** Official pricing from each provider (per 1M tokens)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI Agent Execution                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              CompactionService.track()                           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 1. Save TokenUsageMetric to database                       │ │
│  │ 2. Update SessionTokenState cumulative counters            │ │
│  │ 3. Check if cumulative_total >= threshold                  │ │
│  │ 4. Return {total, threshold, needs_compaction}             │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (if needs_compaction)
┌─────────────────────────────────────────────────────────────────┐
│              Native Provider Compaction                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Anthropic  │  │   OpenAI    │  │      Others             │ │
│  │ context_    │  │ compact_    │  │ Client-side             │ │
│  │ management  │  │ threshold   │  │ summarization           │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              CompactionService.record()                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 1. Save CompactionEvent to database                        │ │
│  │ 2. Reset SessionTokenState cumulative counters             │ │
│  │ 3. Increment compaction_count                              │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Native Provider APIs

### Anthropic SDK (tool_runner)

When using Anthropic's SDK with `tool_runner`, pass the compaction control config:

```python
from services.compaction import get_compaction_service

svc = get_compaction_service()

# Model-aware threshold (50% of context window)
compaction_config = svc.anthropic_config(model="claude-opus-4.6", provider="anthropic")
# Returns: {"enabled": True, "context_token_threshold": 500000}  (50% of 1M context)

# Or override with explicit threshold
compaction_config = svc.anthropic_config(threshold=100000)
# Returns: {"enabled": True, "context_token_threshold": 100000}

# Use with tool_runner
result = await tool_runner(
    model="claude-opus-4.6",
    messages=messages,
    tools=tools,
    compaction_control=compaction_config
)
```

### Anthropic Messages API

For direct Messages API usage with the compaction beta:

```python
# Model-aware threshold
api_config = svc.anthropic_api_config(model="claude-sonnet-4.5", provider="anthropic")
# Threshold auto-computed from model's context window (50% of 1M = 500000)

# Or override with explicit threshold
api_config = svc.anthropic_api_config(threshold=100000)

# Use with Messages API
response = await client.messages.create(
    model="claude-sonnet-4.5",
    messages=messages,
    **api_config
)
```

### OpenAI

For OpenAI models (including GPT-4 and o-series):

```python
# Model-aware threshold
openai_config = svc.openai_config(model="gpt-5.2", provider="openai")
# Returns: {"context_management": {"compact_threshold": 200000}}  (50% of 400K)

# Or override with explicit threshold
openai_config = svc.openai_config(threshold=100000)
# Returns: {"context_management": {"compact_threshold": 100000}}
```

## Database Schema

### TokenUsageMetric

Tracks token usage per agent execution:

```python
class TokenUsageMetric(SQLModel, table=True):
    __tablename__ = "token_usage_metrics"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)        # Memory session ID
    node_id: str = Field(index=True)           # Agent node ID
    workflow_id: Optional[str] = None          # Workflow context
    provider: str                               # openai, anthropic, gemini, groq
    model: str                                  # Model identifier

    # Core token counts (LangChain UsageMetadata compatible)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Provider-specific token details
    cache_creation_tokens: int = 0   # Anthropic cache miss
    cache_read_tokens: int = 0       # Anthropic cache hit
    reasoning_tokens: int = 0        # OpenAI o-series reasoning

    iteration: int = 1               # LangGraph iteration number
    execution_id: Optional[str]      # Workflow execution ID
    created_at: Optional[datetime]
```

### SessionTokenState

Cumulative token state per memory session:

```python
class SessionTokenState(SQLModel, table=True):
    __tablename__ = "session_token_states"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(unique=True, index=True)

    # Cumulative counters (reset after compaction)
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cache_tokens: int = 0
    cumulative_reasoning_tokens: int = 0
    cumulative_total: int = 0

    # Compaction tracking
    last_compaction_at: Optional[datetime]
    compaction_count: int = 0

    # Per-session configuration
    custom_threshold: Optional[int]   # Overrides global setting
    compaction_enabled: bool = True

    updated_at: Optional[datetime]
```

### CompactionEvent

Historical record of compaction events:

```python
class CompactionEvent(SQLModel, table=True):
    __tablename__ = "compaction_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    node_id: str
    workflow_id: Optional[str]

    trigger_reason: str              # "native", "threshold", "manual"
    tokens_before: int
    tokens_after: int
    messages_before: int = 0
    messages_after: int = 0

    summary_model: str
    summary_provider: str
    summary_tokens_used: int = 0
    success: bool = True
    error_message: Optional[str]
    summary_content: Optional[str]   # The compacted summary

    created_at: Optional[datetime]
```

## Service API

### Initialization

The service is initialized via dependency injection in `container.py`:

```python
from services.compaction import get_compaction_service, init_compaction_service

# During app startup (handled by container)
compaction_service = init_compaction_service(database, settings)

# Get the singleton instance anywhere
svc = get_compaction_service()
```

### Track Token Usage

Call after each AI agent execution:

```python
result = await svc.track(
    session_id="user-session-123",
    node_id="agent-node-1",
    provider="anthropic",
    model="claude-opus-4.6",
    usage={
        "input_tokens": 5000,
        "output_tokens": 1000,
        "total_tokens": 6000,
        "cache_creation_tokens": 2000,  # Optional
        "cache_read_tokens": 1500,      # Optional
        "reasoning_tokens": 0           # Optional
    }
)

# result:
# {
#     "total": 6000,           # New cumulative total
#     "threshold": 500000,     # Model-aware: 50% of 1M context window
#     "total_cost": 0.021,     # USD cost
#     "needs_compaction": False
# }
#
# Threshold priority: custom_threshold > model-aware (50% context) > global default
```

### Record Compaction Event

Call after native provider handles compaction:

```python
await svc.record(
    session_id="user-session-123",
    node_id="agent-node-1",
    provider="anthropic",
    model="claude-3-5-sonnet-20241022",
    tokens_before=105000,
    tokens_after=15000,
    summary="## Summary\nConversation about project planning..."  # Optional
)
```

### Get Session Statistics

```python
# Model-aware threshold when model/provider given
stats = await svc.stats("user-session-123", model="claude-opus-4.6", provider="anthropic")
# {
#     "session_id": "user-session-123",
#     "total": 15000,
#     "threshold": 500000,  # 50% of 1M context window
#     "count": 1  # Number of compactions
# }

# Without model/provider, falls back to global default
stats = await svc.stats("user-session-123")
# {"session_id": "user-session-123", "total": 15000, "threshold": 100000, "count": 1}
```

### Configure Per-Session Settings

```python
# Set custom threshold for a session
await svc.configure("user-session-123", threshold=50000)

# Disable compaction for a session
await svc.configure("user-session-123", enabled=False)

# Both
await svc.configure("user-session-123", threshold=75000, enabled=True)
```

## WebSocket Handlers

### get_compaction_stats

Get token usage statistics for a session:

```javascript
// Client request
ws.send(JSON.stringify({
    type: "get_compaction_stats",
    session_id: "user-session-123"
}));

// Server response
{
    "type": "get_compaction_stats",
    "success": true,
    "session_id": "user-session-123",
    "total": 45000,
    "threshold": 100000,
    "count": 0
}
```

### configure_compaction

Update compaction settings for a session:

```javascript
// Client request
ws.send(JSON.stringify({
    type: "configure_compaction",
    session_id: "user-session-123",
    threshold: 50000,
    enabled: true
}));

// Server response
{
    "type": "configure_compaction",
    "success": true
}
```

## Configuration

### Environment Variables

```bash
# In server/.env
COMPACTION_ENABLED=true       # Enable/disable compaction globally (default: true)
COMPACTION_THRESHOLD=100000   # Global fallback threshold (default: 100000, min: 10000)
                              # Used when model context window is unknown
```

**Threshold priority chain:**
1. Per-session `custom_threshold` (set via `configure()` or WebSocket)
2. Model-aware threshold: 50% of model's context window (e.g., 500K for 1M model)
3. Global `COMPACTION_THRESHOLD` from `.env` (fallback when model info unavailable)

### Per-Session Override

Sessions can override the global threshold:

```python
# Via service API
await svc.configure("session-id", threshold=50000)

# Via WebSocket
ws.send(JSON.stringify({
    type: "configure_compaction",
    session_id: "session-id",
    threshold: 50000
}));
```

## Integration with AI Service

### Token Extraction from LangChain

LangChain normalizes token usage across all providers via `UsageMetadata`:

```python
from langchain_core.messages.ai import UsageMetadata, add_usage

# After AI execution
response = await model.ainvoke(messages)
usage = response.usage_metadata

# Example usage_metadata:
# {
#     'input_tokens': 8,
#     'output_tokens': 304,
#     'total_tokens': 312,
#     'input_token_details': {'cache_read': 0, 'cache_creation': 0},
#     'output_token_details': {'reasoning': 256}
# }

# Aggregate across LangGraph iterations
total_usage = None
for msg in final_state["messages"]:
    if hasattr(msg, 'usage_metadata') and msg.usage_metadata:
        total_usage = add_usage(total_usage, msg.usage_metadata)
```

### Integration Point

In `server/services/ai.py`, after agent execution:

```python
# After LangGraph execution, before memory save
if memory_data and memory_data.get('session_id'):
    from services.compaction import get_compaction_service
    svc = get_compaction_service()

    if svc and ai_response and ai_response.usage_metadata:
        usage = ai_response.usage_metadata
        tracking = await svc.track(
            session_id=memory_data['session_id'],
            node_id=node_id,
            provider=provider,
            model=model,
            usage={
                "input_tokens": usage.get('input_tokens', 0),
                "output_tokens": usage.get('output_tokens', 0),
                "total_tokens": usage.get('total_tokens', 0),
                "cache_creation_tokens": usage.get('input_token_details', {}).get('cache_creation', 0),
                "cache_read_tokens": usage.get('input_token_details', {}).get('cache_read', 0),
                "reasoning_tokens": usage.get('output_token_details', {}).get('reasoning', 0),
            }
        )

        if tracking.get('needs_compaction'):
            # Trigger native compaction or client-side summarization
            ...
```

## File Reference

| File | Description |
|------|-------------|
| `server/services/compaction.py` | CompactionService class with model-aware thresholds and provider configs |
| `server/services/model_registry.py` | ModelRegistryService providing context_length for threshold computation |
| `server/models/database.py` | SQLModel tables for token tracking |
| `server/core/database.py` | CRUD methods for metrics and events |
| `server/core/config.py` | Environment variable configuration |
| `server/core/container.py` | Dependency injection setup |
| `server/routers/websocket.py` | WebSocket handlers |
| `server/main.py` | Service initialization on startup |

## Design Decisions

1. **Hybrid Approach**: Leverage native provider APIs (Anthropic, OpenAI) for compaction instead of reimplementing. Track tokens for all providers.

2. **Pydantic BaseModel**: Use Pydantic for configuration validation to reduce boilerplate code.

3. **Per-Session State**: Each memory session has independent token tracking and thresholds. This allows different agents to have different compaction settings.

4. **Model-Aware Threshold**: Threshold is 50% of the model's context window (via `get_model_threshold()`). For example, Claude Opus 4.6 (1M context) gets a 500K threshold, GPT-5.2 (400K) gets 200K, Groq models (131K) get ~65K. Falls back to global `COMPACTION_THRESHOLD` when model info is unavailable. Per-session `custom_threshold` always takes priority.

5. **Singleton Pattern**: Service accessible via `get_compaction_service()` for easy integration anywhere in the codebase.

6. **Lazy Initialization**: Service is lazily initialized via container on first access, not blocking app startup.

## Client-Side Compaction

For all providers (not just Anthropic/OpenAI), the service performs automatic client-side compaction when the model-aware threshold is exceeded. This uses the AI service to generate a structured summary following Claude Code's 5-section pattern. The summary max_tokens is capped at `min(4096, model's max output tokens)`.

### compact_context() Method

```python
result = await svc.compact_context(
    session_id="user-session-123",
    node_id="agent-node-1",
    memory_content="# Conversation History\n...",  # Current memory markdown
    provider="anthropic",
    api_key="sk-...",
    model="claude-opus-4.6"
)

# result:
# {
#     "success": True,
#     "summary": "# Conversation Summary (Compacted)\n...",
#     "tokens_before": 105000,
#     "tokens_after": 0
# }
```

### Summary Structure

The compacted summary follows Claude Code's 5-section pattern:

```markdown
# Conversation Summary (Compacted)
*Generated: 2025-02-12T10:30:00Z*

## Task Overview
What the user is trying to accomplish.

## Current State
What's been completed and what's in progress.

## Important Discoveries
Key findings, decisions, or problems encountered.

## Next Steps
What needs to happen next.

## Context to Preserve
Details that must be retained for continuity.
```

### Automatic Triggering

Compaction is automatically triggered in `_track_token_usage()` when:
1. `needs_compaction` returns true (cumulative tokens >= threshold)
2. Memory content is available (connected memory node)
3. API key is available for summarization

```python
# In server/services/ai.py _track_token_usage()
if tracking.get('needs_compaction') and memory_content and api_key:
    result = await svc.compact_context(
        session_id=session_id,
        node_id=node_id,
        memory_content=memory_content,
        provider=provider,
        api_key=api_key,
        model=model
    )

    if result.get("success"):
        # Update memory with compacted summary
        memory_data['memory_content'] = result['summary']
```

### AI Service Wiring

The compaction service requires the AI service to generate summaries. This is wired during app startup:

```python
# In server/main.py
from services.compaction import get_compaction_service

compaction_svc = container.compaction_service()
compaction_svc.set_ai_service(container.ai_service())
```

## WebSocket Broadcasts

The service broadcasts real-time updates to the frontend:

### token_usage_update

Broadcast after each AI execution with token tracking:

```json
{
    "type": "token_usage_update",
    "session_id": "user-session-123",
    "data": {
        "total": 45000,
        "threshold": 100000,
        "needs_compaction": false
    }
}
```

### compaction_starting

Broadcast when compaction is about to begin:

```json
{
    "type": "compaction_starting",
    "session_id": "user-session-123",
    "node_id": "agent-node-1"
}
```

### compaction_completed

Broadcast when compaction finishes:

```json
{
    "type": "compaction_completed",
    "session_id": "user-session-123",
    "success": true,
    "tokens_before": 105000,
    "tokens_after": 0,
    "error": null
}
```

## Frontend UI

### Token Usage Panel

The Token Usage panel is displayed in the MiddleSection of the parameter panel for memory nodes (simpleMemory). It shows:

- **Progress bar**: Visual representation of tokens used vs threshold
- **Statistics**: Current token count, threshold, compaction count
- **Editable threshold**: Click edit icon to change threshold per session

```typescript
// In client/src/components/parameterPanel/MiddleSection.tsx
<Collapse.Panel header="Token Usage" key="tokenUsage">
  <Progress
    percent={Math.min(100, Math.round((tokenStats.total / tokenStats.threshold) * 100))}
    status={tokenStats.total >= tokenStats.threshold ? 'exception' : 'normal'}
  />
  <Statistic title="Tokens Used" value={`${tokenStats.total.toLocaleString()} / ${tokenStats.threshold.toLocaleString()}`} />
  <Statistic title="Compactions" value={tokenStats.count} />

  {/* Editable threshold */}
  {isEditingThreshold ? (
    <InputNumber
      value={editThresholdValue}
      onChange={setEditThresholdValue}
      min={10000}
      max={1000000}
      step={10000}
    />
  ) : (
    <Button icon={<EditOutlined />} onClick={() => setIsEditingThreshold(true)} />
  )}
</Collapse.Panel>
```

## Future Enhancements

1. **Compaction History UI**: View past compaction events and summaries in the frontend
2. **Multiple Summary Strategies**: Allow users to choose different summarization approaches
