---
name: twitter-send-skill
description: Post tweets, reply to tweets, retweet, like/unlike, and delete tweets on Twitter/X. Supports threading, 280-char tweets, and automatic token refresh.
allowed-tools: twitter_send
metadata:
  author: opencompany
  version: "2.0"
  category: social

---

# Twitter Send Tool

Post and interact with tweets on Twitter/X via X API v2.

## How It Works

This skill provides instructions for the **Twitter Send** tool node. Connect the **Twitter Send** node to an AI Agent's `input-tools` handle to enable posting and interactions.

Actions use the connected OAuth user context and the plugin's token-refresh/retry path. Provider scopes and account limits still apply.

## twitter_send Tool

Send tweets, replies, retweets, likes, and deletions.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action type: `tweet`, `reply`, `quote`, `retweet`, `like`, `unlike`, `delete` |
| text | string | If tweet/reply/quote | Tweet text content (max 280 characters) |
| tweet_id | string | If reply/quote/retweet/like/unlike/delete | Target tweet ID |

### Actions

| Action | Required Fields | Description | Rate Limit Notes |
|--------|-----------------|-------------|------------------|
| `tweet` | text | Post a new tweet | Provider controlled |
| `reply` | text, tweet_id | Reply to an existing tweet | Provider controlled |
| `quote` | text, tweet_id | Quote an existing tweet | Provider controlled |
| `retweet` | tweet_id | Retweet an existing tweet | Provider controlled |
| `like` | tweet_id | Like a tweet | Provider controlled |
| `unlike` | tweet_id | Remove like from a tweet | Provider controlled |
| `delete` | tweet_id | Delete your own tweet | Provider controlled |

### Examples

**Post a tweet:**
```json
{
  "action": "tweet",
  "text": "Hello Twitter! This is my first automated tweet."
}
```

**Reply to a tweet:**
```json
{
  "action": "reply",
  "text": "Thanks for sharing this!",
  "tweet_id": "1234567890123456789"
}
```

**Create a thread (chain of replies):**
First post:
```json
{
  "action": "tweet",
  "text": "Thread: Here are 3 tips for better code (1/3)"
}
```
Then reply to the returned tweet ID for each subsequent post:
```json
{
  "action": "reply",
  "text": "Tip 1: Write tests first (2/3)",
  "tweet_id": "<id_from_first_tweet>"
}
```

**Retweet:**
```json
{
  "action": "retweet",
  "tweet_id": "1234567890123456789"
}
```

**Like a tweet:**
```json
{
  "action": "like",
  "tweet_id": "1234567890123456789"
}
```

**Unlike a tweet:**
```json
{
  "action": "unlike",
  "tweet_id": "1234567890123456789"
}
```

**Delete a tweet:**
```json
{
  "action": "delete",
  "tweet_id": "1234567890123456789"
}
```

### Response Format

**Tweet/Reply:**
```json
{
  "success": true,
  "result": {
    "id": "1234567890123456789",
    "text": "Hello Twitter!"
  },
  "execution_time": 0.45
}
```

**Retweet:**
```json
{
  "success": true,
  "result": {
    "retweeted": true
  },
  "execution_time": 0.3
}
```

**Like:**
```json
{
  "success": true,
  "result": {
    "liked": true
  },
  "execution_time": 0.25
}
```

**Delete:**
```json
{
  "success": true,
  "result": {
    "deleted": true
  },
  "execution_time": 0.3
}
```

### Error Response

```json
{
  "success": false,
  "error": "Tweet text is required",
  "execution_time": 0.01
}
```

## Guidelines

1. **Character limit**: This node clips tweet/reply/quote text to the first 280 characters before sending. Compose within that limit to avoid losing content.
2. **Tweet IDs**: Use the numeric ID string (e.g., `1234567890123456789`). Get these from search results or user timelines.
3. **Rate limits**: X API has strict rate limits (see table above). Space out rapid actions.
4. **Content policy**: Follow X's content policies and terms of service.
5. **Threading**: Use reply action with `tweet_id` set to the previous tweet's ID to create threads.
6. **Token refresh**: If a 401/403 error occurs, the system automatically refreshes the OAuth token and retries.
7. **Retweet vs Quote**: Use `retweet` with `tweet_id` for a native retweet. Use `quote` with `tweet_id` and `text` for a quote tweet.

## Common Use Cases

- Post automated updates and announcements
- Reply to mentions or specific tweets
- Like tweets matching certain criteria (found via twitter_search)
- Create tweet threads by chaining replies using returned IDs
- Engage with followers programmatically
- Delete tweets that match certain conditions

## Setup Requirements

1. Connect the **Twitter Send** node to an AI Agent's `input-tools` handle
2. Ensure Twitter is connected (authenticated via OAuth in Credentials Modal)
3. Your X Developer account must have appropriate API access level
4. Required OAuth scopes: `tweet.read`, `tweet.write`, `like.read`, `like.write`, `users.read`, `offline.access`
