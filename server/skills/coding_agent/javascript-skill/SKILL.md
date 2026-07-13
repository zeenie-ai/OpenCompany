---
name: javascript-skill
description: Execute JavaScript code for calculations, data processing, and JSON manipulation. Full ES6+ support with Node.js runtime.
allowed-tools: "javascript_executor"
metadata:
  author: opencompany
  version: "1.0"
  category: code

---

# JavaScript Code Execution Tool

Execute JavaScript code for calculations, data processing, and JSON manipulation.

## How It Works

This skill provides instructions for the **JavaScript Executor** tool node. Connect the **JavaScript Executor** node to Zeenie's `input-tools` handle to enable JavaScript code execution.

## javascript_code Tool

Execute JavaScript code and return results.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | Yes | JavaScript code to execute |

### Available Features

| Feature | Description |
|---------|-------------|
| ES6+ syntax | Arrow functions, destructuring, spread operator |
| `JSON` | JSON.parse() and JSON.stringify() |
| `Math` | Mathematical operations |
| `Date` | Date and time manipulation |
| `Array` methods | map, filter, reduce, sort, etc. |
| `Object` methods | keys, values, entries, assign |
| `String` methods | All standard string methods |

### Built-in Variables

| Variable | Description |
|----------|-------------|
| `input_data` | Data from connected workflow nodes (object) |
| `output` | Set this to return a result |

### Output Methods

1. **Set `output` variable**: Returns structured data to the workflow
2. **Use `console.log()`**: Captured as console output

### Examples

**Basic calculation:**
```json
{
  "code": "const result = 25 * 4 + 10;\nconsole.log(`Result: ${result}`);\noutput = result;"
}
```

**Array processing:**
```json
{
  "code": "const numbers = input_data.numbers || [1, 2, 3, 4, 5];\nconst total = numbers.reduce((a, b) => a + b, 0);\nconst average = total / numbers.length;\nconsole.log(`Total: ${total}, Average: ${average}`);\noutput = { total, average };"
}
```

**Filter array:**
```json
{
  "code": "const numbers = input_data.numbers || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];\nconst evens = numbers.filter(n => n % 2 === 0);\nconsole.log(`Even numbers: ${evens}`);\noutput = evens;"
}
```

**Transform data:**
```json
{
  "code": "const users = input_data.users || [{name: 'John', age: 30}, {name: 'Jane', age: 25}];\nconst names = users.map(u => u.name);\nconsole.log(`Names: ${names.join(', ')}`);\noutput = names;"
}
```

**JSON manipulation:**
```json
{
  "code": "const data = { name: 'John', age: 30, city: 'NYC' };\nconst json = JSON.stringify(data, null, 2);\nconsole.log(json);\noutput = data;"
}
```

**Object operations:**
```json
{
  "code": "const obj = { a: 1, b: 2, c: 3 };\nconst keys = Object.keys(obj);\nconst values = Object.values(obj);\nconst sum = values.reduce((a, b) => a + b, 0);\nconsole.log(`Sum of values: ${sum}`);\noutput = { keys, values, sum };"
}
```

**Date operations:**
```json
{
  "code": "const now = new Date();\nconst tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);\nconst formatted = tomorrow.toISOString().split('T')[0];\nconsole.log(`Tomorrow: ${formatted}`);\noutput = formatted;"
}
```

**Sort array:**
```json
{
  "code": "const items = input_data.items || ['banana', 'apple', 'cherry'];\nconst sorted = [...items].sort();\nconsole.log(`Sorted: ${sorted}`);\noutput = sorted;"
}
```

**String processing:**
```json
{
  "code": "const text = 'Hello World, Hello JavaScript';\nconst words = text.split(' ');\nconst unique = [...new Set(words)];\nconsole.log(`Unique words: ${unique}`);\noutput = unique;"
}
```

**Destructuring and spread:**
```json
{
  "code": "const { name, age } = input_data.user || { name: 'John', age: 30 };\nconst profile = { name, age, active: true };\nconst extended = { ...profile, role: 'admin' };\nconsole.log(JSON.stringify(extended));\noutput = extended;"
}
```

### Response Format

**Success:**
```json
{
  "success": true,
  "result": { "total": 15, "average": 3 },
  "output": "Total: 15, Average: 3"
}
```

**Error:**
```json
{
  "error": "ReferenceError: undefinedVar is not defined"
}
```

## Use Cases

| Use Case | Approach |
|----------|----------|
| Array manipulation | Use map, filter, reduce |
| JSON processing | Use JSON.parse, JSON.stringify |
| Object operations | Use Object.keys, values, entries |
| String processing | Use split, join, replace |
| Math calculations | Use Math methods |
| Date operations | Use Date object |
| Data transformation | Use spread and destructuring |

## Guidelines

1. **Always set `output`**: This returns data to the workflow
2. **Use `console.log()` for debugging**: Output is captured and returned
3. **Use ES6+ features**: Arrow functions, destructuring, spread
4. **Handle undefined**: Use `|| defaultValue` pattern
5. **Keep code focused**: One task per execution
6. **No network access**: Use http-skill for web requests
7. **Timeout**: Default 30 seconds max execution time

## Security Restrictions

- No network/fetch operations
- No file system access (no require('fs'))
- No child processes
- Limited execution time (30 seconds)
- Sandboxed environment

## Common Patterns

**Default values:**
```javascript
const data = input_data.value || 'default';
```

**Null-safe access:**
```javascript
const name = input_data?.user?.name || 'Unknown';
```

**Array to object:**
```javascript
const arr = [{id: 1, name: 'A'}, {id: 2, name: 'B'}];
const obj = Object.fromEntries(arr.map(x => [x.id, x.name]));
```

## Setup Requirements

1. Connect the **JavaScript Executor** node to Zeenie's `input-tools` handle
2. Node.js must be installed on the server
