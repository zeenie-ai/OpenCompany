---
name: crawlee-scraper-skill
description: Read and extract content from any web page URL.
allowed-tools: "crawlee_scraper"
metadata:
  author: opencompany
  version: "1.0"
  category: web

---

# Web Reader Skill

## Critical Rules

1. When the user gives you a URL or asks to fetch/read/get a web page: call the connected scraper by the callable name in your tool catalogue with `crawler_type: "beautifulsoup"`. Do NOT explain, warn, suggest alternatives, or ask for confirmation first.
2. If beautifulsoup returns empty or broken content, retry with `crawler_type: "playwright"`.
3. If the tool returns an error, report the actual error to the user. Do not pre-emptively predict errors.
4. Use only URLs permitted by the workflow, browser/network policy and the owner's instructions. Report an actual policy refusal instead of claiming unrestricted access.

## Scraper tool contract

The plugin declares `web_reader`; a saved `tool_name` can rename it. Use the
name in the current tool catalogue. All arguments below use the backend
parameter names.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| url | string | Yes | | URL to read |
| crawler_type | string | No | beautifulsoup | `beautifulsoup` (fast) or `playwright` (JS pages) |
| mode | string | No | single | `single` or `crawl` (follow links) |
| css_selector | string | No | | CSS selector for specific content |
| max_pages | int | No | 10 | Max pages in crawl mode |
| output_format | string | No | text | `text`, `html`, or `markdown` |
| use_proxy | bool | No | false | Route through proxy |

## Examples

Read a page:
```json
{"url": "https://example.com", "crawler_type": "beautifulsoup"}
```

Read a JS-rendered page:
```json
{"url": "https://app.example.com", "crawler_type": "playwright"}
```

Crawl a site:
```json
{"url": "https://docs.example.com", "mode": "crawl", "max_pages": 20, "output_format": "markdown"}
```

Extract specific content:
```json
{"url": "https://blog.example.com", "css_selector": "article .content"}
```

## Tips

- Always try beautifulsoup first, it works on most sites and is fast.
- Use playwright only if beautifulsoup returns empty/broken content.
- Use CSS selectors when you know the page structure.
- Use proxy for geo-restricted or rate-limited sites.
