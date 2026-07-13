---
name: twitter-user-skill
description: Look up Twitter/X user profiles with descriptions, get authenticated user info, and retrieve followers/following lists with profile details.
allowed-tools: twitter_user
metadata:
  author: opencompany
  version: "2.0"
  category: social

---

# Twitter User Tool

Look up user profiles and social connections on Twitter/X via X API v2.

## How It Works

This skill provides instructions for the **Twitter User** tool node. Connect the **Twitter User** node to an AI Agent's `input-tools` handle to enable user lookups.

All operations use OAuth 2.0 user context with automatic token refresh on expiry.

## twitter_user Tool

Retrieve user information and social connections.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Operation type: `me`, `by_username`, `by_id`, `followers`, `following` |
| username | string | If by_username | Twitter username (without @) |
| user_id | string | If by_id/followers/following | Twitter user ID |
| max_results | integer | No | For followers/following (1-1000, default: 100) |

### Operations

| Operation | Required Fields | Description | Rate Limit (free tier) |
|-----------|-----------------|-------------|------------------------|
| `me` | none | Get authenticated user's profile | 25 req/24hr |
| `by_username` | username | Look up user by username | 100 req/24hr |
| `by_id` | user_id | Look up user by ID | 100 req/24hr |
| `followers` | user_id (optional) | Get user's followers (defaults to authenticated user) | 15 req/15min |
| `following` | user_id (optional) | Get accounts user follows (defaults to authenticated user) | 15 req/15min |

### Examples

**Get my profile:**
```json
{
  "operation": "me"
}
```

**Look up user by username:**
```json
{
  "operation": "by_username",
  "username": "elonmusk"
}
```

**Look up user by ID:**
```json
{
  "operation": "by_id",
  "user_id": "44196397"
}
```

**Get my followers:**
```json
{
  "operation": "followers",
  "max_results": 100
}
```

**Get accounts I follow:**
```json
{
  "operation": "following",
  "max_results": 50
}
```

**Get another user's followers:**
```json
{
  "operation": "followers",
  "user_id": "44196397",
  "max_results": 200
}
```

### Response Format - Single User

All user lookups return profile data including description and account creation date:

```json
{
  "success": true,
  "result": {
    "id": "44196397",
    "username": "elonmusk",
    "name": "Elon Musk",
    "profile_image_url": "https://pbs.twimg.com/profile_images/.../photo.jpg",
    "verified": true,
    "description": "Mars & Cars, Chips & Dips",
    "created_at": "2009-06-02 20:12:29+00:00"
  },
  "execution_time": 0.35
}
```

### Response Format - User List (Followers/Following)

```json
{
  "success": true,
  "result": {
    "users": [
      {
        "id": "123456789",
        "username": "user1",
        "name": "User One",
        "profile_image_url": "https://pbs.twimg.com/...",
        "verified": false,
        "description": "Software developer and open source enthusiast",
        "created_at": "2020-03-15 08:30:00+00:00"
      }
    ],
    "count": 100
  },
  "execution_time": 1.2
}
```

### Key Response Fields

| Field | Description |
|-------|-------------|
| `id` | Unique numeric user ID (use for followers/following lookups) |
| `username` | Handle without @ (use for by_username lookups) |
| `name` | Display name |
| `profile_image_url` | URL to profile picture |
| `verified` | Whether the account is verified |
| `description` | User's bio text |
| `created_at` | Account creation timestamp |

### Error Response

```json
{
  "success": false,
  "error": "Username is required",
  "execution_time": 0.01
}
```

## Guidelines

1. **Usernames**: Provide without the @ symbol (e.g., `elonmusk` not `@elonmusk`)
2. **User IDs**: Use the numeric ID string. Get these from search results (`author_id`) or user lookups
3. **Rate limits**: `me` endpoint is heavily rate-limited (25/24hr on free tier) -- avoid calling repeatedly
4. **Max results**: Minimum 1, maximum 1000 per request for followers/following
5. **Pagination**: Only the first page is returned. For large accounts, max_results controls page size
6. **Defaults to self**: If `user_id` is omitted for followers/following, it defaults to the authenticated user
7. **Token refresh**: If a 401/403 error occurs, the system automatically refreshes the OAuth token and retries

## Common Use Cases

- Get your own profile information and bio
- Look up profiles of users you interact with
- Check if users are verified
- Analyze follower/following relationships and growth
- Build lists of relevant accounts in a niche
- Get user IDs from usernames for use in search queries (e.g., `from:username`)
- Compare follower counts between accounts

## Setup Requirements

1. Connect the **Twitter User** node to an AI Agent's `input-tools` handle
2. Ensure Twitter is connected (authenticated via OAuth in Credentials Modal)
3. Your X Developer account must have appropriate API access level
4. Required OAuth scopes: `users.read`, `follows.read`, `offline.access`
