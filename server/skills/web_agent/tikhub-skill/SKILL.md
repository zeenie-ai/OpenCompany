---
name: tikhub-skill
description: Scrape TikTok, Douyin, Instagram, YouTube, Twitter/X, Xiaohongshu, Bilibili, Kuaishou, Weibo, Reddit, Threads, LinkedIn and more through the TikHub pay-per-request API - discover endpoints, read their documentation, call them by id, parse a TikTok or Douyin share URL, and check account balance.
allowed-tools: "tikhub_action"
metadata:
  author: opencompany
  version: "2.0"
  category: web

---

# TikHub Skill

TikHub (api.tikhub.io) is a pay-per-request REST API with roughly 1,000
scraping endpoints across TikTok, Douyin, Instagram, YouTube, Twitter/X,
Xiaohongshu, Bilibili, Kuaishou, Weibo, Reddit, Threads, LinkedIn, Zhihu,
Lemon8 and others. Each endpoint is one HTTP call that returns the
platform's raw data; there are no long-running jobs and nothing to poll.
The `tikhub_action` tool addresses every endpoint by its
resource-dot-method id (for example `instagram_v2.search_reels`) and passes
your `params` through as keyword arguments.

Two facts shape how you use it:

- **Raw results are huge.** One page of 12 Instagram reels is about
  310,000 characters, and a question about views and dates needs a few
  hundred of them. So a large result is saved to a file in your workspace
  and you get a preview; you then read only the fields you need from the
  file (see "Large results").
- **Every successful call is billed.** Find the right endpoint and
  parameters before calling, and never repeat a call to see data you
  already have on file.

## Tool: tikhub_action

| Operation | Purpose | Key fields | Billed |
|---|---|---|---|
| `list_endpoints` | Find endpoint ids | `platform` (default `all`), `search` (substring over id / summary / path), `limit` | no |
| `describe` | TikHub's documentation for one endpoint: what it returns, what each parameter means and accepts, its price | `endpoint` | no |
| `call` | Invoke one endpoint | `endpoint`, `params` (JSON object of keyword arguments) | yes |
| `fetch_url` | Parse a TikTok or Douyin share URL | `url`, `minimal` | yes |
| `account` | Balance and today's usage for the stored key | - | no |

## Find, describe, call

Never guess an endpoint id, a parameter name or a parameter value.

1. **Find candidates.** Search by what you want, not by a guess at the
   name:

   ```json
   { "operation": "list_endpoints", "platform": "instagram", "search": "reels" }
   ```

   Each entry has `endpoint`, `http_method`, `path`, a one-line `summary`
   and `params` (`name`, `type`, `required`).

2. **Describe the one you intend to call**, unless you already described
   it in this conversation:

   ```json
   { "operation": "describe", "endpoint": "instagram_v2.fetch_hashtag_posts" }
   ```

   `description` is TikHub's own documentation: the purpose, what the
   endpoint returns, notes, an example and, where TikHub states it, the
   price. Each entry of `params` gains TikHub's `description` and
   `default`, which is where allowed values are stated (for this
   endpoint: `keyword` is the hashtag without `#`, `feed_type` is `top`,
   `recent` or `reels`). The one-line summary is not enough to choose by:
   `instagram_v3.general_search` reads like a content search and returns
   users, hashtags and places.

   A `note` instead of a `description` means either TikHub no longer
   publishes the route (pick another endpoint) or the documentation could
   not be loaded just now (the parameter names and types are still right).

3. **Call it** with exactly the documented parameter names:

   ```json
   { "operation": "call", "endpoint": "instagram_v2.search_reels", "params": { "keyword": "street food" } }
   ```

`endpoint` also accepts the REST path (`/api/v1/instagram/v2/search_reels`).
An unknown id or an unknown or missing parameter is refused before any
request is sent, and the error lists the closest ids or the accepted
parameters.

A few POST endpoints take one JSON body instead of keyword arguments (the
signature shows a single `body` parameter). Pass it as `body` inside
`params`:

```json
{ "operation": "call", "endpoint": "douyin_web.fetch_multi_video", "params": { "body": ["7372484719365098803", "7372484719365098804"] } }
```

## Results

A result of up to 20,000 characters comes back whole:

```json
{
  "operation": "call",
  "endpoint": "douyin_web.handler_user_profile",
  "path": "/api/v1/douyin/web/handler_user_profile",
  "data": { "user": { "nickname": "...", "follower_count": 12345 } },
  "code": 200,
  "router": "/api/v1/douyin/web/handler_user_profile",
  "cost_usd": 0.001
}
```

