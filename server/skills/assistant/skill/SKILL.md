---
name: skill
description: Select and load connected skills through the Skill tool using progressive disclosure. Use before applying a connected skill's procedures or consulting its declared references and scripts.
metadata:
  author: opencompany
  version: "1.0"
  category: automation
  icon: "✨"
  color: "#8b5cf6"
---

# Skill

Use the `Skill` tool to retrieve instructions from skills connected through the
Master Skill node.

1. Inspect the connected skill names and descriptions exposed by the tool.
2. When the task materially matches a skill, call `Skill` with
   `action="load"` and its exact `skill_name` before applying that skill.
3. Treat returned instructions as authoritative for the current conversation.
   Do not infer missing instructions from metadata alone.
4. Inspect the returned resource manifest. Use `read_resource` for a bounded
   page or `search_resource` for line-numbered matches only when more detail is
   required.
5. Do not load unrelated skills. If a repeated load returns `already_loaded`,
   continue using the instructions already present in the conversation.
6. Loading a skill retrieves guidance; it does not execute ordinary action
   tools. Call connected tools separately when the loaded instructions require
   them.
