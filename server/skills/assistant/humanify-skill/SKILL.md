---
name: humanify-skill
description: Output human-readable text in pretty formats instead of raw markdown or code blocks
metadata:
  author: opencompany
  version: "1.0"
  category: formatting
  icon: "✨"
  color: "#F59E0B"

---

# Humanify Output Skill

Format your responses for human readability. Output clean, pretty text that's easy to scan and understand - not raw markdown syntax or code-heavy formatting.

## Core Principles

1. **Plain Language First**: Write naturally, as if speaking to someone
2. **Visual Clarity**: Use spacing, indentation, and structure for scannability
3. **No Raw Syntax**: Avoid showing markdown symbols, JSON, or code unless specifically requested
4. **Progressive Detail**: Lead with key information, details follow

## Formatting Guidelines

### Instead of Markdown Headers
```
Bad:  ## Section Title
Good: SECTION TITLE
      -------------
```

### Instead of Bullet Lists with Symbols
```
Bad:  - Item one
      - Item two

Good: Item one
      Item two

  or: * Item one
      * Item two
```

### Instead of Code Blocks for Data
```
Bad:  ```json
      {"name": "John", "age": 30}
      ```

Good: Name: John
      Age: 30
```

### Instead of Tables with Pipes
```
Bad:  | Name | Value |
      |------|-------|
      | Foo  | 123   |

Good: Name     Value
      ----     -----
      Foo      123
```

### Instead of Raw URLs
```
Bad:  Check https://example.com/very/long/path/to/resource

Good: Check the resource page (link available)
  or: Resource: example.com
```

## Response Patterns

### For Status Updates
```
Status: Complete
Time: 2.3 seconds
Result: Successfully processed 15 items

Details:
  Processed: 15
  Skipped: 2
  Errors: 0
```

### For Lists of Items
```
Found 3 matching files:

  1. config.json
     Location: /app/settings
     Modified: Today

  2. config.backup.json
     Location: /app/backups
     Modified: Yesterday

  3. config.old.json
     Location: /archive
     Modified: Last week
```

### For Explanations
```
The function calculates the total by:

  First, it gathers all input values
  Then, it filters out any invalid entries
  Finally, it sums the remaining numbers

Note: Empty inputs are treated as zero.
```

### For Errors or Warnings
```
Could not complete the request.

Reason: The file was not found at the specified location.

Suggestions:
  Check if the file path is correct
  Verify the file exists
  Ensure you have read permissions
```

### For Confirmations
```
Done! Created new user account.

  Username: jsmith
  Email: john@example.com
  Role: Editor

Next: The user will receive a welcome email shortly.
```

## When to Use Each Style

### Use Pretty Formatting When:
- Showing results or status
- Explaining concepts
- Listing options or items
- Providing instructions
- Displaying data summaries

### Keep Technical Formatting When:
- User explicitly requests code/markdown
- Showing code that needs to be copied
- Displaying configuration files
- Technical documentation
- API responses that need exact format

## Typography Tips

### Visual Separators
```
Section breaks:    ---------------
Light dividers:    . . . . . . . .
Box drawing:       +-------------+
                   |  Content    |
                   +-------------+
```

### Emphasis Without Markdown
```
Instead of **bold**:     Use CAPS or spacing
Instead of *italic*:     Use quotes or context
Instead of `code`:       Use clear naming
```

### Spacing for Clarity
```
Group related items with spacing:

  Primary Actions
    Save changes
    Submit form

  Secondary Actions
    Cancel
    Reset
```

## Examples

### Bad (Raw Markdown)
```
### Results
- **Found**: 5 files
- **Size**: 2.3MB
- **Status**: `complete`

See [documentation](https://docs.example.com) for more info.
```

### Good (Humanified)
```
RESULTS
-------
Found: 5 files
Size: 2.3 MB
Status: Complete

See the documentation for more details.
```

## Remember

The goal is communication, not formatting. Choose the clearest way to convey information to a human reader. When in doubt, write it as you would explain it to a colleague standing next to you.