### Large results

Anything larger (most lists, and most full post or video objects) is saved
to your workspace as JSON, and the result carries `file`, `preview` and
`note` instead of `data`:

```json
{
  "operation": "call",
  "endpoint": "instagram_v2.search_reels",
  "file": { "kind": "file", "path": "tikhub/instagram-v2-search-reels-7_tikhub-3f9a1c.json", "size_bytes": 429857, "mime_type": "application/json" },
  "preview": {
    "data": {
      "count": 12,
      "items": [
        { "code": "C8exampleAb", "taken_at": 1794300000, "play_count": 245000, "like_count": 9800, "comment_count": 54,
          "user": { "username": "example_creator", "is_verified": true, "full_name": "..." },
          "caption": { "text": "The first 80 characters of the caption..." } },
        "... 11 more"
      ]
    },
    "pagination_token": "..."
  },
  "note": "The whole result (308,415 characters) is saved in the workspace at tikhub/...json. ...",
  "code": 200,
  "cost_usd": 0.001
}
```

`preview` is the shape of the saved data with real values (abridged above;
the real one lists every key): each list is cut to its first element
followed by `"... N more"`, empty values are left out and strings are
clipped at 80 characters. **It is not the result.** The other elements are
in the file, and paths in the preview are the paths in the file.

Read the fields you need from `file.path` with one `shell_execute` call.
Commands run in the workspace, so the path works as it is. The form that
works in every shell is one `python` command:

```
python -c "import json; d=json.load(open('tikhub/instagram-v2-search-reels-7_tikhub-3f9a1c.json', encoding='utf-8')); print(json.dumps([{'code': i.get('code'), 'taken_at': i.get('taken_at'), 'plays': i.get('play_count'), 'likes': i.get('like_count'), 'user': (i.get('user') or {}).get('username'), 'verified': (i.get('user') or {}).get('is_verified')} for i in d['data']['items']]))"
```

If `python` is not found, Nushell does the same (it is the shell wherever
it is installed), and `where` filters before the projection:

```nu
open tikhub/instagram-v2-search-reels-7_tikhub-3f9a1c.json | get data.items | where play_count > 100000 | each {|it| {code: $it.code?, taken_at: $it.taken_at?, plays: $it.play_count?, user: $it.user?.username?}} | to json -r
```

Both return every item with only the chosen fields, about 100 characters
an item. Take the field names and the list's path from `preview`, and keep
a list comprehension or `each` over the whole list so no item is skipped.

Without a shell tool:

- `fs_search` in `grep` mode finds one field at a time. It matches a plain
  substring per line and the file has one key per line, so
  `{ "mode": "grep", "path": "<file.path>", "pattern": "\"play_count\":" }`
  returns every value in file order. Keys that also occur in nested
  objects match too.
- `file_read` with `offset` and `limit` pages through the file by line,
  which suits reading one item in full.
- `sandboxed_python` with the `workspace_read` capability reads the file at
  `/workspace/<file.path>`.

If you have none of these, answer from `preview`, say that it covers only
the first item of each list, and ask the user to connect a shell or file
tool to this agent.

Rules that follow from the file:

- Do not call an endpoint again to see more of a result you already have
  on file. That is a second charge for the same data.
- Do not print the whole file into the conversation. Select fields.
- Convert timestamps before judging recency: `taken_at`, `create_time` and
  similar fields are Unix seconds.
- Pagination cursors are in the file too, at the path the preview shows.

## Share URLs: fetch_url

For a TikTok or Douyin share link (including short links on v.douyin.com
and vm.tiktok.com) use `fetch_url`. It resolves the link and returns the
video in one request, with no id to extract first:

```json
{ "operation": "fetch_url", "url": "https://www.tiktok.com/@scout2015/video/7372484719365098803", "minimal": true }
```

`minimal: true` trims the payload to the fields most tasks need (id,
description, author, statistics, media URLs). Other platforms have their
own URL-accepting endpoints: `instagram_v2.fetch_post_info` takes
`code_or_url`,
`kuaishou_web.fetch_one_video_by_url` takes `url`,
`youtube_web_v2.get_video_info_v2` takes `video_url`.

## Per-platform starting points

Ids below exist in the installed SDK and on TikHub's live API. Parameters
are the required ones; `describe` is the authority for the rest. A
platform's families are not versions of one another: a higher number is
not a superset and may cost more, so choose by what `describe` says the
endpoint returns.

### Instagram

