---
name: perplexity-search-skill
description: Search the web using Perplexity Sonar AI for synthesized answers with citations, related questions, and optional images.
allowed-tools: perplexity_search
metadata:
  author: opencompany
  version: "1.0"
  category: search

---

# Perplexity Search Skill

Search the web using Perplexity's Sonar AI models. Unlike traditional search engines that return links, Perplexity provides synthesized AI-generated answers with inline citations and source URLs.

## How It Works

This skill provides instructions and context. To execute searches, connect the **Perplexity Search** node to the agent's `input-tools` handle.

## perplexity_search Tool

Ask a question and get an AI-synthesized answer with citations.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | Yes | Question or search query to get AI-powered answer with citations |

### Node Parameters

Additional options configured on the node:

| Parameter | Default | Description |
|-----------|---------|-------------|
| model | sonar | Model: sonar (fast), sonar-pro (deeper research) |
| searchRecencyFilter | (empty) | Filter results by recency: month, week, day, hour |
| returnImages | false | Include relevant images in response |
| returnRelatedQuestions | false | Include follow-up question suggestions |

### Response Format

```json
{
  "query": "What are the latest developments in quantum computing?",
  "answer": "Recent developments in quantum computing include several significant breakthroughs. **Google's Willow chip** demonstrated error correction below the threshold needed for reliable quantum computation [1]. **IBM** released its 1,121-qubit Condor processor [2], while **Microsoft** announced a new topological qubit approach [3].\n\nKey areas of progress:\n- Error correction advances\n- Increased qubit counts\n- New materials and architectures\n- Growing commercial applications",
  "citations": [
    "https://blog.google/technology/research/quantum-computing-willow/",
    "https://research.ibm.com/blog/condor-processor",
    "https://azure.microsoft.com/en-us/blog/quantum/"
  ],
  "results": [
    {"url": "https://blog.google/technology/research/quantum-computing-willow/"},
    {"url": "https://research.ibm.com/blog/condor-processor"},
    {"url": "https://azure.microsoft.com/en-us/blog/quantum/"}
  ],
  "model": "sonar",
  "provider": "perplexity",
  "images": [],
  "related_questions": [
    "What is quantum error correction?",
    "How many qubits does a useful quantum computer need?"
  ]
}
```

### Examples

**Research question:**
```json
{
  "query": "What are the latest developments in quantum computing?"
}
```

**Technical question:**
```json
{
  "query": "How does React Server Components work in Next.js 15?"
}
```

**Current events:**
```json
{
  "query": "What happened at the latest AI safety summit?"
}
```

**Comparison:**
```json
{
  "query": "Compare PostgreSQL vs MySQL for large-scale applications"
}
```

## When to Use Perplexity Search

- **Research questions** - Get synthesized, well-structured answers with citations
- **Technical explanations** - Detailed answers about how things work
- **Current events analysis** - AI-summarized coverage of recent events
- **Comparison queries** - Side-by-side analysis of options
- **Complex topics** - Multi-faceted questions that benefit from AI synthesis
- **When you need citations** - Every claim backed by source URLs

## When NOT to Use Perplexity Search

- **Simple factual lookups** - Use Brave or Serper for quick facts
- **Image search** - Use Serper with searchType "images"
- **Local business search** - Use Serper with searchType "places"
- **High-volume queries** - Perplexity costs more per query ($0.005)
- **Simple calculations** - Use the calculator tool

## Key Differences from Traditional Search

| Aspect | Traditional Search (Brave/Serper) | Perplexity Sonar |
|--------|----------------------------------|-----------------|
| Output | List of links with snippets | AI-written answer with inline citations |
| Format | title + snippet + URL | Full markdown answer with [1][2] references |
| Best for | Quick lookups, browsing | Research, explanations, synthesis |
| Speed | Faster | Slightly slower (AI generation) |
| Cost | $0.001-0.003/query | $0.005/query |

## Answer Format

The `answer` field contains rich markdown:
- **Bold text** for emphasis
- Numbered citations [1][2] referencing the `citations` array
- Bullet points and lists for structured information
- Headers for organized sections
- Code blocks for technical content

## API Details

- **API**: POST `https://api.perplexity.ai/chat/completions`
- **Auth**: `Authorization: Bearer` header
- **Models**: `sonar` (fast, default), `sonar-pro` (deeper research)
- **Timeout**: 60 seconds (AI generation takes longer than traditional search)
- **Pricing**: ~$0.005 per request

## Setup Requirements

1. Connect this skill to the agent's `input-skill` handle
2. Connect the **Perplexity Search** node to the agent's `input-tools` handle
3. Add your Perplexity API key in Credentials > Search
