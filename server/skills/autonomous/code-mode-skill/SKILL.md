---
name: code-mode-skill
description: Generate Python code instead of sequential tool calls (81-98% token savings)
allowed-tools: "python_executor javascript_executor"
metadata:
  author: opencompany
  version: "1.0"
  category: autonomous

---
# Code Mode Pattern

You are a Code Mode agent. Instead of calling tools sequentially, generate Python code that accomplishes the entire task in a single execution.

## Why Code Mode?

Research from Cloudflare and Anthropic shows Code Mode provides:
- **81-98% token savings** vs sequential tool call sequences
- **Explicit control flow** - loops, conditionals, error handling in code
- **Reusable patterns** - functions and variables persist across iterations
- **Better debugging** - executable code is easier to trace and verify

## Available Libraries

When generating Python code, you have access to:
```python
import math          # Mathematical functions (factorial, sqrt, sin, cos, etc.)
import json          # JSON parsing and serialization
import datetime      # Date and time operations
from datetime import timedelta
import re            # Regular expressions for text processing
import random        # Random number generation
from collections import Counter, defaultdict  # Data structures
```

## Core Pattern

1. **Analyze** - Understand the complete task requirements
2. **Generate** - Write complete Python code that solves the entire task
3. **Execute** - Use the `python_code` tool to run the code
4. **Return** - The code output becomes your response

## Simple Example

**Task**: "Calculate factorial of 10 and check if it's divisible by 7"

**Wrong approach** (multiple tool calls - wasteful):
```
1. Call calculator: factorial(10)
2. Get result: 3628800
3. Call calculator: 3628800 % 7
4. Get result: 0
5. Return answer
(4 LLM round-trips, ~4000 tokens)
```

**Code Mode approach** (single execution):
```python
import math
import json

# Calculate factorial
result = math.factorial(10)

# Check divisibility
divisible = result % 7 == 0

# Output structured result
output = {
    "factorial_of_10": result,
    "divisible_by_7": divisible,
    "remainder": result % 7
}
print(json.dumps(output, indent=2))
```
(2 LLM round-trips, ~800 tokens - 80% savings)

## Complex Example with Loop

**Task**: "Find all prime numbers between 1 and 100, show which are twin primes"

```python
import json

def is_prime(n):
    """Check if a number is prime."""
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True

# Find all primes
primes = [n for n in range(1, 101) if is_prime(n)]

# Find twin primes (primes that differ by 2)
twin_primes = []
for i in range(len(primes) - 1):
    if primes[i + 1] - primes[i] == 2:
        twin_primes.append((primes[i], primes[i + 1]))

output = {
    "primes": primes,
    "count": len(primes),
    "sum": sum(primes),
    "twin_primes": twin_primes,
    "twin_count": len(twin_primes)
}
print(json.dumps(output, indent=2))
```

## Data Processing Example

**Task**: "Analyze this list of numbers: find mean, median, mode, and standard deviation"

```python
import json
from collections import Counter
import math

# Input data (would come from user or previous step)
numbers = [23, 45, 67, 23, 89, 45, 23, 67, 90, 12, 45, 78]

# Calculate statistics
n = len(numbers)
mean = sum(numbers) / n

# Median
sorted_nums = sorted(numbers)
if n % 2 == 0:
    median = (sorted_nums[n//2 - 1] + sorted_nums[n//2]) / 2
else:
    median = sorted_nums[n//2]

# Mode
counter = Counter(numbers)
mode = counter.most_common(1)[0][0]

# Standard deviation
variance = sum((x - mean) ** 2 for x in numbers) / n
std_dev = math.sqrt(variance)

output = {
    "data": numbers,
    "count": n,
    "mean": round(mean, 2),
    "median": median,
    "mode": mode,
    "std_deviation": round(std_dev, 2),
    "min": min(numbers),
    "max": max(numbers)
}
print(json.dumps(output, indent=2))
```

## Error Handling in Code

Always include error handling for robustness:

```python
import json

def safe_divide(a, b):
    """Safely divide two numbers."""
    try:
        return {"result": a / b, "success": True}
    except ZeroDivisionError:
        return {"error": "Division by zero", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}

# Example usage
results = []
test_cases = [(10, 2), (15, 3), (7, 0), (100, 4)]

for a, b in test_cases:
    result = safe_divide(a, b)
    result["operation"] = f"{a} / {b}"
    results.append(result)

print(json.dumps({"calculations": results}, indent=2))
```

## When NOT to Use Code Mode

Use specific tools instead for:
- **External API calls** - Use `http_request` tool for network requests
- **Database operations** - Use data-specific tools
- **File operations** - Use file-specific tools
- **User interaction** - Respond directly without code
- **Real-time data** - Use `web_search` or specific data tools
- **Device control** - Use Android/device-specific tools

## Integration with Multiple Tools

When you need both code AND external tools, use this pattern:
1. Gather data using appropriate tools (http_request, web_search, etc.)
2. Process the gathered data using Code Mode
3. Return the combined result

Example flow:
```
User: "Search for Python release dates and calculate days since each release"

1. Use web_search tool: "Python version release dates"
2. Use python_code to process:
   - Parse the dates from search results
   - Calculate days since each release
   - Format output nicely
```

## Output Format

Always output results as JSON for downstream processing:
```python
import json
# ... your calculations ...
print(json.dumps(output, indent=2))
```

This enables:
- Easy parsing by downstream nodes
- Structured data for further processing
- Clear, readable output for users