Three families with three prices: `instagram_v1` ($0.001 a call),
`instagram_v2` ($0.002), `instagram_v3` ($0.008). Use `instagram_v2` first;
it covers search, users, posts, reels, hashtags, locations and music.

| Endpoint | Required params | Notes |
|---|---|---|
| `instagram_v2.search_reels` | `keyword` | Reels by keyword; paginate with `pagination_token`. A long phrase can come back empty: try two or three words |
| `instagram_v2.fetch_hashtag_posts` | `keyword` (hashtag without `#`) | `feed_type`: `top`, `recent` or `reels`; `pagination_token` |
| `instagram_v2.fetch_user_info` | `username` or `user_id` | Profile |
| `instagram_v2.fetch_user_reels` | `username` or `user_id` | Paginate with `pagination_token` |
| `instagram_v2.fetch_user_posts` | `username` or `user_id` | Paginate with `pagination_token` |
| `instagram_v2.fetch_post_info` | `code_or_url` | One post or reel from its shortcode or URL |
| `instagram_v2.fetch_post_comments` | `code_or_url` | `sort_by`; `pagination_token` |
| `instagram_v2.general_search` | `keyword` | Blended results: users, posts, reels |
| `instagram_v3.general_search` | `query` | Users, hashtags and places, not posts; `next_max_id` + `rank_token` |

A reel or post item carries `code` (the shortcode: the link is
`https://www.instagram.com/reel/<code>/`), `taken_at`, `play_count`,
`like_count`, `comment_count`, `user.username`, `user.is_verified` and
`caption.text`.

### TikTok

`tiktok_web` uses camelCase parameters (`secUid`, `uniqueId`, `itemId`) and
`tiktok_app_v3` snake_case ones (`sec_user_id`, `unique_id`, `aweme_id`).

| Endpoint | Required params | Notes |
|---|---|---|
| `tiktok_app_v3.fetch_video_search_result` | `keyword` | `offset`, `count`, `sort_type`, `publish_time`, `region` |
| `tiktok_app_v3.fetch_one_video` | `aweme_id` | Single video |
| `tiktok_app_v3.fetch_one_video_by_share_url` | `share_url` | Video from a share link (or `fetch_url`) |
| `tiktok_app_v3.fetch_user_post_videos` | `sec_user_id` or `unique_id` | Paginate with `max_cursor`; `count` |
| `tiktok_app_v3.handler_user_profile` | one of `user_id` / `sec_user_id` / `unique_id` | Profile |
| `tiktok_app_v3.fetch_hashtag_video_list` | `ch_id` | Get `ch_id` from `tiktok_app_v3.fetch_hashtag_search_result` |
| `tiktok_web.fetch_user_profile` | `uniqueId` or `secUid` | Profile by @handle |
| `tiktok_web.fetch_user_post` | `secUid` | Paginate with `cursor`; `count` |
| `tiktok_web.fetch_post_detail` | `itemId` | Single video (web fields) |
| `tiktok_web.fetch_search_video` | `keyword` | Paginate with `offset` + `search_id` |
| `tiktok_web.fetch_post_comment` | `aweme_id` | Paginate with `cursor`; `count` |

### Douyin

| Endpoint | Required params | Notes |
|---|---|---|
| `douyin_web.fetch_one_video` | `aweme_id` | Single video |
| `douyin_web.fetch_one_video_by_share_url` | `share_url` | Video from a share link (or `fetch_url`) |
| `douyin_web.handler_user_profile` | `sec_user_id` | Profile; `douyin_web.handler_user_profile_v2` takes `unique_id` |
| `douyin_web.fetch_user_post_videos` | `sec_user_id` | Paginate with `max_cursor`; `count` |
| `douyin_web.fetch_video_comments` | `aweme_id` | Paginate with `cursor`; `count` |
| `douyin_web.fetch_hot_search_result` | - | Hot search board |
| `douyin_search.fetch_video_search_v2` | `keyword` | POST with plain keyword params; `cursor` + `search_id`, `sort_type`, `publish_time` |
| `douyin_app_v3.fetch_one_video_v3` | `aweme_id` | Video without copyright restrictions on media URLs |

### YouTube

