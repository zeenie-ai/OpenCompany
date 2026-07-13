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

All actions use OAuth 2.0 user context with automatic token refresh on expiry (tokens last 2 hours).

## twitter_send Tool

Send tweets, replies, retweets, likes, and deletions.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action type: `tweet`, `reply`, `retweet`, `like`, `unlike`, `delete` |
| text | string | If tweet/reply | Tweet text content (max 280 characters) |
| tweet_id | string | If retweet/like/unlike/delete | Target tweet ID |
| reply_to_id | string | If reply | Tweet ID to reply to |

### Actions

| Action | Required Fields | Description | Rate Limit Notes |
|--------|-----------------|-------------|------------------|
| `tweet` | text | Post a new tweet | 200 tweets/15min (app), 300/3hr (user) |
| `reply` | text, reply_to_id | Reply to an existing tweet | Same as tweet limits |
| `retweet` | tweet_id | Retweet an existing tweet | 5 retweets/15min (free tier) |
| `like` | tweet_id | Like a tweet | 200 likes/24hr (free tier) |
| `unlike` | tweet_id | Remove like from a tweet | 50 unlikes/15min |
| `delete` | tweet_id | Delete your own tweet | 50 deletes/15min |

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
  "reply_to_id": "1234567890123456789"
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
  "reply_to_id": "<id_from_first_tweet>"
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

1. **Character limit**: Tweets are limited to 280 characters. The API will reject longer text.
2. **Tweet IDs**: Use the numeric ID string (e.g., `1234567890123456789`). Get these from search results or user timelines.
3. **Rate limits**: X API has strict rate limits (see table above). Space out rapid actions.
4. **Content policy**: Follow X's content policies and terms of service.
5. **Threading**: Use reply action with `reply_to_id` set to the previous tweet's ID to create threads.
6. **Token refresh**: If a 401/403 error occurs, the system automatically refreshes the OAuth token and retries.
7. **Retweet vs Quote**: This tool only supports native retweets. For quote tweets, use `tweet` action and include the tweet URL in the text.

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
