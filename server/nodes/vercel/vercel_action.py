"""Vercel Action — deploy / inspect / list over the Vercel CLI, plus a
raw-command passthrough (stripe idiom).

Parsing contract (vercel.com/docs/cli/deploy): for ``deploy`` stdout
carries ONLY the deployment URL — progress and errors go to stderr.
``list`` / ``inspect`` are human-readable text (no ``--json``); the raw
text is returned in ``stdout`` and the AI tool works with it directly.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.events import run_cli_command
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

from ._credentials import VercelCredential

_DEPLOY = {"displayOptions": {"show": {"operation": ["deploy"]}}}
_INSPECT = {"displayOptions": {"show": {"operation": ["inspect"]}}}
_LIST = {"displayOptions": {"show": {"operation": ["list"]}}}
_CUSTOM = {"displayOptions": {"show": {"operation": ["custom"]}}}

_DEPLOY_TIMEOUT = 600.0
_ONESHOT_TIMEOUT = 60.0
_INSPECT_WAIT_TIMEOUT = 300.0
_STDERR_TAIL_CHARS = 2000


class VercelActionParams(BaseModel):
    operation: Literal["deploy", "inspect", "list", "custom"] = "deploy"

    # deploy
    path: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Directory to deploy (defaults to the workflow workspace)",
            **_DEPLOY,
        },
    )
    prod: bool = Field(
        default=False,
        description="Deploy to production instead of a preview",
        json_schema_extra={"displayOptions": {"show": {"operation": ["deploy", "list"]}}},
    )
    prebuilt: bool = Field(
        default=False,
        description="Deploy a prior 'vercel build' output (.vercel/output) without uploading source",
        json_schema_extra=_DEPLOY,
    )
    archive: bool = Field(
        default=False,
        description="Compress the upload into a tarball (--archive=tgz) — avoids the files-count limit",
        json_schema_extra=_DEPLOY,
    )
    scope: str = Field(
        default="",
        json_schema_extra={"placeholder": "Team slug (optional)", **_DEPLOY},
    )
    extra_args: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Extra CLI flags, e.g. --env KEY=value --no-wait",
            **_DEPLOY,
        },
    )

    # deploy + list
    project: str = Field(
        default="",
        description=(
            "Vercel project name or id. Required for a first deploy from an unlinked directory — "
            "lowercase letters, digits, '.', '_' and '-' only (Vercel rejects anything else)"
        ),
        json_schema_extra={
            "placeholder": "my-site (lowercase)",
            "displayOptions": {"show": {"operation": ["deploy", "list"]}},
        },
    )

    # inspect
    deployment: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "https://my-app-abc123.vercel.app or deployment id",
            **_INSPECT,
        },
    )
    logs: bool = Field(
        default=False,
        description="Print build logs instead of deployment info",
        json_schema_extra=_INSPECT,
    )
    wait: bool = Field(
        default=False,
        description="Block until the deployment finishes",
        json_schema_extra=_INSPECT,
    )
    timeout: str = Field(
        default="3m",
        description="How long --wait blocks (ms-style duration, e.g. 5m)",
        json_schema_extra=_INSPECT,
    )

    # list
    status: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "READY,BUILDING,ERROR (comma-separated, optional)",
            **_LIST,
        },
    )

    # custom
    command: str = Field(
        default="",
        description="Vercel CLI command, exactly as typed after 'vercel '",
        json_schema_extra={
            "placeholder": "env ls | logs <url> --json | rollback <url>",
            **_CUSTOM,
        },
    )

    model_config = ConfigDict(extra="ignore")


class VercelActionOutput(BaseModel):
    operation: Optional[str] = None
    success: Optional[bool] = None
    url: Optional[str] = None
    result: Optional[Any] = None
    stdout: Optional[str] = None
    stderr_tail: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VercelActionNode(ActionNode):
    type = "vercelAction"
    display_name = "Vercel"
    subtitle = "CLI"
    group = ("deployment", "tool")
    description = "Deploy to Vercel and inspect deployments via the Vercel CLI (deploy / inspect / list / custom command)"
    component_kind = "square"
    tool_name = "vercel"
    tool_description = (
        "Interact with Vercel via its CLI. Operations: deploy (deploy a directory as a preview or "
        "production deployment; returns the deployment URL), inspect (details/build logs for a "
        "deployment URL or id), list (recent deployments for a project), custom (run any other "
        "Vercel CLI command — pass 'command' exactly as you would type after 'vercel ', e.g. "
        "'env ls', 'logs <url> --json', 'rollback <url>', 'promote <url>', 'domains ls', "
        "'project ls --format=json'). A first deploy from an unlinked directory requires the "
        "'project' field — a Vercel project name in lowercase letters/digits/'.'/'_'/'-'. "
        "Reference: https://vercel.com/docs/cli"
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (VercelCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = VercelActionParams
    Output = VercelActionOutput

    # ---- shared plumbing -------------------------------------------------

    async def _preflight(self) -> Optional[str]:
        """Ensure one auth path is live; return the stored token (or None
        when relying on CLI login). Raises an annotated PermissionError
        so the framework emits the credential envelope + the
        ``credential.oauth.runtime_failed`` broadcast."""
        from ._service import is_logged_in, stored_token

        token = await stored_token()
        if token or is_logged_in():
            return token
        err = PermissionError("Vercel not connected. Open Credentials → Vercel and click Login, or paste an access token (vercel.com/account/tokens).")
        err.provider = "vercel"  # type: ignore[attr-defined]
        err.reason = "missing"  # type: ignore[attr-defined]
        err.auth = "oauth2"  # type: ignore[attr-defined]
        raise err

    async def _run(
        self,
        argv: List[str],
        *,
        token: Optional[str],
        cwd: Optional[str] = None,
        timeout: float = _ONESHOT_TIMEOUT,
    ) -> Dict[str, Any]:
        from ._install import ensure_vercel_cli
        from ._service import global_argv, vercel_env

        try:
            binary = str(await ensure_vercel_cli())
        except Exception as e:
            raise RuntimeError(f"Vercel CLI install failed: {e}") from e

        result = await run_cli_command(
            binary=binary,
            argv=global_argv(argv),
            timeout=timeout,
            env=vercel_env(token),
            cwd=cwd,
        )
        if not result.get("success"):
            stderr = (result.get("stderr") or "").strip()
            detail = stderr[-_STDERR_TAIL_CHARS:] if stderr else (result.get("error") or "Vercel CLI invocation failed")
            raise NodeUserError(f"vercel {argv[0]} failed: {detail}")
        return result

    def _resolve_deploy_cwd(self, ctx: NodeContext, path: str) -> str:
        """Deploy directory: explicit param (absolute, or relative to the
        per-workflow workspace) falling back to the workspace itself."""
        workspace = ctx.workspace_dir
        if path:
            p = Path(path)
            if not p.is_absolute():
                if not workspace:
                    raise NodeUserError(f"Relative path {path!r} needs a workflow workspace — run inside a workflow or pass an absolute path")
                p = Path(workspace) / p
            if not p.is_dir():
                raise NodeUserError(f"Deploy path does not exist or is not a directory: {p}")
            return str(p)
        if not workspace:
            raise NodeUserError("No deploy path given and no workflow workspace available — set the 'path' parameter")
        return str(workspace)

    @staticmethod
    def _shape(operation: str, result: Dict[str, Any], *, url: Optional[str] = None) -> Dict[str, Any]:
        stderr = (result.get("stderr") or "").strip()
        return {
            "operation": operation,
            "success": True,
            "url": url,
            "result": result.get("result"),
            "stdout": result.get("stdout"),
            "stderr_tail": stderr[-_STDERR_TAIL_CHARS:] or None,
        }

    # ---- operations ------------------------------------------------------

    @staticmethod
    def _require_project_target(cwd: str, params: "VercelActionParams") -> None:
        """First deploys from an unlinked directory need an explicit
        project target, otherwise Vercel derives the name from the cwd —
        which for workflow workspaces (``AI_Assistant_1``) violates its
        naming rules and 400s late, after the upload. Fail fast with the
        remediation instead; Vercel stays the authority on what names
        are valid."""
        if params.project.strip():
            return
        if (Path(cwd) / ".vercel" / "project.json").exists():
            return  # already linked — Vercel knows the project
        if os.environ.get("VERCEL_PROJECT_ID"):
            return
        if "--project" in shlex.split(params.extra_args or ""):
            return
        raise NodeUserError(
            "First deploy from this directory needs a project target: set the 'project' "
            "parameter (lowercase letters, digits, '.', '_', '-'; see "
            "https://vercel.com/docs/projects/overview#project-name), or link the directory "
            "once with the custom operation: command='link --yes --project <name>'."
        )

    @Operation("deploy", cost={"service": "vercel", "action": "deploy", "count": 1})
    async def deploy(self, ctx: NodeContext, params: "VercelActionParams") -> Any:
        token = await self._preflight()
        cwd = self._resolve_deploy_cwd(ctx, params.path.strip())
        self._require_project_target(cwd, params)
        argv = ["deploy", "--yes"]
        if params.prod:
            argv.append("--prod")
        if params.prebuilt:
            argv.append("--prebuilt")
        if params.archive:
            argv.append("--archive=tgz")
        if params.project.strip():
            argv.extend(["--project", params.project.strip()])
        if params.scope.strip():
            argv.extend(["--scope", params.scope.strip()])
        if params.extra_args.strip():
            argv.extend(shlex.split(params.extra_args))
        result = await self._run(argv, token=token, cwd=cwd, timeout=_DEPLOY_TIMEOUT)
        # Deploy prints exactly the deployment URL on stdout.
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") else None
        return self._shape("deploy", result, url=url)

    @Operation("inspect", cost={"service": "vercel", "action": "inspect", "count": 1})
    async def inspect(self, ctx: NodeContext, params: "VercelActionParams") -> Any:
        token = await self._preflight()
        deployment = params.deployment.strip()
        if not deployment:
            raise NodeUserError("deployment is required (a deployment URL or id, e.g. https://my-app-abc123.vercel.app)")
        argv = ["inspect", deployment]
        if params.logs:
            argv.append("--logs")
        if params.wait:
            argv.append("--wait")
            if params.timeout.strip():
                argv.extend(["--timeout", params.timeout.strip()])
        timeout = _INSPECT_WAIT_TIMEOUT if params.wait else _ONESHOT_TIMEOUT
        result = await self._run(argv, token=token, timeout=timeout)
        return self._shape("inspect", result)

    @Operation("list", cost={"service": "vercel", "action": "list", "count": 1})
    async def list_deployments(self, ctx: NodeContext, params: "VercelActionParams") -> Any:
        token = await self._preflight()
        argv = ["list"]
        if params.project.strip():
            argv.append(params.project.strip())
        if params.prod:
            argv.append("--prod")
        if params.status.strip():
            argv.extend(["--status", params.status.strip()])
        argv.append("--yes")
        result = await self._run(argv, token=token)
        return self._shape("list", result)

    @Operation("custom", cost={"service": "vercel", "action": "custom", "count": 1})
    async def custom(self, ctx: NodeContext, params: "VercelActionParams") -> Any:
        token = await self._preflight()
        cmd = params.command.strip()
        if not cmd:
            raise NodeUserError("command is required (e.g. 'env ls', 'logs <url> --json', 'domains ls')")
        argv = shlex.split(cmd)
        # Directory-scoped commands (env/link/pull/...) run in the
        # workflow workspace when one exists.
        result = await self._run(argv, token=token, cwd=ctx.workspace_dir, timeout=_DEPLOY_TIMEOUT)
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") and "\n" not in stdout else None
        return self._shape("custom", result, url=url)
