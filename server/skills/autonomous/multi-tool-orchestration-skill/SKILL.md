---
name: multi-tool-orchestration-skill
description: Combine connected tools with explicit data dependencies, bounded parallel work, and verified delivery.
allowed-tools: task_manager python_executor javascript_executor duckduckgo_search http_request calculator_tool current_time_tool
metadata:
  author: opencompany
  version: "2.0"
  category: autonomous
---

# Combining Tools

Inspect the tools actually connected to the agent and use their exposed
schemas and callable names. Frontmatter visual aliases are not invocation
names: examples include callable `web_search`, `python_code`,
`javascript_code`, `calculator`, and `get_current_time`.

## Data flow

1. Gather source data through the appropriate connected read tools.
2. Perform independent reads together only when the runtime supports it.
3. Wait for required dependencies before processing their results.
4. Pass the observed data explicitly to a computation tool; do not substitute invented sample values for live results.
5. Inspect output and errors before delivery. A successful envelope may still contain a nonzero shell exit or partial per-item failures.
6. Use a connected sending tool with its real recipient fields and current approval policy. A held draft is not a completed send.

Python Executor example (standalone calculation with synthetic input):

```python
prices = {"first": 185.50, "second": 141.25, "third": 378.90}
output = {"average": round(sum(prices.values()) / len(prices), 2)}
print(json.dumps(output))
```

Python Executor already injects `json`; imports fail and its namespace does
not persist between calls. JS/TS use their separate Bun sidecar. Tool-call
JSON contains the chosen tool's arguments, not an invented universal
`{"tool": ..., ...}` wrapper.

## Teams and effects

Managed team leads use `task_manager` for authorized assignments and review.
Return receipts as progress rather than presenting them as final results.
Only legacy graphs exposing direct delegation use that separate bounded
contract. Shared work is not permission to share another agent's private
Browser resource or rewrite locked tool configuration.

Treat sending, filesystem changes and process starts as effects with their
own recovery rules. Never reissue an uncertain effect simply because another
step failed. Observe cooperative Stop/Resume and explicit cancellation rather
than treating a pause as a fresh execution.
