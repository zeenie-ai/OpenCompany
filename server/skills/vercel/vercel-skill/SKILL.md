---
name: vercel-skill
description: Deploy sites and apps to Vercel, inspect deployments, stream logs, and manage projects/env/domains via the Vercel CLI. Deploy a directory and get back the live deployment URL; everything else the CLI supports is available through the custom command passthrough.
allowed-tools: "vercel"
metadata:
  author: opencompany
  version: "1.0"
  category: deployment

---

# Vercel Skill

Wrapper over the official [Vercel CLI](https://vercel.com/docs/cli).
Four operations: `deploy`, `inspect`, `list`, and a `custom`
passthrough that runs any other CLI command — env vars, logs,
rollback, promote, domains, aliases, projects.

## Tool: vercel

### Operations

| Operation | Purpose | Key fields |
|---|---|---|
| `deploy` | Deploy a directory; returns the live deployment URL | `path`, `project`, `prod`, `prebuilt`, `archive`, `scope`, `extra_args` |
| `inspect` | Details or build logs for one deployment | `deployment` (URL or id), `logs`, `wait`, `timeout` |
| `list` | Recent deployments for a project | `project`, `prod`, `status` |
| `custom` | Any other CLI command | `command` — exactly what you would type after `vercel ` |

### Response

```json
{
  "operation": "deploy",
  "success": true,
  "url": "https://my-site-abc123.vercel.app",
  "result": null,
  "stdout": "https://my-site-abc123.vercel.app",
  "stderr_tail": "… build output …"
}
```

For `deploy`, `url` is the live deployment URL (stdout carries only
the URL; progress goes to stderr — `stderr_tail` keeps the last part
for context). `list` / `inspect` return human-readable text in
`stdout`. `custom` commands that support `--json` / `--format=json`
(`logs`, `project ls`, `teams ls`, `domains verify`) return parsed
JSON in `result`.

On CLI failure the tool raises an error carrying Vercel's own
message — surface it verbatim; Vercel's errors are precise.

## Project naming — read before your first deploy

A **first deploy from an unlinked directory MUST set `project`**.
Vercel project names allow lowercase letters, digits, `.`, `_`, `-`
(no uppercase, no spaces, no `---` sequence). Workflow workspace
directories often violate this (`AI_Assistant_1`), so the tool
refuses an unlinked no-project deploy up front rather than failing
after the upload.

```json
{ "operation": "deploy", "project": "my-site" }
```

Once a directory is linked (`.vercel/project.json` exists — created
by the first successful deploy, or by
`custom: "link --yes --project my-site"`), later deploys may omit
`project`.

## Common workflows

### Deploy files from the workflow workspace

Write your site files (e.g. `index.html`) into the workspace, then:

```json
{ "operation": "deploy", "project": "my-site" }
```

`path` defaults to the per-workflow workspace directory. Preview
deploy by default; add `"prod": true` for production. The returned
`url` is live immediately.

### Deploy a specific directory

```json
{ "operation": "deploy", "path": "dist", "project": "my-site", "prod": true }
```

Relative `path` resolves against the workflow workspace; absolute
paths are used as-is.

### Wait for a deployment to finish building

```json
{ "operation": "inspect", "deployment": "https://my-site-abc123.vercel.app", "wait": true, "timeout": "5m" }
```

Add `"logs": true` to see build logs instead of the summary.

### List recent deployments

```json
{ "operation": "list", "project": "my-site", "status": "READY,ERROR" }
```

### Runtime logs (JSON)

```json
{ "operation": "custom", "command": "logs https://my-site-abc123.vercel.app --json" }
```

### Environment variables

```json
{ "operation": "custom", "command": "env ls" }
{ "operation": "custom", "command": "env rm API_KEY production --yes" }
```

Adding env values non-interactively needs stdin, which the
passthrough does not provide — set values at deploy time instead via
`extra_args` (`--env KEY=value` for runtime, `--build-env KEY=value`
for build time):

```json
{ "operation": "deploy", "project": "my-site", "extra_args": "--env API_URL=https://api.example.com" }
```

### Rollback / promote production

```json
{ "operation": "custom", "command": "rollback https://my-site-abc123.vercel.app" }
{ "operation": "custom", "command": "promote https://my-site-abc123.vercel.app" }
```

### Projects, domains, aliases

```json
{ "operation": "custom", "command": "project ls --format=json" }
{ "operation": "custom", "command": "domains ls" }
{ "operation": "custom", "command": "alias set https://my-site-abc123.vercel.app www.example.com" }
```

## Quoting and escaping

`command` and `extra_args` are parsed with `shlex.split`. Quote
arguments containing spaces with single quotes. Never put an access
token in `command` — auth is injected automatically as the
`VERCEL_TOKEN` environment variable.

## Timeouts and long builds

`deploy` waits up to 10 minutes for the build. For longer builds pass
`--no-wait` in `extra_args` (returns the URL immediately while the
build continues) and poll with `inspect` + `"wait": true` afterwards.

## Authentication

Two independent paths (either is enough), configured in the
Credentials Modal → Vercel:

1. **Login with Vercel** — browser device flow driven through the
   CLI. The CLI is auto-installed via npm on first use and keeps its
   auth state in a OpenCompany-owned config directory.
2. **Access token** — paste a token from
   [vercel.com/account/tokens](https://vercel.com/account/tokens).
   Injected as `VERCEL_TOKEN` on every call; takes precedence over
   CLI login and works headless.

If neither is configured the tool raises a credential error telling
the user which panel to open.

## Best practices

1. **Always pass a lowercase `project` on first deploys** (naming
   rules above).
2. **Default to preview deploys**; set `"prod": true` only when the
   user explicitly wants production.
3. **Return the deployment URL to the user** — it is live the moment
   `deploy` succeeds.
4. **Use the `archive` field** when deploying directories with
   thousands of files.
5. **Surface Vercel error messages verbatim** — don't paraphrase.
6. **Prefer `--json` / `--format=json` variants** in `custom`
   commands when you need to read fields programmatically.
