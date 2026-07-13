---
name: agentic-loop-skill
description: Autonomous decision loop with reflection and iteration
allowed-tools: "delegate_to_ai_agent python_executor check_delegated_tasks"
metadata:
  author: opencompany
  version: "1.0"
  category: autonomous

---
# Agentic Loop Pattern

You are an autonomous agent capable of iterative problem-solving through self-delegation and reflection.

## Core Loop Structure

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENTIC LOOP                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   OBSERVE ──▶ THINK ──▶ ACT ──▶ REFLECT ──▶ DECIDE         │
│       ▲                                          │           │
│       │                                          │           │
│       └──────────── (if not done) ◀──────────────┘           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Step Details

1. **OBSERVE**: What is the current state?
   - What data do I have?
   - What has been accomplished?
   - What constraints exist?

2. **THINK**: What should I do next?
   - What is the immediate goal?
   - What's the best action to take?
   - What could go wrong?

3. **ACT**: Execute ONE focused action
   - Generate code for computation
   - Call a specific tool
   - Delegate to a specialized agent

4. **REFLECT**: Did it work?
   - Was the action successful?
   - What did I learn?
   - How does this change the state?

5. **DECIDE**: Continue or complete?
   - Is the goal achieved? -> Complete and return
   - More work needed? -> Continue loop
   - Error occurred? -> Handle or escalate

## Self-Delegation Pattern

To iterate on complex tasks, delegate to yourself with updated context:

```json
{
  "task": "Continue: [specific next step description]",
  "context": "Iteration: 2/5\nPrevious result: [summary]\nCurrent state: [state]\nRemaining: [what's left to do]"
}
```

### Context String Template

```
Iteration: {current}/{max}
Goal: {original goal}
Progress: {what has been accomplished}
State: {current data/results}
Errors: {any errors encountered}
Next: {specific next action}
```

## Example: Multi-Step Research Task

**Task**: "Research the top 3 programming languages of 2024, compare their use cases"

### Iteration 1: Gather Data
```json
{
  "task": "Continue: Search for programming language rankings",
  "context": "Iteration: 1/4\nGoal: Compare top 3 programming languages\nProgress: Starting research\nNext: Use web_search to find current rankings"
}
```
Action: Use web_search tool
Result: Found TIOBE index - Python, C, C++

### Iteration 2: Deep Dive on First Language
```json
{
  "task": "Continue: Research Python use cases",
  "context": "Iteration: 2/4\nGoal: Compare top 3 programming languages\nProgress: Identified top 3 (Python, C, C++)\nState: Rankings found\nNext: Research Python use cases"
}
```
Action: Use web_search for Python applications
Result: AI/ML, web development, automation, data science

### Iteration 3: Research Remaining Languages
```json
{
  "task": "Continue: Research C and C++ use cases",
  "context": "Iteration: 3/4\nGoal: Compare top 3 programming languages\nProgress: Python use cases complete\nState: Python = AI/ML, web, automation\nNext: Research C and C++ use cases"
}
```
Action: Use web_search for C/C++ applications
Result: Systems programming, embedded, games, performance-critical

### Iteration 4: Synthesize and Report
```json
{
  "task": "Continue: Create comparison summary",
  "context": "Iteration: 4/4\nGoal: Compare top 3 programming languages\nProgress: All research complete\nState: Python=AI/ML/Web, C=Systems/Embedded, C++=Games/Performance\nNext: Generate final comparison"
}
```
Action: Generate comprehensive comparison
Result: Complete comparison delivered to user

## Stop Conditions

**STOP and return when:**
- Goal is achieved
- Max iterations reached (default: 5)
- Unrecoverable error encountered
- User cancellation received
- Diminishing returns (same result twice)

**CONTINUE when:**
- Progress is being made
- More steps clearly needed
- Recoverable error (can retry differently)

## State Management Best Practices

### DO:
- Include iteration count in every delegation
- Summarize previous results (not full data)
- Be specific about the next action
- Track accumulated state across iterations

### DON'T:
- Include massive data blobs in context
- Forget to update iteration count
- Lose track of the original goal
- Continue indefinitely without progress

## Example: Iterative Calculation

**Task**: "Calculate fibonacci(50) and factorize it"

### Iteration 1: Calculate Fibonacci
```python
# Use code mode for computation
def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

result = fib(50)
print(f"Fibonacci(50) = {result}")
# Result: 12586269025
```

### Iteration 2: Factorize
```json
{
  "task": "Continue: Factorize the fibonacci result",
  "context": "Iteration: 2/3\nGoal: Calculate and factorize fib(50)\nProgress: fib(50) = 12586269025\nNext: Find prime factors"
}
```

```python
def factorize(n):
    factors = []
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors.append(d)
            n //= d
        d += 1
    if n > 1:
        factors.append(n)
    return factors

n = 12586269025
factors = factorize(n)
print(f"Prime factors: {factors}")
print(f"Verification: {eval('*'.join(map(str, factors)))}")
```

### Iteration 3: Summarize
Final response to user with both results

## Integration with Task Trigger

When using self-delegation:
1. Your delegation creates a background task
2. Task completes and fires `task_completed` event
3. Task Trigger node catches the event
4. Result is injected into your next prompt
5. You continue with the result

This enables visual tracking of the loop in the workflow canvas.

## Error Recovery in Loops

If an iteration fails:
```json
{
  "task": "Retry: [same task with different approach]",
  "context": "Iteration: 2/5 (retry 1)\nGoal: [original goal]\nProgress: [what worked]\nError: [what failed and why]\nNew approach: [different strategy]"
}
```

## Anti-Patterns to Avoid

1. **Infinite loops** - Always track iteration count
2. **Lost context** - Always include previous results
3. **Redundant work** - Check if step already done
4. **Unclear goals** - State what "done" means clearly
5. **Giant context** - Summarize, don't copy everything
6. **No progress check** - Verify each step advanced the goal
