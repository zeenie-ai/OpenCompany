---
name: serper-search-skill
description: Search the web using Serper API for Google-powered search results including web, news, images, and places.
allowed-tools: serper_search
metadata:
  author: opencompany
  version: "1.0"
  category: search

---

# Serper Search Skill

Search the web using the Serper API, which provides Google Search results via a simple API. Supports web search, news, images, and places.

## How It Works

This skill provides instructions and context. To execute searches, connect the **Serper Search** node to the agent's `input-tools` handle.

## serper_search Tool

Search Google via Serper API and get structured results.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | Yes | Search query to look up on Google |

### Node Parameters

Additional options configured on the node:

| Parameter | Default | Description |
|-----------|---------|-------------|
| maxResults | 10 | Number of results to return (max 100) |
| searchType | search | Type: search (web), news, images, places |
| country | (empty) | Country code for regional results (gl parameter) |
| language | (empty) | Language code for results (hl parameter) |

### Response Format

**Web search:**
```json
{
  "query": "artificial intelligence news",
  "results": [
    {
      "title": "AI Breakthrough in Medical Research",
      "snippet": "Researchers have developed a new AI model...",
      "url": "https://example.com/ai-research",
      "position": 1
    }
  ],
  "result_count": 10,
  "search_type": "search",
  "provider": "serper",
  "knowledge_graph": {
    "title": "Artificial Intelligence",
    "description": "..."
  }
}
```

**News search:**
```json
{
  "query": "technology news today",
  "results": [
    {
      "title": "Tech Industry Update",
      "snippet": "Major developments in...",
      "url": "https://example.com/news",
      "date": "2 hours ago",
      "source": "TechCrunch"
    }
  ],
  "search_type": "news",
  "provider": "serper"
}
```

**Images search:**
```json
{
  "query": "aurora borealis photos",
  "results": [
    {
      "title": "Northern Lights Over Iceland",
      "imageUrl": "https://example.com/image.jpg",
      "url": "https://example.com/gallery"
    }
  ],
  "search_type": "images",
  "provider": "serper"
}
```

**Places search:**
```json
{
  "query": "coffee shops near me",
  "results": [
    {
      "title": "Blue Bottle Coffee",
      "address": "123 Main St, San Francisco, CA",
      "rating": 4.5,
      "url": "https://bluebottlecoffee.com"
    }
  ],
  "search_type": "places",
  "provider": "serper"
}
```

### Examples

**Web search:**
```json
{
  "query": "Python 3.13 new features"
}
```

**News search** (set searchType to "news" on node):
```json
{
  "query": "stock market today"
}
```

**Places search** (set searchType to "places" on node):
```json
{
  "query": "Italian restaurants in Manhattan"
}
```

## When to Use Serper Search

- **Google-quality results** - Powered by Google's search index
- **News search** - Set searchType to "news" for current headlines with sources and dates
- **Image search** - Set searchType to "images" for visual content
- **Places/Local search** - Set searchType to "places" for business listings with ratings
- **Knowledge Graph** - Includes Google Knowledge Graph data when available

## When NOT to Use Serper Search

- **AI-powered answers** - Use Perplexity instead for synthesized answers
- **Privacy-focused search** - Use Brave Search instead
- **Simple calculations** - Use the calculator tool
- **Creative tasks** - Writing, brainstorming, analysis

## Search Query Best Practices

- Use specific, focused queries (under 10 words when possible)
- Include relevant keywords, dates, and context
- Use Google search operators: `site:`, `filetype:`, `intitle:`, `-exclude`
- Break complex questions into simpler searches

## API Details

- **API**: POST `https://google.serper.dev/search` (also /news, /images, /places)
- **Auth**: `X-API-KEY` header
- **Pricing**: ~$0.001 per query (one of the cheapest Google SERP APIs)

## Setup Requirements

1. Connect this skill to the agent's `input-skill` handle
2. Connect the **Serper Search** node to the agent's `input-tools` handle
3. Add your Serper API key in Credentials > Scrapers
