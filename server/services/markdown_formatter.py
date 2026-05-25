"""Platform-specific markdown formatting using markdown-it-py.

Converts GFM-style markdown (as produced by LLMs) to platform-native formats:
- Telegram HTML: render to HTML, convert unsupported tags
- WhatsApp: walk token stream, map to WhatsApp-native syntax
- Plain text: strip all formatting
"""

import re
from markdown_it import MarkdownIt


_md = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable(["strikethrough", "table"])


def _table_to_pre(match: re.Match) -> str:
    """Convert an HTML table to preformatted pipe-separated text."""
    table_html = match.group(0)
    rows = []
    for tr in re.finditer(r"<tr>(.*?)</tr>", table_html, re.DOTALL):
        cells = [c.strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr.group(1), re.DOTALL)]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    widths = [0] * max(len(r) for r in rows)
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    for idx, row in enumerate(rows):
        line = " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(line)
        if idx == 0:
            lines.append("-+-".join("-" * w for w in widths))
    return "<pre>" + "\n".join(lines) + "</pre>"


def to_telegram_html(text: str) -> str:
    """Convert GFM markdown to Telegram-compatible HTML.

    Telegram HTML supports: <b>, <i>, <s>, <code>, <pre>, <a>, <blockquote>, <u>.
    Unsupported tags are converted: <h1>-<h6> -> <b>, <ul>/<ol>/<li> -> bullet text,
    <table> -> <pre>, <p>/<br>/<hr> -> stripped/newlines.
    """
    if not text or not text.strip():
        return text

    html = _md.render(text).strip()

    # Convert <br> to newline (not supported by Telegram HTML)
    html = re.sub(r"<br\s*/?>", "\n", html)

    # Convert tables to preformatted text (Telegram doesn't support <table>)
    html = re.sub(r"<table>.*?</table>", _table_to_pre, html, flags=re.DOTALL)

    # Convert headings to bold
    html = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"<b>\1</b>", html, flags=re.DOTALL)

    # Convert list items to bullet lines
    html = re.sub(r"<li>(.*?)</li>", r"  - \1", html, flags=re.DOTALL)
    html = re.sub(r"</?[ou]l>", "", html)

    # Strip <p> tags, replace closing </p> with double newline
    html = re.sub(r"<p>", "", html)
    html = re.sub(r"</p>", "\n", html)

    # Strip <hr> tags
    html = re.sub(r"<hr\s*/?>", "", html)

    # Clean up blockquote inner whitespace
    html = re.sub(r"<blockquote>\s*\n*", "<blockquote>", html)
    html = re.sub(r"\s*\n*</blockquote>", "</blockquote>", html)

    # Map <strong> -> <b>, <em> -> <i>, <del>/<strike> -> <s>
    html = html.replace("<strong>", "<b>").replace("</strong>", "</b>")
    html = html.replace("<em>", "<i>").replace("</em>", "</i>")
    html = html.replace("<del>", "<s>").replace("</del>", "</s>")
    html = html.replace("<strike>", "<s>").replace("</strike>", "</s>")

    # Strip class attributes from <code> (Telegram supports <code> but not class attr)
    html = re.sub(r'<code class="[^"]*">', "<code>", html)

    # Strip ALL unsupported HTML tags (keep only Telegram-supported ones)
    # Telegram supports: b, i, s, u, code, pre, a, blockquote, tg-spoiler, tg-emoji
    _TELEGRAM_ALLOWED = frozenset({"b", "i", "s", "u", "code", "pre", "a", "blockquote", "tg-spoiler", "tg-emoji"})

    def _strip_unsupported(m: re.Match) -> str:
        tag = m.group(1).lower().split()[0].strip("/")
        return m.group(0) if tag in _TELEGRAM_ALLOWED else ""

    html = re.sub(r"<(/?\s*[a-zA-Z][a-zA-Z0-9-]*)[^>]*>", _strip_unsupported, html)

    # Collapse multiple newlines
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def to_whatsapp(text: str) -> str:
    """Convert GFM markdown to WhatsApp-native formatting.

    WhatsApp supports: *bold*, _italic_, ~strikethrough~, ```code```, > quote (single line).
    """
    if not text or not text.strip():
        return text

    tokens = _md.parse(text)
    result = []
    _walk_tokens(tokens, result)
    output = "".join(result)
    # Collapse excessive newlines
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output.strip()


