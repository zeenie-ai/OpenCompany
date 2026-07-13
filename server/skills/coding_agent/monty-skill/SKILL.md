---
name: monty-skill
description: Run AI-generated Python in a hard sandbox (Pydantic Monty) with enforced time + memory limits and opt-in capabilities. Use for untrusted code; supports a Python subset.
allowed-tools: "monty_executor"
metadata:
  author: opencompany
  version: "1.0"
  category: code

---

# Sandboxed Python (Monty) Tool

Execute Python in a **deny-by-default** sandbox powered by [Pydantic Monty](https://github.com/pydantic/monty) — a minimal Python interpreter written in Rust. Unlike the `python_code` tool (CPython `exec` with a restricted namespace), Monty **enforces** wall-clock and memory limits and grants **zero** host access unless you explicitly request it.

Prefer this tool when running code you don't fully trust, or when you need guaranteed time/memory bounds. Use `python_code` instead when you need libraries Monty doesn't support (e.g. `random`, `collections`) or language features it lacks (classes, generators).

## How It Works

Connect the **Monty Executor** node to an agent's `input-tools` handle. The LLM calls the `sandboxed_python` tool with `code` (and optionally `capabilities`, `timeout`, `max_memory_mb`).

## Schema Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| code | string | Yes | — | Python code to run in the sandbox |
| timeout | int | No | 30 | Max wall-clock seconds (1–600), **enforced** |
| max_memory_mb | int | No | 256 | Max memory in MB (16–2048), **enforced** |
| capabilities | string[] | No | `[]` | Host grants to enable (see below); empty = no host access |

## Inputs & Output

- **`input_data`** — a dict of upstream node outputs is available as the variable `input_data`. Read with `input_data.get('someNode', {})`.
- **Return a result** by making it the **last expression** in your code (e.g. a final line that is just `output` or a dict literal).
- **`print(...)`** output is captured and returned as `console_output`.

## Supported Python Subset

| Works | Does NOT work |
|-------|---------------|
| `def`, closures, `lambda` | `class` definitions |
| `if` / `for` / `while` | `yield` / generators |
| `try` / `except` | `with` statements (without a workspace mount) |
| list / dict / set comprehensions | `match` / `case` |
| f-strings | `import random`, `import collections`, `import os` |
| `async def` / `await` | arbitrary third-party packages |
| `import math`, `import json`, `import re` | |

If you hit an unsupported feature, rewrite without it or switch to the `python_code` tool.

## Capabilities (opt-in host access)

By default the sandbox has **no** filesystem, network, or environment access. Request only what the task needs via `capabilities`:

| Capability | Grants | In-sandbox usage |
|------------|--------|------------------|
| `http_get` | An `http_get(url)` function (public http/https only; private/loopback hosts blocked) | `body = http_get("https://example.com")` |
| `workspace_read` | Read-only mount of the workflow workspace at `/workspace` | `open("/workspace/data.txt").read()` |
| `workspace_write` | Read-write mount at `/workspace` | `open("/workspace/out.txt", "w").write(text)` |

Requesting more than necessary is discouraged — each capability is a deliberate hole in the sandbox.

## Examples

**Basic calculation (no capabilities):**
```json
{
  "code": "total = sum(range(1, 11))\nprint(f'sum 1..10 = {total}')\ntotal"
}
```

**Process upstream data:**
```json
{
  "code": "nums = input_data.get('start', {}).get('numbers', [1,2,3])\navg = sum(nums) / len(nums)\nprint(f'avg = {avg}')\n{'avg': avg, 'count': len(nums)}"
}
```

**Use the curated stdlib:**
```json
{
  "code": "import math, json\nr = 5\narea = math.pi * r ** 2\njson.dumps({'radius': r, 'area': round(area, 2)})"
}
```

**Fetch a URL (requires http_get):**
```json
{
  "code": "body = http_get('https://example.com')\nlen(body)",
  "capabilities": ["http_get"]
}
```

**Read a workspace file (requires workspace_read):**
```json
{
  "code": "data = open('/workspace/input.txt').read()\nlen(data.splitlines())",
  "capabilities": ["workspace_read"]
}
```

**Enforced timeout (this will be terminated, not hang):**
```json
{
  "code": "while True:\n    pass",
  "timeout": 1
}
```

## Guidelines

1. **Return via the last expression** — don't rely on a magic `output` variable; the final expression's value is returned.
2. **Use `print()` for debugging** — captured as `console_output`.
3. **Request minimal `capabilities`** — start with none; add only what the task needs.
4. **Keep limits sane** — `timeout` and `max_memory_mb` are hard caps; raise them only when justified.
5. **Unsupported feature?** Rewrite without it, or fall back to the `python_code` tool.