| Endpoint | Required params | Notes |
|---|---|---|
| `youtube_web_v2.get_general_search_v2` | `keyword` | `upload_date`, `type`, `duration`, `sort_by`; `continuation_token` |
| `youtube_web_v2.get_shorts_search_v2` | `keyword` | Shorts only; `upload_date`, `sort_by` |
| `youtube_web.search_video` | `search_query` | `order_by`, `language_code`, `country_code` |
| `youtube_web_v2.get_video_info` | `video_id` | Video metadata; `youtube_web_v2.get_video_info_v2` also takes `video_url` |
| `youtube_web_v2.get_video_comments` | `video_id` | `sort_by`; `continuation_token` |
| `youtube_web_v2.get_video_captions` | `video_id` or `video_url` | Subtitle text; `language_code`, `format` ($0.008) |
| `youtube_web.get_channel_id` | `channel_name` | Resolve an @handle to a channel id |
| `youtube_web.get_channel_info` | `channel_id` | Channel metadata |
| `youtube_web_v2.get_channel_videos` | `channel_id` | Paginate with `continuation_token` |

### Twitter / X

| Endpoint | Required params | Notes |
|---|---|---|
| `twitter_web.fetch_search_timeline` | `keyword` | `search_type` (Top / Latest / Media / People / Lists); `cursor` |
| `twitter_web.fetch_tweet_detail` | `tweet_id` | Single tweet |
| `twitter_web.fetch_user_profile` | `screen_name` or `rest_id` | Profile |
| `twitter_web.fetch_user_post_tweet` | `screen_name` or `rest_id` | User timeline; `cursor` |
| `twitter_web.fetch_post_comments` | `tweet_id` | Replies; `cursor` |
| `twitter_web.fetch_trending` | - | `country` optional |

### Xiaohongshu (RedNote)

Use `xiaohongshu_app_v2`. Most of its endpoints accept either an id or the
raw `share_text` copied from the app.

| Endpoint | Required params | Notes |
|---|---|---|
| `xiaohongshu_app_v2.search_notes` | `keyword` | `page`, `sort_type`, `note_type`, `time_filter` |
| `xiaohongshu_app_v2.get_image_note_detail` | `note_id` or `share_text` | Image note |
| `xiaohongshu_app_v2.get_video_note_detail` | `note_id` or `share_text` | Video note |
| `xiaohongshu_app_v2.get_note_comments` | `note_id` or `share_text` | `cursor`; `sort_strategy` |
| `xiaohongshu_app_v2.get_user_info` | `user_id` or `share_text` | Profile |
| `xiaohongshu_app_v2.get_user_posted_notes` | `user_id` or `share_text` | Paginate with `cursor` |

### Other platforms

Every other family follows the same shape; list it by platform. Starting
points: `bilibili_web.fetch_one_video` (`bv_id`),
`kuaishou_web.fetch_one_video_by_url` (`url`),
`weibo_web_v2.fetch_post_detail` (`id`), `reddit_app.fetch_post_details`
(`post_id`), `threads_web.fetch_post_detail` (`post_id`),
`linkedin_web_v2.get_user_profile` (`url`),
`zhihu_web.fetch_question_answers` (`question_id`).

### Hybrid parsing and account

| Endpoint | Required params | Notes |
|---|---|---|
| `hybrid_parsing.video_data` | `url` | What `fetch_url` calls; TikTok and Douyin only |
| `tikhub_user.get_user_info` | - | Balance, free credit, email (what `account` calls) |
| `tikhub_user.get_user_daily_usage` | - | Today's request count and spend (what `account` calls) |
| `tikhub_user.get_endpoint_info` | `endpoint` (a `/api/v1/...` path) | TikHub's record for one endpoint |
| `tikhub_user.calculate_price` | `endpoint` | Projected cost; `request_per_day` |

## Pagination

One call returns one page. `describe` names the cursor parameter and where
the next value is returned; read that value from `data`, or from the saved
file when the page was large, and pass it back unchanged with the same
other parameters.

| Family | Request param | Usually stop when |
|---|---|---|
| Instagram v2 | `pagination_token` | token absent |
| Instagram v1 / v3 | `end_cursor`, `max_id`, `after`, `next_max_id` (per endpoint) | no next cursor, or `has_more` false |
| TikTok app / Douyin | `max_cursor` (user posts), `cursor` (comments, hashtags), `offset` (search) | `has_more` is `false` or `0` |
| TikTok web | `cursor`, or `offset` + `search_id` for search | `hasMore` false |
| YouTube | `continuation_token` | token absent |
| Twitter | `cursor` | cursor repeats or no new entries |
| Xiaohongshu app v2 | `cursor`, or `page` for search | `has_more` false |
| Kuaishou | `pcursor` | `pcursor` is `"no_more"` |

Fetch pages one after another, tell the user how many pages you fetched,
and stop at a page count the task justifies. Each page is one charge.

## Cost and rate limits

