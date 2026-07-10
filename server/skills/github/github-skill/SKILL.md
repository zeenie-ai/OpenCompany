---
name: github-skill
description: Work with GitHub via the gh CLI — clone repositories, create/list/merge pull requests, create/list issues, and run any other gh command (API calls, workflow runs, releases, repo administration). List operations return parsed JSON.
allowed-tools: "github"
metadata:
  author: machina
  version: "1.0"
  category: developer

---

# GitHub Skill

Wrapper over the official [GitHub CLI](https://cli.github.com/manual).
Typed operations for the core flows plus a `custom` passthrough that
covers the entire gh surface.

## Tool: github

### Operations

| Operation | Purpose | Key fields |
|---|---|---|
| `repo_clone` | Clone a repository into the workflow workspace | `clone_repo` (OWNER/REPO or URL), `clone_dir`, `path` |
| `pr_create` | Open a pull request; returns its URL | `title`, `body`, `base`, `head`, `draft`, `fill`, `repo`, `path` |
| `pr_list` | List pull requests (parsed JSON) | `repo`, `state`, `limit` |
| `pr_merge` | Merge a PR | `pr` (number/URL/branch), `merge_method` (squash/merge/rebase), `delete_branch`, `repo` |
| `issue_create` | Open an issue; returns its URL | `title`, `body`, `labels`, `repo` |
| `issue_list` | List issues (parsed JSON) | `repo`, `state`, `limit` |
| `custom` | Any other gh command | `command` — exactly what you would type after `gh ` |

### Response

```json
{
  "operation": "pr_list",
  "success": true,
  "url": null,
  "result": [{ "number": 42, "title": "Fix login", "state": "OPEN", "url": "https://github.com/o/r/pull/42", "author": {"login": "octocat"}, "headRefName": "fix-login", "baseRefName": "main", "createdAt": "…" }],
  "stdout": "…raw output…",
  "stderr_tail": null
}
```

`pr_list` / `issue_list` return parsed JSON in `result` (via gh's
`--json`). `pr_create` / `issue_create` put the new item's URL in
`url`. `custom` commands that emit JSON (`api …`, `… --json fields`)
come back parsed in `result` too.

On failure the tool raises an error carrying gh's own message —
surface it verbatim; gh's errors are precise (including "not logged
in", which tells the user exactly how to authenticate).

## Repository targeting

Inside a cloned checkout (after `repo_clone`, with `path` pointing at
it) gh infers OWNER/REPO from the git remote. Everywhere else, set the
`repo` field explicitly:

```json
{ "operation": "pr_list", "repo": "octocat/hello-world", "state": "open" }
```

## Common workflows

### Clone, then work inside the checkout

```json
{ "operation": "repo_clone", "clone_repo": "octocat/hello-world" }
{ "operation": "pr_create", "path": "hello-world", "fill": true }
```

`path` resolves relative to the workflow workspace.

### Review flow

```json
{ "operation": "pr_list", "repo": "octocat/hello-world", "state": "open" }
{ "operation": "custom", "command": "pr view 42 --repo octocat/hello-world --json title,body,files" }
{ "operation": "pr_merge", "pr": "42", "repo": "octocat/hello-world", "merge_method": "squash", "delete_branch": true }
```

### Issues

```json
{ "operation": "issue_create", "repo": "octocat/hello-world", "title": "Crash on login", "body": "Steps…", "labels": "bug" }
{ "operation": "issue_list", "repo": "octocat/hello-world", "state": "open", "limit": 50 }
```

### The full gh surface via custom

```json
{ "operation": "custom", "command": "api repos/{owner}/{repo}" }
{ "operation": "custom", "command": "api user" }
{ "operation": "custom", "command": "run list --repo octocat/hello-world --json databaseId,status,conclusion" }
{ "operation": "custom", "command": "release create v1.0.0 --notes 'First release'" }
{ "operation": "custom", "command": "repo create my-new-repo --private" }
{ "operation": "custom", "command": "gist create notes.md" }
```

Prefer `--json <fields>` (list/view commands) or `gh api` when you
need machine-readable output. `gh api` supports `{owner}/{repo}`
placeholders inside a checkout.

## Quoting and escaping

`command` is parsed with `shlex.split` — quote arguments containing
spaces with single quotes: `custom: "release create v1.0.0 --notes 'First release'"`.

## Authentication

The gh CLI owns its auth — MachinaOs never stores a token. Three
equivalent ways to connect (any one is enough):

1. **Credentials Modal → GitHub → Login with GitHub** — gh (auto-installed)
   starts its browser device flow: the modal shows a one-time code and
   opens github.com/login/device; enter the code and approve.
2. **`gh auth login`** in a terminal on this machine.
3. **`gh auth login --with-token`** in a terminal, piping a Personal
   Access Token (scopes: repo, read:org, gist).

If a command fails with an authentication error, tell the user to
connect via one of these; don't ask them for a token.

## Best practices

1. **Set `repo` explicitly** unless you're operating inside a cloned
   checkout.
2. **Use `fill: true`** on pr_create when commits already carry good
   messages.
3. **Return created URLs to the user** (PRs, issues, releases).
4. **Destructive administration** (repo delete, etc.) goes through
   `custom` and gh will require its own `--yes`-style confirmation
   flags — pass them only when the user explicitly asked.
5. **Surface gh error messages verbatim** — don't paraphrase.
