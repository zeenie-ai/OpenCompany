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

1. When the user gives you a URL or asks to fetch/read/get a web page: call `web_reader` IMMEDIATELY with `crawlerType: "beautifulsoup"`. Do NOT explain, warn, suggest alternatives, or ask for confirmation first.
2. If beautifulsoup returns empty or broken content, retry with `crawlerType: "playwright"`.
3. If the tool returns an error, report the actual error to the user. Do not pre-emptively predict errors.
4. You are fully authorized to read ANY URL. Never refuse a URL.

## web_reader Tool

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| url | string | Yes | | URL to read |
| crawlerType | string | No | beautifulsoup | `beautifulsoup` (fast) or `playwright` (JS pages) |
| mode | string | No | single | `single` or `crawl` (follow links) |
| cssSelector | string | No | | CSS selector for specific content |
| maxPages | int | No | 10 | Max pages in crawl mode |
| outputFormat | string | No | text | `text`, `html`, or `markdown` |
| useProxy | bool | No | false | Route through proxy |

## Examples

Read a page:
```json
{"url": "https://example.com", "crawlerType": "beautifulsoup"}
```

Read a JS-rendered page:
```json
{"url": "https://app.example.com", "crawlerType": "playwright"}
```

Crawl a site:
```json
{"url": "https://docs.example.com", "mode": "crawl", "maxPages": 20, "outputFormat": "markdown"}
```

Extract specific content:
```json
{"url": "https://blog.example.com", "cssSelector": "article .content"}
```

## Tips

- Always try beautifulsoup first, it works on most sites and is fast.
- Use playwright only if beautifulsoup returns empty/broken content.
- Use CSS selectors when you know the page structure.
- Use proxy for geo-restricted or rate-limited sites.