def _walk_tokens(tokens: list, result: list, depth: int = 0) -> None:
    """Walk markdown-it token stream and emit WhatsApp-formatted text."""
    in_list = False
    in_blockquote = False
    table_rows: list = []
    table_row: list = []
    in_table = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        ttype = token.type

        if ttype == "inline" and token.children:
            if in_table:
                # Collect cell text for table formatting
                cell_parts: list = []
                _walk_tokens(token.children, cell_parts, depth)
                table_row.append("".join(cell_parts))
            else:
                _walk_tokens(token.children, result, depth)
        elif ttype == "text":
            result.append(token.content)
        elif ttype == "code_inline":
            result.append(f"```{token.content}```")
        elif ttype == "softbreak":
            result.append("\n")
        elif ttype == "hardbreak":
            result.append("\n")
        elif ttype == "fence":
            result.append(f"```{token.content}```")
        elif ttype == "code_block":
            result.append(f"```{token.content}```")
        elif ttype in ("strong_open", "bold_open"):
            result.append("*")
        elif ttype in ("strong_close", "bold_close"):
            result.append("*")
        elif ttype in ("em_open", "emphasis_open"):
            result.append("_")
        elif ttype in ("em_close", "emphasis_close"):
            result.append("_")
        elif ttype in ("s_open",):
            result.append("~")
        elif ttype in ("s_close",):
            result.append("~")
        elif ttype == "paragraph_open":
            if not in_list and not in_blockquote and i > 0:
                result.append("\n")
        elif ttype == "paragraph_close":
            if not in_list and not in_blockquote:
                result.append("\n")
        elif ttype == "heading_open":
            if i > 0:
                result.append("\n")
            result.append("*")
        elif ttype == "heading_close":
            result.append("*\n")
        elif ttype in ("bullet_list_open", "ordered_list_open"):
            in_list = True
        elif ttype in ("bullet_list_close", "ordered_list_close"):
            in_list = False
        elif ttype == "list_item_open":
            result.append("  - " if depth == 0 else "    - ")
        elif ttype == "list_item_close":
            if not result or not result[-1].endswith("\n"):
                result.append("\n")
        elif ttype == "blockquote_open":
            in_blockquote = True
            result.append("> ")
        elif ttype == "blockquote_close":
            in_blockquote = False
            result.append("\n")
        elif ttype == "hr":
            result.append("\n---\n")
        elif ttype == "link_open":
            pass  # WhatsApp auto-links URLs
        elif ttype == "link_close":
            href = None
            for j in range(i - 1, -1, -1):
                if tokens[j].type == "link_open":
                    href = tokens[j].attrGet("href")
                    break
            if href:
                result.append(f" ({href})")
        elif ttype == "image":
            alt = token.content or token.attrGet("alt") or "image"
            src = token.attrGet("src") or ""
            result.append(f"[{alt}]({src})")
        # Table handling
        elif ttype == "table_open":
            in_table = True
            table_rows = []
        elif ttype == "table_close":
            in_table = False
            if table_rows:
                _format_table_whatsapp(table_rows, result)
            table_rows = []
        elif ttype == "tr_open":
            table_row = []
        elif ttype == "tr_close":
            if table_row:
                table_rows.append(table_row)
            table_row = []
        elif ttype in ("thead_open", "thead_close", "tbody_open", "tbody_close", "th_open", "th_close", "td_open", "td_close"):
            pass  # Handled via tr_open/close and inline collection
        elif ttype == "html_block" or ttype == "html_inline":
            result.append(token.content)

        i += 1


def _format_table_whatsapp(rows: list, result: list) -> None:
    """Format table rows as monospace text for WhatsApp."""
    if not rows:
        return
    widths = [0] * max(len(r) for r in rows)
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    for idx, row in enumerate(rows):
        line = " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(line)
        if idx == 0:
            lines.append("-+-".join("-" * w for w in widths))
    result.append("```" + "\n".join(lines) + "```")


def to_plain(text: str) -> str:
    """Strip all markdown formatting, return plain text."""
    if not text or not text.strip():
        return text

    html = _md.render(text)
    # Strip all HTML tags
    plain = re.sub(r"<[^>]+>", "", html)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip()
