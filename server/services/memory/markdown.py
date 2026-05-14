"""Markdown-based conversation-memory helpers.

Parse, append, and trim conversation history stored in the bespoke
``### **Human/Assistant** (timestamp)`` markdown format. Used by
aiAgent, chatAgent, and rlm_agent for persistent memory across turns.
The claude_code_agent bridge uses the JSONL helpers in
:mod:`services.memory.jsonl` instead.
"""

import re
from datetime import datetime
from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def parse_memory_markdown(content: str) -> List[BaseMessage]:
    """Parse markdown memory content into LangChain messages.

    Markdown format:
        ### **Human** (timestamp)
        message content

        ### **Assistant** (timestamp)
        response content
    """
    messages: List[BaseMessage] = []
    pattern = r'### \*\*(Human|Assistant)\*\*[^\n]*\n(.*?)(?=\n### \*\*|$)'
    for role, text in re.findall(pattern, content, re.DOTALL):
        text = text.strip()
        if text:
            msg_class = HumanMessage if role == "Human" else AIMessage
            messages.append(msg_class(content=text))
    return messages


def append_to_memory_markdown(content: str, role: str, message: str) -> str:
    """Append a message to markdown memory content."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = "Human" if role == "human" else "Assistant"
    entry = f"\n### **{label}** ({ts})\n{message}\n"
    # Remove the empty-state placeholder if present.
    return content.replace("*No messages yet.*\n", "") + entry


def trim_markdown_window(content: str, window_size: int) -> tuple:
    """Keep the last ``window_size`` message PAIRS; return
    ``(trimmed_content, removed_texts)``. Removed texts are returned
    so callers can hand them to the long-term vector store."""
    pattern = r'(### \*\*(Human|Assistant)\*\*[^\n]*\n.*?)(?=\n### \*\*|$)'
    blocks = [m[0] for m in re.findall(pattern, content, re.DOTALL)]

    if len(blocks) <= window_size * 2:
        return content, []

    keep = blocks[-(window_size * 2):]
    removed = blocks[:-(window_size * 2)]

    removed_texts: List[str] = []
    for block in removed:
        match = re.search(r"\n(.*)$", block, re.DOTALL)
        if match:
            removed_texts.append(match.group(1).strip())

    return "# Conversation History\n" + "\n".join(keep), removed_texts
