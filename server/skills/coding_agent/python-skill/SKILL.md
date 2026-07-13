---
name: python-skill
description: Execute Python code for calculations, data processing, and automation. Access to math, json, datetime, collections, and more.
allowed-tools: "python_executor"
metadata:
  author: opencompany
  version: "1.0"
  category: code

---

# Python Code Execution Tool

Execute Python code for calculations, data processing, and automation tasks.

## How It Works

This skill provides instructions for the **Python Executor** tool node. Connect the **Python Executor** node to Zeenie's `input-tools` handle to enable Python code execution.

## python_code Tool

Execute Python code and return results.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | Yes | Python code to execute |

### Available Libraries

All modules below are **pre-injected as names** in the sandbox. **Do not use `import` statements** — they will fail (`ImportError: __import__ not found`). Reference each name directly.

| Name | Reference Style | Description |
|------|-----------------|-------------|
| `math` | `math.sqrt(2)` | Mathematical functions |
| `json` | `json.loads(s)` / `json.dumps(d)` | JSON encoding/decoding |
| `datetime` | `datetime.datetime.now()` (the **module**) | Date/time module |
| `timedelta` | `timedelta(days=30)` | Duration class (already unwrapped) |
| `re` | `re.findall(pat, text)` | Regular expressions |
| `random` | `random.randint(1, 10)` | Random number generation |
| `Counter` | `Counter(items)` | Count hashable objects (already unwrapped) |
| `defaultdict` | `defaultdict(list)` | Dictionary with defaults (already unwrapped) |

### Built-in Variables

| Variable | Description |
|----------|-------------|
| `input_data` | Data from connected workflow nodes (dict) |
| `output` | Set this to return a result |

### Output Methods

1. **Set `output` variable**: Returns structured data to the workflow
2. **Use `print()`**: Captured as console output

### Examples

**Basic calculation:**
```json
{
  "code": "result = 25 * 4 + 10\nprint(f'Result: {result}')\noutput = result"
}
```

**Calculate tip:**
```json
{
  "code": "bill = 85.50\ntip_percent = 15\ntip = bill * (tip_percent / 100)\ntotal = bill + tip\nprint(f'Tip: ${tip:.2f}')\nprint(f'Total: ${total:.2f}')\noutput = {'tip': tip, 'total': total}"
}
```

**Generate random numbers:**
```json
{
  "code": "numbers = [random.randint(1, 100) for _ in range(5)]\nprint(f'Random numbers: {numbers}')\noutput = numbers"
}
```

**Date calculations:**
```json
{
  "code": "today = datetime.datetime.now()\nfuture = today + timedelta(days=30)\nresult = future.strftime('%Y-%m-%d')\nprint(f'30 days from now: {result}')\noutput = result"
}
```

**Process data:**
```json
{
  "code": "data = input_data.get('numbers', [1, 2, 3, 4, 5])\ntotal = sum(data)\naverage = total / len(data)\nprint(f'Total: {total}, Average: {average}')\noutput = {'total': total, 'average': average}"
}
```

**Parse JSON:**
```json
{
  "code": "json_str = '{\"name\": \"John\", \"age\": 30}'\ndata = json.loads(json_str)\nprint(f'Name: {data[\"name\"]}')\noutput = data"
}
```

**Count items:**
```json
{
  "code": "items = ['apple', 'banana', 'apple', 'cherry', 'banana', 'apple']\ncounts = dict(Counter(items))\nprint(f'Counts: {counts}')\noutput = counts"
}
```

**Text processing:**
```json
{
  "code": "text = 'Contact: john@example.com or jane@test.org'\nemails = re.findall(r'\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b', text)\nprint(f'Found emails: {emails}')\noutput = emails"
}
```

### Response Format

**Success:**
```json
{
  "success": true,
  "result": {"tip": 12.825, "total": 98.325},
  "output": "Tip: $12.83\nTotal: $98.33"
}
```

**Error:**
```json
{
  "error": "name 'undefined_var' is not defined"
}
```

## Use Cases

| Use Case | Approach |
|----------|----------|
| Math calculations | Use `math` library functions |
| Date/time operations | Use `datetime` and `timedelta` |
| Data analysis | Use list comprehensions, sum, len |
| Random generation | Use `random` library |
| Text parsing | Use `re` regular expressions |
| JSON manipulation | Use `json.loads()` and `json.dumps()` |
| Counting | Use `Counter` from collections |

## Guidelines

1. **Always set `output`**: This returns data to the workflow
2. **Use `print()` for debugging**: Output is captured and returned
3. **Keep code focused**: One task per execution
4. **Handle errors**: Use try/except for robust code
5. **No network access**: Use http-skill for web requests
6. **No file system access**: Restricted to safe operations
7. **Timeout**: Default 30 seconds max execution time

## Security Restrictions

- No network/socket operations
- No file system access outside designated areas
- No subprocess/shell commands
- Limited execution time (30 seconds)
- Sandboxed environment

## Setup Requirements

1. Connect the **Python Executor** node to Zeenie's `input-tools` handle
2. Python must be installed on the server
