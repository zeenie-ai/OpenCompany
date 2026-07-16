"""Cloudflare Action — typed core operations over the `cf` CLI, plus a
raw-command passthrough.

The cf CLI owns its own auth (Stripe/gh pattern): no pre-flight check
and no token injection here — cf reads its stored OAuth session
(populated by ``cf auth login``) or an ambient ``CLOUDFLARE_API_TOKEN``
env token, and its own "Not logged in" error surfaces through the
``NodeUserError`` wrap.

cf 0.2.0 prints JSON to stdout by default (status noise goes to
stderr), so parsed results flow straight into the output panel's JSON
tree. Argv shapes below are verified against the pinned 0.2.0 (`cf
<cmd> --help-full`): zone selection is the global ``-z/--zone`` flag,
``dns records create`` takes ``--body`` (raw JSON request body — the
schema-stable escape hatch; per-field flags churn between preview
versions), and ``dns records delete`` takes the record id as a
positional.
"""

from __future__ import annotations

import json
import shlex
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.events import run_cli_command
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

from ._credentials import CloudflareCredential

_ZONES_LIST = {"displayOptions": {"show": {"operation": ["zones_list"]}}}
_DNS_ANY = {"displayOptions": {"show": {"operation": ["dns_records_list", "dns_record_create", "dns_record_delete"]}}}
_CREATE = {"displayOptions": {"show": {"operation": ["dns_record_create"]}}}
_DELETE = {"displayOptions": {"show": {"operation": ["dns_record_delete"]}}}
_CUSTOM = {"displayOptions": {"show": {"operation": ["custom"]}}}

_ONESHOT_TIMEOUT = 120.0
_CUSTOM_TIMEOUT = 300.0
_STDERR_TAIL_CHARS = 2000


class CloudflareActionParams(BaseModel):
    operation: Literal[
        "whoami", "zones_list", "dns_records_list", "dns_record_create", "dns_record_delete", "custom"
    ] = "whoami"

    # Shared zone target for the DNS operations (global -z/--zone flag).
    zone: str = Field(
        default="",
        description="Zone ID or domain name",
        json_schema_extra={"placeholder": "example.com or zone ID", **_DNS_ANY},
    )

    # zones_list
    name_filter: str = Field(
        default="",
        description="Filter zones by domain name",
        json_schema_extra={"placeholder": "example.com (optional)", **_ZONES_LIST},
    )
    account_id: str = Field(
        default="",
        description="Filter zones by account ID",
        json_schema_extra={"placeholder": "Account ID (optional)", **_ZONES_LIST},
    )

    # dns_record_create
    record_body: str = Field(
        default="",
        description="DNS record as a raw JSON request body (--body)",
        json_schema_extra={
            "rows": 4,
            "placeholder": '{"type":"A","name":"www","content":"192.0.2.1","ttl":1,"proxied":false}',
            **_CREATE,
        },
    )

    # dns_record_delete
    record_id: str = Field(
        default="",
        description="DNS record ID to delete",
        json_schema_extra={"placeholder": "023e105f4ecef8ad9ca31a8372d0c353", **_DELETE},
    )

    # custom
    command: str = Field(
        default="",
        description="cf CLI command, exactly as typed after 'cf '",
        json_schema_extra={
            "placeholder": "accounts list | dns records get <id> --zone example.com | agent-context dns",
            **_CUSTOM,
        },
    )

    model_config = ConfigDict(extra="ignore")

    @field_validator("record_body", mode="before")
    @classmethod
    def _coerce_record_body(cls, v: Any) -> Any:
        """LLM tool calls may pass the record as a real JSON object —
        coerce to the string the CLI flag expects (canonical
        field_validator(mode="before") rule)."""
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return v


