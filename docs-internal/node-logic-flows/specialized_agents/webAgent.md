# Web Control Agent (`web_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/web_agent/__init__.py`](../../../server/nodes/agent/web_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.pink` |
| **Icon** | globe (U+1F310) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for web automation. Typical tool connections:
`httpRequest`, `browser`, `crawleeScraper`, `apifyActor`, `proxyRequest`,
and any of the search nodes (`braveSearch`, `serperSearch`,
`perplexitySearch`, `duckduckgoSearch`).

## What is unique to this node

- **Intended tool set**: HTTP, browser, scrapers, proxies, search.
- **Intended skills**: `server/skills/web_agent/` (http-request-skill,
  browser-skill, crawlee-scraper-skill, apify-skill, proxy-config-skill,
  duckduckgo/brave/serper/perplexity search skills).
- **Frontend theming**: pink dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/web_agent/`](../../../server/skills/web_agent/)
