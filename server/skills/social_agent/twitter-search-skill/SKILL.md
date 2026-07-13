---
name: twitter-search-skill
description: Search for recent tweets on Twitter/X using keywords, hashtags, mentions, and advanced query operators. Returns rich tweet data with expanded URLs, author info, media, metrics, and referenced tweets.
allowed-tools: twitter_search
metadata:
  author: opencompany
  version: "2.1"
  category: social

---

# Twitter Search Tool

Search for recent tweets on Twitter/X with rich data including full text, expanded URLs, author profiles, media attachments, engagement metrics, and referenced tweets.

## How It Works

This skill provides instructions for the **Twitter Search** tool node. Connect the **Twitter Search** node to an AI Agent's `input-tools` handle to enable tweet searching.

## twitter_search Tool

Search for tweets matching a query. Returns enriched tweet data via X API v2 expansions.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | Yes | Search query (supports operators) |
| max_results | integer | No | Number of results (10-100, default: 10) |

### Query Rules (X API constraint — required reading)

**Every query must contain at least one STANDALONE term.** Operator-only queries return HTTP 400 from X.

- **Standalone (can stand alone as the whole query)**: keyword, `"quoted phrase"`, `#hashtag`, `@mention`, `from:user`, `to:user`, `url:domain`, `context:`.
- **Conjunction-required (must be combined with a standalone term)**: `lang:`, `is:retweet` / `-is:retweet`, `is:reply` / `-is:reply`, `is:quote`, `has:links`, `has:media`, `has:images`, `has:videos`, plain `-negation` of any keyword.

**HTTP 400 — these queries fail:**

| Bad query | Why |
|---|---|
| `lang:en` | conjunction-required operator alone |
| `-is:retweet` | negation alone |
| `-is:retweet lang:en` | two conjunction-required operators, zero standalone |
| `has:media` | conjunction-required alone |
| `is:reply` | conjunction-required alone |

**Valid — these have at least one standalone term:**

| Good query | Standalone anchor |
|---|---|
| `python -is:retweet` | keyword `python` |
| `"machine learning" lang:en` | quoted phrase |
| `from:elonmusk -is:retweet` | `from:` |
| `#ai has:media` | hashtag |
| `@OpenAI lang:en` | mention |
| `url:"github.com" -is:retweet` | `url:` |

### Query Operators

The X API v2 supports advanced search operators:

| Operator | Example | Description |
|----------|---------|-------------|
| keyword | `python` | Tweets containing the word |
| phrase | `"machine learning"` | Exact phrase match |
| hashtag | `#AI` | Tweets with hashtag |
| mention | `@username` | Tweets mentioning user |
| from | `from:elonmusk` | Tweets by specific user |
| to | `to:username` | Replies to user |
| -keyword | `-spam` | Exclude keyword |
| OR | `python OR javascript` | Either term |
| lang | `lang:en` | Language filter |
| has:links | `AI has:links` | Tweets with URLs |
| has:media | `sunset has:media` | Tweets with media |
| has:images | `cat has:images` | Tweets with images |
| has:videos | `news has:videos` | Tweets with videos |
| is:retweet | `bitcoin is:retweet` | Only retweets |
| -is:retweet | `news -is:retweet` | Exclude retweets |
| is:reply | `@user is:reply` | Only replies |
| -is:reply | `topic -is:reply` | Exclude replies |
| is:quote | `breaking is:quote` | Only quote tweets |
| url: | `url:"github.com"` | Tweets linking to domain |
| context: | `context:131.1007360414114435072` | Tweets in topic context |

### Examples

**Simple keyword search:**
```json
{
  "query": "artificial intelligence",
  "max_results": 20
}
```

**Search with hashtag:**
```json
{
  "query": "#MachineLearning -is:retweet lang:en",
  "max_results": 50
}
```

**Search tweets from a user (original tweets only):**
```json
{
  "query": "from:OpenAI -is:retweet",
  "max_results": 10
}
```

**Complex query:**
```json
{
  "query": "AI (startup OR company) -is:retweet lang:en has:links",
  "max_results": 100
}
```

**Search for media posts:**
```json
{
  "query": "sunset has:media -is:retweet",
  "max_results": 25
}
```

### Response Format

Each tweet includes rich data. The `display_text` field has t.co links replaced with full expanded URLs. Long-form tweets (>280 chars) use `note_tweet` content.