- TikHub prices each endpoint: most cost $0.001 a successful call, some
  $0.002 to $0.008 (all of `instagram_v3`), a few more. `describe` shows
  the price where TikHub documents one. Requests that fail are not billed.
- `cost_usd` on a result is OpenCompany's flat estimate ($0.001), not
  TikHub's price for that endpoint. `account` reports the real balance.
- `list_endpoints` and `describe` never spend anything.
- 10 requests per second per endpoint. Past that TikHub answers 429; the
  tool retries with backoff and reports `retry_after` when TikHub sends it.
- A request can take up to a minute on a slow platform. Do not send the
  same call again while waiting.
- Before a crawl of hundreds of pages, run `account` and tell the user the
  balance.

## Errors

The tool raises a plain message that carries TikHub's own words. Relay it
to the user and act on the kind of failure:

| Message contains | Meaning | What to do |
|---|---|---|
| `rejected the API key (401)` | Key missing, revoked or wrong | Tell the user to update it in Credentials -> TikHub; do not retry |
| `refused <endpoint> (402)` | The account balance is too low | Run `account`, tell the user the balance and to top up at user.tikhub.io; do not retry |
| `refused <endpoint> (403)` | The key lacks permission for this endpoint, or the account is disabled or unverified | Tell the user to enable the endpoint for the key at user.tikhub.io; do not retry |
| `could not complete <endpoint> (400)` | Either a wrong parameter value or a failure on TikHub's or the platform's side; TikHub sends the same message for both | Check the values against `describe`. If they match the documentation, retry once, then use another endpoint for the same data. Do not keep changing correct parameters |
| `rejected the parameters of <endpoint> (422)` | A parameter is missing or has the wrong type | The message names the parameter; fix it and retry |
| `returned 404 for <endpoint>` | The item does not exist, or TikHub retired the route | `describe` the endpoint: a note says when the route is no longer published |
| `rate-limited <endpoint> (429)` | More than 10 requests a second on one endpoint | Wait `retry_after` seconds (or a few), then retry once |
| `upstream error while calling <endpoint>` | Platform-side failure after the tool's retries | Try later, or another endpoint for the same data |
| `Unknown TikHub endpoint` | No such id | Use the suggestions in the message or `list_endpoints` |
| `rejected params` | Parameter name not accepted | Use the accepted names listed in the message |
| `No workspace is available` | A large result could not be saved | Tell the user to save the workflow and run it again |

A 200 whose body says `code: 4xx` is raised as an error with TikHub's
message: the platform returned nothing useful for that id.

An empty result (an empty list, or a count of 0) is an answer, not an
error. For a search, try a shorter or differently worded query rather than
the same one again.

## Authentication

OpenCompany stores the TikHub API key encrypted. You never see it, it is
not in the environment or in any file, and you must never look for it or
ask the user to paste it into the chat. If a call fails with the 401
message, tell the user to open **Credentials -> Scrapers -> TikHub**,
paste the key from the TikHub user portal (user.tikhub.io -> API Keys) and
click Validate. The modal shows the account email and balance when the key
is accepted.

## Working inside a team

When this node is wired to a teammate rather than to the lead, the lead
never sees the `tikhub_action` tool. It sees one roster line per teammate
listing that teammate's tools and skills, and assigns work through
`task_manager` with a bounded mission. As the teammate holding TikHub:

1. Treat the mission as the query: pick the platform from the URL or the
   wording, find and describe the endpoint, then `call` or `fetch_url`.
2. Return the fields the mission asked for, never a raw payload or a file
   path alone. Include the endpoint id used, the number of items and the
   number of calls made, so the lead can report spend.
3. If the key is missing or the balance is too low, say so in the result
   so the lead can tell the user; do not retry.

## Best practices

1. **Describe before the first call to an endpoint.** It is free, and the
   one-line summary does not say what the endpoint returns or which values
   a parameter accepts.
2. **Read fields from the saved file.** `preview` shows one item; the file
   has them all. One command returns the fields you need for every item.
3. **Never repeat a paid call for data you have.** Not to see more of a
   saved result, and not because an earlier result scrolled out of view:
   the file is still in the workspace.
4. **Prefer the cheaper family** when two return what you need.
5. **Use `fetch_url` for TikTok and Douyin share links.**
6. **Paginate deliberately** and say how many pages you fetched.
7. **Report what you verified.** State the endpoint, the query and the
   date range the numbers come from, and that counts change over time.
8. **Scrape public content only**, collect no personal data beyond the
   task, and stay under 10 requests a second per endpoint.
