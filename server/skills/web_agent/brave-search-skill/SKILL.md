---
name: brave-search-skill
description: Search the web using Brave Search API for privacy-focused, independent search results with no tracking.
allowed-tools: brave_search
metadata:
  author: opencompany
  version: "1.0"
  category: search

---

# Brave Search Skill

Search the web using the Brave Search API. Brave Search is a privacy-focused, independent search engine that does not track users or profile them.

## How It Works

This skill provides instructions and context. To execute searches, connect the **Brave Search** node to the agent's `input-tools` handle.

## brave_search Tool

Search the web and get relevant results from Brave's independent index.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | Yes | Search query to look up on the web |

### Node Parameters

Additional options configured on the node:

| Parameter | Default | Description |
|-----------|---------|-------------|
| maxResults | 10 | Number of results to return (max 100) |
| country | (empty) | Country code for regional results (e.g., US, GB, DE) |
| searchLang | (empty) | Language code for search results (e.g., en, es, fr) |
| safeSearch | moderate | Safe search filtering: off, moderate, strict |

### Response Format

```json
{
  "query": "artificial intelligence news",
  "results": [
    {
      "title": "AI Breakthrough in Medical Research",
      "snippet": "Researchers have developed a new AI model that can predict...",
      "url": "https://example.com/ai-medical-research"
    }
  ],
  "result_count": 10,
  "provider": "brave_search"
}
```

### Examples

**General search:**
```json
{
  "query": "latest news about artificial intelligence"
}
```

**Technical search:**
```json
{
  "query": "Python asyncio best practices 2026"
}
```

**Local search:**
```json
{
  "query": "best restaurants in San Francisco"
}
```

## When to Use Brave Search

- **Privacy-focused results** - No user tracking or profiling
- **Independent index** - Not reliant on Google or Bing
- **General web search** - News, facts, documentation, products
- **Regional results** - Use country/language parameters for localized results

## When NOT to Use Brave Search

- **AI-powered answers** - Use Perplexity instead for synthesized answers with citations
- **Google-specific results** - Use Serper for Google's index
- **Simple calculations** - Use the calculator tool
- **Creative tasks** - Writing, brainstorming, analysis

## Search Query Best Practices

- Use specific, focused queries (under 10 words when possible)
- Include relevant keywords, dates, and context
- Break complex questions into simpler searches
- Avoid filler words like "please" or "can you tell me"

## API Details

- **API**: GET `https://api.search.brave.com/res/v1/web/search`
- **Auth**: `X-Subscription-Token` header
- **Rate Limits**: Depends on plan (Free: 1 query/sec, 2000/month)
- **Pricing**: ~$0.003 per query

## Setup Requirements

1. Connect this skill to the agent's `input-skill` handle
2. Connect the **Brave Search** node to the agent's `input-tools` handle
3. Add your Brave Search API key in Credentials > Search
