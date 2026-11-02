---
name: compaction-skill
description: Manage memory compaction by summarizing conversation history when approaching token limits
allowed-tools: memory
metadata:
  author: opencompany
  version: "2.0"
  category: memory
  icon: "📦"
  color: "#8B5CF6"

---

# Memory Compaction Skill

The agent runtime owns automatic transcript compaction and saves the resulting conversation through Context. This skill teaches how to write a useful summary when asked; it does not expose a callable that replaces a transcript. The separate `memory` tool stores explicit durable facts and cannot compact or clear Context.

## When to Compact

Compact memory when:
- The system indicates token threshold is approaching
- Conversation history becomes repetitive or verbose
- You need to preserve important context but reduce size
- Starting a new phase of work after completing a major task

## Compaction Summary Structure

When compacting, create a summary with these 5 sections:

### 1. Task Overview
What the user is trying to accomplish. Include:
- Primary goal or objective
- Key constraints or requirements
- Scope of the work

### 2. Current State
What's been completed and what's in progress:
- Completed tasks and their outcomes
- Work currently in flight
- Pending decisions or blockers

### 3. Important Discoveries
Key findings, decisions, or problems encountered:
- Technical discoveries or insights
- Decisions made and their rationale
- Problems encountered and solutions applied
- User preferences learned

### 4. Next Steps
What needs to happen next:
- Immediate actions required
- Planned approach for remaining work
- Dependencies or prerequisites

### 5. Context to Preserve
Critical details that must be retained:
- Specific values, IDs, or references
- User preferences or constraints
- Technical details needed for continuity
- Any warnings or caveats

## Compaction Format

Output the compacted summary in this format:

```markdown
# Conversation Summary (Compacted)
*Generated: [ISO timestamp]*

## Task Overview
[1-3 sentences describing the goal]

## Current State
- [Completed item 1]
- [Completed item 2]
- [In progress: description]

## Important Discoveries
- [Discovery 1 with context]
- [Decision made: rationale]
- [Problem solved: approach]

## Next Steps
1. [Next action]
2. [Following action]

## Context to Preserve
- [Critical detail 1]
- [Critical detail 2]
```

## Best Practices

1. **Be Concise**: Each section should be brief but complete
2. **Preserve Specifics**: Keep exact values, names, and references
3. **Capture Decisions**: Record WHY decisions were made, not just WHAT
4. **Include Failures**: Document what didn't work to avoid repetition
5. **Maintain Continuity**: Summary should allow seamless continuation

## What NOT to Include

- Verbose back-and-forth dialogue
- Redundant information
- Superseded decisions (only keep final decisions)
- Exploratory tangents that didn't lead anywhere
- Standard pleasantries or acknowledgments

## Example Compaction

**Before** (verbose history):
```
Human: Can you help me debug this Python function?
AI: Of course! Please share the function.
Human: Here's the function: def calculate(x): return x * 2
AI: I see the function. What issue are you experiencing?
Human: It returns None sometimes
AI: That's interesting. Can you show me an example input?
Human: calculate("5") returns None
AI: Ah, I see the issue! When you pass a string...
[continues for 50+ messages]
```

**After** (compacted):
```markdown
# Conversation Summary (Compacted)
*Generated: 2025-02-13T10:30:00Z*

## Task Overview
Debug Python function `calculate(x)` that returns None for some inputs.

## Current State
- Identified root cause: string inputs cause implicit None return
- Implemented fix with type checking and conversion
- Tests passing for int, float, and string inputs

## Important Discoveries
- Original function had no type validation
- String multiplication in Python doesn't raise error but behaves unexpectedly
- User prefers explicit error messages over silent failures

## Next Steps
1. Add input validation for edge cases (None, empty string)
2. Write unit tests for the fixed function

## Context to Preserve
- Function location: `utils/math_helpers.py:45`
- User wants to maintain backward compatibility
- Prefer raising ValueError over returning None
```

## Integration with Context and Memory

Runtime-triggered compaction replaces the runtime's working transcript with
its summary under the existing conversation contract. Do not claim that
saving a Memory item replaces that transcript, frees its token budget, or
archives earlier turns. Use `memory` with `remember` only for durable facts
that should survive separately; use `recall` or `list` to retrieve those facts.
Do not call removed hyphenated memory tools.