```json
{
  "success": true,
  "result": {
    "tweets": [
      {
        "id": "1234567890123456789",
        "text": "Check out https://t.co/abc123",
        "display_text": "Check out https://www.example.com/full-article-url",
        "author_id": "987654321",
        "created_at": "2025-02-19T10:30:00+00:00",
        "lang": "en",
        "source": "Twitter Web App",
        "conversation_id": "1234567890123456789",
        "in_reply_to_user_id": null,
        "possibly_sensitive": false,
        "public_metrics": {
          "retweet_count": 42,
          "reply_count": 12,
          "like_count": 256,
          "quote_count": 5,
          "bookmark_count": 18,
          "impression_count": 15000
        },
        "author": {
          "id": "987654321",
          "username": "techuser",
          "name": "Tech User",
          "profile_image_url": "https://pbs.twimg.com/..."
        },
        "urls": [
          {
            "url": "https://t.co/abc123",
            "expanded_url": "https://www.example.com/full-article-url",
            "display_url": "example.com/full-article-..."
          }
        ],
        "media": [
          {
            "media_key": "3_123456789",
            "type": "photo",
            "url": "https://pbs.twimg.com/media/...",
            "alt_text": "Description of the image"
          }
        ],
        "referenced_tweets": [
          {
            "type": "quoted",
            "id": "1111111111111111111",
            "text": "Original tweet text that was quoted",
            "author_id": "222222222"
          }
        ]
      }
    ],
    "count": 20,
    "query": "#AI"
  },
  "execution_time": 0.82
}
```

### Key Response Fields

| Field | Description |
|-------|-------------|
| `text` | Raw tweet text (may contain t.co shortened links) |
| `display_text` | Text with t.co links expanded to full URLs -- **use this for display** |
| `author` | Author profile (username, name, profile image) from includes expansion |
| `public_metrics` | Engagement: retweet_count, reply_count, like_count, quote_count, bookmark_count, impression_count |
| `media` | Attached media objects (photo, video, animated_gif) with URLs and alt text |
| `urls` | Expanded URL mappings (short t.co -> full URL) |
| `referenced_tweets` | Quoted or replied-to tweets with their text content |
| `lang` | Detected language code (e.g., "en", "es", "ja") |
| `source` | Client used to post (e.g., "Twitter Web App", "Twitter for iPhone") |
| `conversation_id` | ID of the conversation thread |
| `in_reply_to_user_id` | If a reply, the user ID being replied to |

### Important Notes

- **Always use `display_text`** instead of `text` when showing tweet content to users -- it contains expanded URLs
- **Long tweets**: Tweets over 280 characters use note_tweet; the full text is in the `text` field
- **Media URLs**: `media[].url` gives the direct image/video URL
- **Referenced tweets**: `referenced_tweets[].text` contains the full text of quoted/replied tweets
- **Metrics**: `public_metrics` shows real-time engagement counts

### Error Response

```json
{
  "success": false,
  "error": "Search query is required",
  "execution_time": 0.01
}
```

## Guidelines

1. **Always include a standalone term** (keyword / phrase / hashtag / mention / `from:` / `to:` / `url:`) — see "Query Rules" above. Operator-only queries fail with HTTP 400.
2. **Max results**: Minimum 10, maximum 100 per request (X API v2 constraint).
3. **Recent tweets only**: X API v2 free/basic tier searches recent tweets (last 7 days).
4. **Rate limits**: Be mindful of API rate limits when searching repeatedly.
5. **Combine operators on a base term**: e.g., `python -is:retweet lang:en has:links` — the `python` keyword anchors all three filters.
6. **Exclude retweets**: append `-is:retweet` to a keyword query (e.g. `python -is:retweet`). Cannot stand alone.
7. **Language filter**: append `lang:en` to a keyword/`from:`/hashtag query (e.g. `"openai" lang:en`). Cannot stand alone.
8. **Media filter**: append `has:media` / `has:images` / `has:videos` to a keyword query. Cannot stand alone.

## Common Use Cases

- Monitor brand mentions with engagement metrics
- Track trending topics and measure reach
- Find tweets about specific subjects with media
- Research competitor activity and engagement
- Gather content for curation with full context
- Find influencers by analyzing follower/engagement metrics
- Analyze conversation threads via conversation_id

## Setup Requirements

1. Connect the **Twitter Search** node to an AI Agent's `input-tools` handle
2. Ensure Twitter is connected (authenticated via OAuth in Credentials Modal)
3. Your X Developer account must have appropriate API access level

## References

- [X API — Build a query](https://docs.x.com/x-api/posts/search/integrate/build-a-query)
- [X API — Operators reference](https://docs.x.com/x-api/posts/search/integrate/operators)
- [X API — /2/tweets/search/recent](https://docs.x.com/x-api/posts/search-recent-posts)