class CloudflareActionOutput(BaseModel):
    operation: Optional[str] = None
    success: Optional[bool] = None
    url: Optional[str] = None
    result: Optional[Any] = None
    stdout: Optional[str] = None
    stderr_tail: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CloudflareActionNode(ActionNode):
    type = "cloudflareAction"
    display_name = "Cloudflare"
    subtitle = "cf CLI"
    group = ("deployment", "tool")
    description = "Cloudflare via the official cf CLI — check auth, list zones, manage DNS records, or run any cf command"
    component_kind = "square"
    tool_name = "cloudflare"
    tool_description = (
        "Interact with Cloudflare via the official cf CLI. Operations: whoami (auth status + "
        "identity), zones_list (list/filter zones), dns_records_list (records for a zone), "
        "dns_record_create (pass 'record_body' as the raw JSON record, e.g. "
        '{"type":"A","name":"www","content":"192.0.2.1","ttl":1}), dns_record_delete (by record '
        "id), custom (any other cf command — pass 'command' exactly as typed after 'cf ', e.g. "
        "'accounts list', 'dns records get <id> --zone example.com', 'registrar domains list', "
        "'agent-context dns' for deep per-product docs, 'schema <cmd>' for request shapes). "
        "DNS operations take 'zone' as a zone ID or domain name. Output is JSON. "
        "Auth is managed by the cf CLI itself — connect via Credentials -> Cloudflare (Login "
        "button) or run 'cf auth login' in a terminal; a CLOUDFLARE_API_TOKEN env var also works. "
        "Reference: https://developers.cloudflare.com"
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    # OutputPanel renders textual output preformatted (cf status text /
    # custom-command output is terminal text, not markdown).
    ui_hints = {"outputMode": "terminal"}
    credentials = (CloudflareCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = CloudflareActionParams
    Output = CloudflareActionOutput

    # ---- shared plumbing -------------------------------------------------

    async def _run(
        self,
        argv: List[str],
        *,
        timeout: float = _ONESHOT_TIMEOUT,
    ) -> Dict[str, Any]:
        """No auth pre-flight (Stripe pattern) — cf authenticates from
        its own config (or an ambient env token), and its error
        (including "Not logged in") surfaces via the NodeUserError wrap
        below."""
        from ._install import ensure_cf_cli
        from ._service import cf_env

        try:
            binary = str(await ensure_cf_cli())
        except Exception as e:
            raise RuntimeError(f"cf CLI install failed: {e}") from e

        result = await run_cli_command(
            binary=binary,
            argv=argv,
            timeout=timeout,
            env=cf_env(),
        )
        if not result.get("success"):
            stderr = (result.get("stderr") or "").strip()
            detail = stderr[-_STDERR_TAIL_CHARS:] if stderr else (result.get("error") or "cf invocation failed")
            raise NodeUserError(f"cf {argv[0]} failed: {detail}")
        return result

    @staticmethod
    def _zone_flag(params: "CloudflareActionParams") -> List[str]:
        zone = params.zone.strip()
        if not zone:
            raise NodeUserError("zone is required (a zone ID or domain name)")
        return ["--zone", zone]

    @staticmethod
    def _parse_ndjson(stdout: str) -> Optional[List[Any]]:
        """Multi-object stdout (one JSON document per line) defeats
        run_cli_command's single ``json.loads`` — recover it here.
        ``None`` when the text isn't NDJSON."""
        lines = [ln for ln in (s.strip() for s in stdout.splitlines()) if ln]
        if len(lines) < 2:
            return None
        parsed: List[Any] = []
        for ln in lines:
            try:
                parsed.append(json.loads(ln))
            except ValueError:
                return None
        return parsed

    @classmethod
    def _shape(cls, operation: str, result: Dict[str, Any], *, url: Optional[str] = None) -> Dict[str, Any]:
        """Output-panel shaping: when cf returned JSON (its default
        stdout contract), the parsed data IS the payload — the raw
        stdout string would just duplicate it as an unreadable blob
        (and pre-stringified JSON violates the output contract). Keys
        are omitted (not None'd) when empty so the panel shows only
        meaningful fields (`exclude_unset` preserves this)."""
        shaped: Dict[str, Any] = {"operation": operation, "success": True}
        if url:
            shaped["url"] = url
        parsed = result.get("result")
        stdout = (result.get("stdout") or "").strip()
        if parsed is None and stdout:
            parsed = cls._parse_ndjson(stdout)
        if parsed is not None:
            shaped["result"] = parsed
        elif stdout:
            shaped["stdout"] = stdout
        stderr = (result.get("stderr") or "").strip()
        if stderr:
            shaped["stderr_tail"] = stderr[-_STDERR_TAIL_CHARS:]
        return shaped

    # ---- operations ------------------------------------------------------

    @Operation("whoami", cost={"service": "cloudflare", "action": "whoami", "count": 1})
    async def whoami(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        result = await self._run(["auth", "whoami"])
        return self._shape("whoami", result)

    @Operation("zones_list", cost={"service": "cloudflare", "action": "zones_list", "count": 1})
    async def zones_list(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        argv = ["zones", "list"]
        if params.name_filter.strip():
            argv += ["--name", params.name_filter.strip()]
        if params.account_id.strip():
            argv += ["--account-id", params.account_id.strip()]
        result = await self._run(argv)
        return self._shape("zones_list", result)

    @Operation("dns_records_list", cost={"service": "cloudflare", "action": "dns_records_list", "count": 1})
    async def dns_records_list(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        argv = ["dns", "records", "list", *self._zone_flag(params)]
        result = await self._run(argv)
        return self._shape("dns_records_list", result)

    @Operation("dns_record_create", cost={"service": "cloudflare", "action": "dns_record_create", "count": 1})
    async def dns_record_create(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        body = params.record_body.strip()
        if not body:
            raise NodeUserError('record_body is required (raw JSON, e.g. {"type":"A","name":"www","content":"192.0.2.1","ttl":1})')
        try:
            json.loads(body)
        except ValueError as e:
            raise NodeUserError(f"record_body is not valid JSON: {e}") from e
        argv = ["dns", "records", "create", *self._zone_flag(params), "--body", body]
        result = await self._run(argv)
        return self._shape("dns_record_create", result)

    @Operation("dns_record_delete", cost={"service": "cloudflare", "action": "dns_record_delete", "count": 1})
    async def dns_record_delete(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        record_id = params.record_id.strip()
        if not record_id:
            raise NodeUserError("record_id is required (find it via dns_records_list)")
        argv = ["dns", "records", "delete", record_id, *self._zone_flag(params)]
        result = await self._run(argv)
        return self._shape("dns_record_delete", result)

    @Operation("custom", cost={"service": "cloudflare", "action": "custom", "count": 1})
    async def custom(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        cmd = params.command.strip()
        if not cmd:
            raise NodeUserError("command is required (e.g. 'accounts list', 'dns records get <id> --zone example.com', 'agent-context dns')")
        argv = shlex.split(cmd)
        result = await self._run(argv, timeout=_CUSTOM_TIMEOUT)
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") and "\n" not in stdout else None
        return self._shape("custom", result, url=url)
