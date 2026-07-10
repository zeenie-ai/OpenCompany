"""GitHub Action — typed core operations over the `gh` CLI, plus a
raw-command passthrough.

The gh CLI owns its own auth (Stripe pattern): no pre-flight check and
no token injection here — gh reads its system credential store
(populated by ``gh auth login``) or an ambient env token, and its own
"not logged in" error surfaces through the ``NodeUserError`` wrap.
List operations use ``--json`` for machine-readable results; ``gh
api`` (via ``custom``) returns raw JSON.
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.events import run_cli_command
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

from ._credentials import GitHubCredential

_CLONE = {"displayOptions": {"show": {"operation": ["repo_clone"]}}}
_PR_CREATE = {"displayOptions": {"show": {"operation": ["pr_create"]}}}
_LISTS = {"displayOptions": {"show": {"operation": ["pr_list", "issue_list"]}}}
_PR_MERGE = {"displayOptions": {"show": {"operation": ["pr_merge"]}}}
_ISSUE_CREATE = {"displayOptions": {"show": {"operation": ["issue_create"]}}}
_CUSTOM = {"displayOptions": {"show": {"operation": ["custom"]}}}

_ONESHOT_TIMEOUT = 120.0
_CLONE_TIMEOUT = 600.0
_STDERR_TAIL_CHARS = 2000

_PR_JSON_FIELDS = "number,title,state,url,author,headRefName,baseRefName,createdAt"
_ISSUE_JSON_FIELDS = "number,title,state,url,author,labels,createdAt"


class GitHubActionParams(BaseModel):
    operation: Literal[
        "repo_clone", "pr_create", "pr_list", "pr_merge", "issue_create", "issue_list", "custom"
    ] = "pr_list"

    # Shared repo target: gh infers OWNER/REPO from the cwd's git remote;
    # set explicitly to operate outside a checkout (maps to --repo / GH_REPO).
    repo: str = Field(
        default="",
        description="Target repository as OWNER/REPO (optional inside a cloned checkout)",
        json_schema_extra={
            "placeholder": "octocat/hello-world",
            "displayOptions": {"show": {"operation": ["pr_create", "pr_list", "pr_merge", "issue_create", "issue_list"]}},
        },
    )
    # Working directory (relative → workflow workspace).
    path: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Working directory (defaults to the workflow workspace)",
            "displayOptions": {"show": {"operation": ["repo_clone", "pr_create", "pr_merge", "custom"]}},
        },
    )

    # repo_clone
    clone_repo: str = Field(
        default="",
        description="Repository to clone: OWNER/REPO or a full URL",
        json_schema_extra={"placeholder": "octocat/hello-world", **_CLONE},
    )
    clone_dir: str = Field(default="", json_schema_extra={"placeholder": "Target directory name (optional)", **_CLONE})

    # pr_create
    title: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"operation": ["pr_create", "issue_create"]}}},
    )
    body: str = Field(
        default="",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"operation": ["pr_create", "issue_create"]}},
        },
    )
    base: str = Field(default="", json_schema_extra={"placeholder": "Base branch (optional)", **_PR_CREATE})
    head: str = Field(default="", json_schema_extra={"placeholder": "Head branch (optional)", **_PR_CREATE})
    draft: bool = Field(default=False, json_schema_extra=_PR_CREATE)
    fill: bool = Field(
        default=False,
        description="Derive title/body from commits (--fill)",
        json_schema_extra=_PR_CREATE,
    )

    # pr_list / issue_list
    state: Literal["open", "closed", "merged", "all"] = Field(default="open", json_schema_extra=_LISTS)
    limit: int = Field(default=30, ge=1, le=100, json_schema_extra=_LISTS)

    # pr_merge
    pr: str = Field(
        default="",
        description="PR number, URL, or branch",
        json_schema_extra=_PR_MERGE,
    )
    merge_method: Literal["squash", "merge", "rebase"] = Field(default="squash", json_schema_extra=_PR_MERGE)
    delete_branch: bool = Field(default=False, json_schema_extra=_PR_MERGE)

    # issue_create
    labels: str = Field(default="", json_schema_extra={"placeholder": "bug,help wanted (comma-separated, optional)", **_ISSUE_CREATE})

    # custom
    command: str = Field(
        default="",
        description="gh CLI command, exactly as typed after 'gh '",
        json_schema_extra={
            "placeholder": "api repos/{owner}/{repo} | run list | release create v1.0.0",
            **_CUSTOM,
        },
    )

    model_config = ConfigDict(extra="ignore")


class GitHubActionOutput(BaseModel):
    operation: Optional[str] = None
    success: Optional[bool] = None
    url: Optional[str] = None
    result: Optional[Any] = None
    stdout: Optional[str] = None
    stderr_tail: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class GitHubActionNode(ActionNode):
    type = "githubAction"
    display_name = "GitHub"
    subtitle = "gh CLI"
    group = ("vcs", "tool")
    description = "GitHub via the gh CLI — clone repos, create/list/merge PRs, create/list issues, or run any gh command"
    component_kind = "square"
    tool_name = "github"
    tool_description = (
        "Interact with GitHub via the gh CLI. Operations: repo_clone (clone into the workspace), "
        "pr_create (open a pull request; returns its URL), pr_list / issue_list (JSON results), "
        "pr_merge (squash/merge/rebase), issue_create, custom (any other gh command — pass "
        "'command' exactly as typed after 'gh ', e.g. 'api repos/{owner}/{repo}', 'run list', "
        "'release create v1.0.0 --notes ...', 'repo create my-repo --private'). Inside a cloned "
        "checkout gh infers the repository; otherwise set the 'repo' field (OWNER/REPO). "
        "Auth is managed by the gh CLI itself — connect via Credentials → GitHub (Login button) "
        "or run 'gh auth login' in a terminal. Reference: https://cli.github.com/manual"
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    # OutputPanel renders textual output preformatted (gh tables /
    # clone progress are terminal text, not markdown).
    ui_hints = {"outputMode": "terminal"}
    credentials = (GitHubCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = GitHubActionParams
    Output = GitHubActionOutput

    # ---- shared plumbing -------------------------------------------------

    async def _run(
        self,
        argv: List[str],
        *,
        cwd: Optional[str] = None,
        timeout: float = _ONESHOT_TIMEOUT,
    ) -> Dict[str, Any]:
        """No auth pre-flight (Stripe pattern) — gh authenticates from
        its own credential store, and its error (including "not logged
        in") surfaces via the NodeUserError wrap below."""
        from ._install import ensure_gh_cli
        from ._service import gh_env

        try:
            binary = str(await ensure_gh_cli())
        except Exception as e:
            raise RuntimeError(f"gh CLI install failed: {e}") from e

        result = await run_cli_command(
            binary=binary,
            argv=argv,
            timeout=timeout,
            env=gh_env(),
            cwd=cwd,
        )
        if not result.get("success"):
            stderr = (result.get("stderr") or "").strip()
            detail = stderr[-_STDERR_TAIL_CHARS:] if stderr else (result.get("error") or "gh invocation failed")
            raise NodeUserError(f"gh {argv[0]} failed: {detail}")
        return result

    def _cwd(self, ctx: NodeContext, path: str, *, required: bool = False) -> Optional[str]:
        from ._service import resolve_repo_path

        if not path and not required and not ctx.workspace_dir:
            return None
        return resolve_repo_path(ctx.workspace_dir, path.strip())

    @staticmethod
    def _repo_flag(params: "GitHubActionParams") -> List[str]:
        return ["--repo", params.repo.strip()] if params.repo.strip() else []

    @staticmethod
    def _shape(operation: str, result: Dict[str, Any], *, url: Optional[str] = None) -> Dict[str, Any]:
        """Output-panel shaping: when gh returned JSON (pr_list /
        issue_list / `gh api`), the parsed data IS the payload — the
        raw stdout string would just duplicate it as an unreadable
        blob (and pre-stringified JSON violates the output contract).
        Keys are omitted (not None'd) when empty so the panel shows
        only meaningful fields (`exclude_unset` preserves this)."""
        shaped: Dict[str, Any] = {"operation": operation, "success": True}
        if url:
            shaped["url"] = url
        parsed = result.get("result")
        stdout = (result.get("stdout") or "").strip()
        if parsed is not None:
            shaped["result"] = parsed
        elif stdout:
            shaped["stdout"] = stdout
        stderr = (result.get("stderr") or "").strip()
        if stderr:
            shaped["stderr_tail"] = stderr[-_STDERR_TAIL_CHARS:]
        return shaped

    # ---- operations ------------------------------------------------------

    @Operation("repo_clone", cost={"service": "github", "action": "repo_clone", "count": 1})
    async def repo_clone(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        repo = params.clone_repo.strip()
        if not repo:
            raise NodeUserError("clone_repo is required (OWNER/REPO or a full URL)")
        cwd = self._cwd(ctx, params.path, required=True)
        argv = ["repo", "clone", repo]
        if params.clone_dir.strip():
            argv.append(params.clone_dir.strip())
        result = await self._run(argv, cwd=cwd, timeout=_CLONE_TIMEOUT)
        return self._shape("repo_clone", result)

    @Operation("pr_create", cost={"service": "github", "action": "pr_create", "count": 1})
    async def pr_create(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        argv = ["pr", "create", *self._repo_flag(params)]
        if params.fill:
            argv.append("--fill")
        else:
            if not params.title.strip():
                raise NodeUserError("title is required (or set fill=true to derive it from commits)")
            argv += ["--title", params.title.strip(), "--body", params.body]
        if params.base.strip():
            argv += ["--base", params.base.strip()]
        if params.head.strip():
            argv += ["--head", params.head.strip()]
        if params.draft:
            argv.append("--draft")
        result = await self._run(argv, cwd=self._cwd(ctx, params.path))
        # gh prints the new PR's URL on stdout.
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") else None
        return self._shape("pr_create", result, url=url)

    @Operation("pr_list", cost={"service": "github", "action": "pr_list", "count": 1})
    async def pr_list(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        argv = [
            "pr", "list", *self._repo_flag(params),
            "--state", params.state, "--limit", str(params.limit),
            "--json", _PR_JSON_FIELDS,
        ]
        result = await self._run(argv, cwd=self._cwd(ctx, ""))
        return self._shape("pr_list", result)

    @Operation("pr_merge", cost={"service": "github", "action": "pr_merge", "count": 1})
    async def pr_merge(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        pr = params.pr.strip()
        if not pr:
            raise NodeUserError("pr is required (a PR number, URL, or branch name)")
        argv = ["pr", "merge", pr, *self._repo_flag(params), f"--{params.merge_method}"]
        if params.delete_branch:
            argv.append("--delete-branch")
        result = await self._run(argv, cwd=self._cwd(ctx, params.path))
        return self._shape("pr_merge", result)

    @Operation("issue_create", cost={"service": "github", "action": "issue_create", "count": 1})
    async def issue_create(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        if not params.title.strip():
            raise NodeUserError("title is required")
        argv = ["issue", "create", *self._repo_flag(params), "--title", params.title.strip(), "--body", params.body]
        for label in filter(None, (s.strip() for s in params.labels.split(","))):
            argv += ["--label", label]
        result = await self._run(argv, cwd=self._cwd(ctx, ""))
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") else None
        return self._shape("issue_create", result, url=url)

    @Operation("issue_list", cost={"service": "github", "action": "issue_list", "count": 1})
    async def issue_list(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        state = params.state if params.state != "merged" else "all"
        argv = [
            "issue", "list", *self._repo_flag(params),
            "--state", state, "--limit", str(params.limit),
            "--json", _ISSUE_JSON_FIELDS,
        ]
        result = await self._run(argv, cwd=self._cwd(ctx, ""))
        return self._shape("issue_list", result)

    @Operation("custom", cost={"service": "github", "action": "custom", "count": 1})
    async def custom(self, ctx: NodeContext, params: GitHubActionParams) -> Any:
        cmd = params.command.strip()
        if not cmd:
            raise NodeUserError("command is required (e.g. 'api repos/{owner}/{repo}', 'run list', 'release list')")
        argv = shlex.split(cmd)
        result = await self._run(argv, cwd=self._cwd(ctx, params.path), timeout=_CLONE_TIMEOUT)
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") and "\n" not in stdout else None
        return self._shape("custom", result, url=url)
