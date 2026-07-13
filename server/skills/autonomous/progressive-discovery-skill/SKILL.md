---
name: progressive-discovery-skill
description: Discover and load tools progressively as needed (reduces token overhead)
allowed-tools: delegate_to_ai_agent check_delegated_tasks
metadata:
  author: opencompany
  version: "1.0"
  category: autonomous
  icon: "🔍"
  color: "#F59E0B"

---
# Progressive Tool Discovery

You are an agent that discovers and uses capabilities progressively as needed, rather than loading everything upfront.

## Why Progressive Discovery?

Loading all tools at start creates:
- **Token overhead**: 150,000+ tokens for tool definitions
- **Context pollution**: Irrelevant tools confuse the LLM
- **Slower responses**: More tokens = more processing time

Progressive discovery provides:
- **98.7% token savings** (Anthropic MCP research)
- **Focused context**: Only relevant tools loaded
- **Better decisions**: Less noise, clearer choices

## Discovery Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                 PROGRESSIVE DISCOVERY                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   1. START with minimal context                             │
│         │                                                   │
│         ▼                                                   │
│   2. IDENTIFY what capability is needed                     │
│         │                                                   │
│         ▼                                                   │
│   3. CHECK what tools/agents are connected                  │
│         │                                                   │
│         ├──▶ Tool exists? ──▶ USE it directly              │
│         │                                                   │
│         └──▶ Specialized agent exists? ──▶ DELEGATE to it  │
│                                                              │
│   4. EXECUTE with focused context                           │
│         │                                                   │
│         ▼                                                   │
│   5. RETURN result (don't load more than needed)           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Available Specialized Agents

Check your connected tools - you may have access to these specialized agents:

| Agent | Capabilities | When to Delegate |
|-------|--------------|------------------|
| `android_agent` | Device control, apps, sensors | Android device tasks |
| `coding_agent` | Python/JavaScript execution | Code computation |
| `web_agent` | Web scraping, HTTP requests | Internet data |
| `task_agent` | Scheduling, timers, cron | Time-based tasks |
| `social_agent` | WhatsApp, messaging | Communication |
| `travel_agent` | Maps, locations, directions | Travel planning |

## Discovery Examples

### Example 1: User asks "What's the battery level?"

**Without Progressive Discovery** (wasteful):
```
Load ALL tools (calculator, web_search, whatsapp, android, maps, code, ...)
Parse user request
Find that android tool is needed
Execute battery check
```

**With Progressive Discovery** (efficient):
```
1. IDENTIFY: This needs Android device access
2. CHECK: Is android_agent or battery tool connected?
3. YES → Delegate: "Check battery level"
4. RETURN: Battery at 75%, charging
```

### Example 2: User asks "Calculate compound interest for $10,000 at 5% for 10 years"

**Discovery Process:**
```
1. IDENTIFY: This is a mathematical calculation
2. CHECK: Do I have python_code or calculator tool?

   IF python_code connected:
      Use Code Mode for complex calculation

   ELIF calculator connected:
      Use calculator for simple operations

   ELSE:
      Report: "I need a code executor or calculator to compute this"
```

### Example 3: User asks "Send my location to John on WhatsApp"

**Discovery Process:**
```
1. IDENTIFY: Needs location + messaging
2. CHECK: What's connected?

   Step A - Get location:
   IF location tool connected → Use directly
   ELIF android_agent connected → Delegate location request
   ELSE → Ask user for location

   Step B - Send message:
   IF whatsapp_send connected → Use directly
   ELIF social_agent connected → Delegate message
   ELSE → Report: "WhatsApp not available"
```

## Delegation Pattern

When delegating to a specialized agent:

```json
{
  "task": "Specific task description",
  "context": "Relevant context only (not everything)"
}
```

**Good Context** (focused):
```json
{
  "task": "Get current GPS coordinates",
  "context": "User needs their location for a WhatsApp message"
}
```

**Bad Context** (bloated):
```json
{
  "task": "Get current GPS coordinates",
  "context": "Full conversation history... user preferences... all previous results... system info..."
}
```

## Capability Check Pattern

Before attempting an action, verify the capability exists:

```
IF task requires capability X:

    IF direct_tool_for_X is connected:
        → Use tool directly (fastest)

    ELIF specialized_agent_for_X is connected:
        → Delegate to agent (handles complexity)

    ELSE:
        → Report: "This capability is not available"
        → Suggest: "Connect [tool/agent name] to enable this"
```

## Anti-Patterns to Avoid

### 1. Loading Everything Upfront
```
❌ "Let me check all my tools: calculator, web_search, whatsapp,
    android, maps, code, http, scheduler, memory..."

✓ "To answer this, I need [specific capability]"
```

### 2. Delegating Without Checking
```
❌ Immediately delegate to android_agent without checking if connected

✓ Check connected tools first, then delegate if available
```

### 3. Over-Explaining Capabilities
```
❌ "I have access to many tools including... [lists everything]"

✓ "I can help with that. Let me [specific action]."
```

### 4. Redundant Delegation
```
❌ Delegate "calculate 2+2" to coding_agent

✓ Simple math can be done directly or with calculator tool
```

## Integration with Agentic Loop

Progressive Discovery works with the Agentic Loop pattern:

```
OBSERVE: What does the user need?
    ↓
THINK: What capability is required?
    ↓
DISCOVER: Is that capability connected?
    ↓
ACT: Use tool directly OR delegate to agent
    ↓
REFLECT: Did it work?
    ↓
DECIDE: Complete or discover next capability
```

## Best Practices

1. **Start minimal** - Don't enumerate all tools at the start
2. **Discover on demand** - Only check for capabilities when needed
3. **Prefer direct tools** - Use connected tools before delegating
4. **Focused delegation** - Pass only relevant context to agents
5. **Report gaps clearly** - If capability missing, say what's needed
