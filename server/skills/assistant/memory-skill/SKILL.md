---
name: memory-skill
description: Manage conversation memory. Use when user asks to remember something, recall information, view/edit conversation history, or manage short/long-term memory.
allowed-tools: memory-save memory-get memory-clear memory-view memory-search
metadata:
  author: opencompany
  version: "2.0"
  category: memory
  icon: "🧠"
  color: "#8B5CF6"

---

# Memory Management Skill

This skill enables you to manage conversation memory using the SimpleMemory node's markdown-based storage system.

## Memory Architecture

### Short-Term Memory (Markdown)
- Visible and editable in the SimpleMemory node's UI
- Stored as markdown with timestamped entries
- Window-based: keeps last N message pairs (configurable)
- Format: `### **Human** (timestamp)` and `### **Assistant** (timestamp)`

### Long-Term Memory (Vector DB)
- Automatically archives messages that exceed the window
- Semantic search for relevant past conversations
- Enabled via `longTermEnabled` in SimpleMemory node

## Capabilities

1. **View History**: See recent conversation in markdown format
2. **Save Notes**: Add explicit notes/memories to the conversation
3. **Search Memory**: Find relevant past conversations semantically
4. **Clear Memory**: Reset conversation history

## When to Use

**Save information when:**
- User says "remember this" or "don't forget"
- Important context is shared for future use
- User wants to note something specific

**Recall information when:**
- User asks "do you remember..."
- Context from earlier is needed
- User references previous discussions

**View history when:**
- User wants to see conversation log
- Debugging or reviewing past exchanges

## Tool Reference

### memory-save
Add a note or memory entry to the conversation history.

Parameters:
- `content` (required): Information to remember
- `role` (optional): "note" (default) or "context"

Example:
```json
{
  "content": "User's favorite color is blue",
  "role": "note"
}
```

### memory-get
Get recent conversation history or search for specific content.

Parameters:
- `count` (optional): Number of recent messages (default: 10)
- `search` (optional): Search term to find specific memories

Example - Recent history:
```json
{
  "count": 5
}
```

Example - Search:
```json
{
  "search": "favorite color"
}
```

### memory-clear
Clear conversation history.

Parameters:
- `confirm` (required): Must be true to clear

Example:
```json
{
  "confirm": true
}
```

### memory-view
View the current conversation history in markdown format.

Parameters: None

Returns the full markdown content of the conversation history.

### memory-search
Semantic search in long-term memory (if enabled).

Parameters:
- `query` (required): Search query
- `count` (optional): Number of results (default: 3)

Example:
```json
{
  "query": "what did we discuss about the project",
  "count": 5
}
```

## Markdown Format

The conversation history uses this format:

```markdown
# Conversation History

### **Human** (2025-01-30 10:15:32)
Hello, how are you?

### **Assistant** (2025-01-30 10:15:35)
I'm doing well! How can I help you today?

### **Note** (2025-01-30 10:16:00)
User prefers formal language.
```

## Integration with SimpleMemory Node

When connected to a Zeenie:
1. Conversation is automatically logged to markdown
2. Window size limits short-term memory
3. Overflow archives to vector DB (if long-term enabled)
4. You can view/edit the markdown in the node's parameter panel

## Best Practices

1. Use memory-save for explicit user requests to remember
2. Use memory-search for semantic recall of past discussions
3. Don't save sensitive information without user consent
4. The markdown is editable - users can manually curate their history
