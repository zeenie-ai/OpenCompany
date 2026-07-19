"""Agent Builder -- runtime canvas-mutation tool for AI agents.

Multi-op plugin matching the gmail / calendar / drive convention:
ONE node, ONE Params model with an ``operation: Literal[...]``
discriminator, FIVE ``@Operation`` methods. The LLM sees ONE tool
``agentBuilder`` with a select-style ``operation`` field; agents wire
the node into their standard ``input-tools`` handle (no special
topology, no parallel dispatch system).

The five operations are pure canvas mutations:

* ``inspect_canvas`` (read-only) -- snapshot of nodes / edges + what's
  wired to the calling agent.
* ``add_tool`` -- spawn a tool node + edge into caller's input-tools.
* ``add_skill`` -- toggle a skill on caller's Master Skill (or
  spawn a Master Skill if absent).
* ``add_subagent`` -- spawn a delegate agent + wire to caller's
  input-teammates (team-leads only).
* ``create_workflow`` -- persist a fresh empty workflow + return its
  workflow_id.

Each mutation pushes a ``workflow_ops_apply`` event via the plugin's
``_events.broadcast_workflow_ops`` wrapper so the live canvas updates.
The agent loop binds tools at the start of each turn, so a mutation
made mid-run does NOT add a callable tool to the current turn; the
agent's NEXT invocation rediscovers it. Each summary string ends
with "Available on your next turn" so the LLM doesn't loop trying
to call something that isn't there yet.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services import workflow_ops
from services.node_registry import registered_node_classes
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode
from services.workflow_naming import next_available_slug


logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_TOOL_HANDLE = "input-tools"
_SKILL_HANDLE = "input-skill"
_SKILL_OUTPUT_HANDLE = "output-tool"
_TEAMMATES_HANDLE = "input-teammates"
_TEAMMATES_OUTPUT_HANDLE = "output-main"
_TOOL_OUTPUT_HANDLE = "output-main"
_MASTER_SKILL_TYPE = "masterSkill"
_MASTER_SKILL_LABEL = "Master Skill"
_AGENT_BUILDER_TYPE = "agentBuilder"
_TASK_MANAGER_TYPE = "taskManager"
_TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})
_DENIED_TOOL_TYPES = frozenset({_AGENT_BUILDER_TYPE, _MASTER_SKILL_TYPE, _TASK_MANAGER_TYPE})
_KEY_PARAM_FIELDS = ("provider", "model", "operation", "url", "query")
_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

# Temporary feature flag — set to True to re-enable the create_workflow
# operation. Flipping this constant restores the operation's prior
# behaviour (validation + slug allocation + database.save_workflow).
# The implementation below stays intact so re-enabling is one line.
_CREATE_WORKFLOW_ENABLED = False


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _apply_canvas_ops(
    data: Dict[str, Any],
    ops: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply an agentBuilder batch to one current workflow snapshot.

    Minted node IDs and exact edge identities are de-duplicated so retrying
    the same batch is harmless. Distinct batches retain their distinct minted
    nodes even when they happen to add the same type concurrently.
    """
    data = dict(data or {})
    nodes = [dict(node) for node in (data.get("nodes") or [])]
    edges = [dict(edge) for edge in (data.get("edges") or [])]
    ref_to_id: Dict[str, str] = {}
    changed = False

    def _resolve(ref: Any) -> Optional[str]:
        if isinstance(ref, str):
            return ref
        if isinstance(ref, dict) and "client_ref" in ref:
            return ref_to_id.get(ref["client_ref"])
        return None

    for op in ops:
        op_type = op.get("type")
        if op_type == "add_node":
            node_type = op.get("node_type") or ""
            if not node_type:
                continue
            new_id = op.get("minted_id") or op.get("client_ref") or ""
            if not new_id:
                continue
            client_ref = op.get("client_ref") or ""

            if client_ref:
                ref_to_id[client_ref] = new_id
            if any(node.get("id") == new_id for node in nodes):
                continue
            params = op.get("parameters") or {}
            nodes.append(
                {
                    "id": new_id,
                    "type": node_type,
                    "position": (
                        op.get("position")
                        if isinstance(op.get("position"), dict)
                        and "x" in op["position"]
                        else {"x": 0, "y": 0}
                    ),
                    "data": {
                        "label": op.get("label") or params.get("label") or node_type,
                        "parameters": dict(params),
                    },
                }
            )
            changed = True
        elif op_type == "add_edge":
            source = _resolve(op.get("source"))
            target = _resolve(op.get("target"))
            if not source or not target:
                continue
            identity = (
                source,
                target,
                op.get("source_handle"),
                op.get("target_handle"),
            )
            if any(
                (
                    edge.get("source"),
                    edge.get("target"),
                    edge.get("sourceHandle"),
                    edge.get("targetHandle"),
                )
                == identity
                for edge in edges
            ):
                continue
            edges.append(
                {
                    "id": f"e-{source}-{target}",
                    "source": source,
                    "target": target,
                    "sourceHandle": op.get("source_handle"),
                    "targetHandle": op.get("target_handle"),
                }
            )
            changed = True
        elif op_type == "set_node_parameters":
            node_id = op.get("node_id")
            merge = op.get("parameters") or {}
            for node in nodes:
                if node.get("id") != node_id:
                    continue
                node_data = dict(node.get("data") or {})
                existing = dict(node_data.get("parameters") or {})
                merged = {**existing, **merge}
                if merged != existing:
                    node_data["parameters"] = merged
                    node["data"] = node_data
                    changed = True
                break

    data["nodes"] = nodes
    data["edges"] = edges
    return data, {"changed": changed, "nodes": len(nodes), "edges": len(edges)}


async def _persist_canvas_mutation(
    workflow_id: Optional[str],
    ops: List[Dict[str, Any]],
) -> Optional[bool]:
    """Apply the three op types agentBuilder emits to the persisted
    ``workflow.data`` via the existing ``database.get_workflow`` +
    ``database.save_workflow`` round-trip.

    Without this, every chat-message trigger reloads the workflow JSON
    from the DB and sees the pre-mutation snapshot — the agent then
    re-spawns the same tools the LLM already added in a prior run.
    Persisting after each mutation makes the DB the source of truth
    for cross-run canvas state.

    Adopts the BE-side ``minted_id`` (already stamped on ``add_node``
    ops for FE alignment) so the persisted row matches the React Flow
    state exactly — no id divergence on reload.
    """
    if not ops:
        return False
    if not workflow_id:
        return None
    try:
        from services.plugin.deps import get_database

        database = get_database()
        atomic_mutate = getattr(database, "mutate_workflow_data_atomic", None)
        if atomic_mutate is not None and inspect.iscoroutinefunction(atomic_mutate):
            canonical_ops = json.dumps(
                ops,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            mutation_id = (
                f"agent-builder:{workflow_id}:"
                f"{hashlib.sha256(canonical_ops.encode('utf-8')).hexdigest()}"
            )
            workflow, metadata, applied = await atomic_mutate(
                workflow_id,
                lambda current: _apply_canvas_ops(current, ops),
                mutation_id=mutation_id,
                operation="agent_builder_ops",
            )
            if workflow is None or not metadata.get("found", True):
                logger.debug(
                    "[agentBuilder] persist skipped: workflow %s not found",
                    workflow_id,
                )
                return None
            logger.info(
                "[agentBuilder] transactionally persisted %s ops to workflow %s "
                "(nodes=%d edges=%d applied=%s)",
                [op.get("type") for op in ops],
                workflow_id,
                metadata.get("nodes", 0),
                metadata.get("edges", 0),
                applied,
            )
            return bool(applied and metadata.get("changed"))

        # Compatibility path for existing isolated test doubles.
        workflow = await database.get_workflow(workflow_id)
        if workflow is None:
            logger.debug("[agentBuilder] persist skipped: workflow %s not found", workflow_id)
            return None

        data, metadata = _apply_canvas_ops(dict(workflow.data or {}), ops)
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
        if not metadata.get("changed"):
            return False
        await database.save_workflow(
            workflow_id=workflow_id,
            name=workflow.name,
            slug=workflow.slug,
            data=data,
            description=workflow.description,
        )
        logger.info(
            "[agentBuilder] persisted %s ops to workflow %s (nodes=%d edges=%d)",
            [op.get("type") for op in ops],
            workflow_id,
            len(nodes),
            len(edges),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[agentBuilder] persist failed for workflow %s: %s", workflow_id, exc, exc_info=True
        )
        return None


async def _broadcast(workflow_id: Optional[str], caller_id: str, ops: List[Dict[str, Any]]) -> None:
    """Persist the canvas mutation to the DB, then push a
    ``workflow_ops_apply`` event so the live React Flow canvas updates.

    Persist-then-broadcast: the next agentBuilder call (within this run
    OR on the next chat-message trigger) reads from the DB and sees the
    new state, so duplicate detection in ``_find_wired_types`` catches
    repeat add_tool calls instead of silently spawning copies.

    Wave 12 B10: routes through plugin _events.py wrapper.
    """
    if not ops:
        return
    persisted = await _persist_canvas_mutation(workflow_id, ops)
    # ``None`` means persistence was unavailable/missing and is explicitly
    # best-effort: the live canvas still needs the operation event. ``False``
    # is a known idempotent no-op and should not be rebroadcast.
    if persisted is False:
        return
    try:
        from ._events import broadcast_workflow_ops

        await broadcast_workflow_ops(
            workflow_id=workflow_id,
            caller_node_id=caller_id,
            operations=ops,
        )
        logger.info(
            "[agentBuilder] broadcast workflow_ops_apply: workflow_id=%s " "caller=%s ops=%s",
            workflow_id,
            caller_id,
            [op.get("type") for op in ops],
        )
    except Exception as exc:
        logger.warning(f"[agentBuilder] broadcast failed: {exc}", exc_info=True)


def _allowlist_config() -> Dict[str, Any]:
    """Load the operator allowlist config once per call — same source of
    truth the UI palette uses (server/config/node_allowlist.json). The
    NodeAllowlistService singleton handles parse failures with a safe
    show_all fallback, so this never raises on missing / malformed JSON.
    """
    try:
        from services.node_allowlist import get_node_allowlist_service

        return get_node_allowlist_service().get_config()
    except Exception as exc:  # noqa: BLE001 — defensive: never fail catalogue
        logger.debug("[agentBuilder] node_allowlist read failed: %s", exc)
        return {"disabled_nodes": [], "disabled_groups": [], "disabled_skill_folders": []}


def _is_blocked_by_allowlist(cls: Any, ntype: str, config: Dict[str, Any]) -> bool:
    """``True`` when ``ntype`` or any group in ``cls.group`` is in the
    operator blocklist. Honors both ``disabled_nodes`` (per-type) and
    ``disabled_groups`` (every plugin in the named group)."""
    if ntype in (config.get("disabled_nodes") or ()):
        return True
    disabled_groups = set(config.get("disabled_groups") or ())
    if disabled_groups:
        plugin_groups = set(getattr(cls, "group", ()) or ())
        if plugin_groups & disabled_groups:
            return True
    return False


def _allowed_tool_types() -> set[str]:
    """Tool node types the LLM may spawn via ``add_tool``.

    Includes:
    - Pure ToolNode plugins (``component_kind == 'tool'``).
    - Dual-purpose ActionNode plugins with ``usable_as_tool=True``
      (e.g. ``twitterSearch``, ``googleGmail``, ``pythonExecutor``,
      ``fileRead``). These are the bulk of the actually-useful tools;
      excluding them was the source of "only a few nodes visible" reports.
    - Excludes chat-model plugins (``component_kind == 'model'``) even
      when they carry ``usable_as_tool=True`` — models are configured
      via input-model handles, not spawned as agent tools.

    Excludes ``_DENIED_TOOL_TYPES`` (recursion guard: no agentBuilder /
    masterSkill spawnable) AND anything the operator has marked
    ``disabled_nodes`` / ``disabled_groups`` in node_allowlist.json.
    """
    config = _allowlist_config()
    out: set[str] = set()
    for ntype, cls in registered_node_classes().items():
        if ntype in _DENIED_TOOL_TYPES:
            continue
        kind = getattr(cls, "component_kind", "")
        is_tool = kind == "tool"
        is_dual_purpose = bool(getattr(cls, "usable_as_tool", False)) and kind != "model"
        if not (is_tool or is_dual_purpose):
            continue
        if _is_blocked_by_allowlist(cls, ntype, config):
            continue
        out.add(ntype)
    return out


def _allowed_subagent_types() -> set[str]:
    """Agent types the LLM may spawn as teammates. Honors
    ``disabled_nodes`` + ``disabled_groups`` from node_allowlist.json
    so the same config governs both UI palette and LLM spawnable set.
    """
    config = _allowlist_config()
    return {
        ntype
        for ntype, cls in registered_node_classes().items()
        if getattr(cls, "component_kind", "") == "agent"
        and not _is_blocked_by_allowlist(cls, ntype, config)
    }


def _is_team_lead(node_type: str) -> bool:
    return node_type in _TEAM_LEAD_TYPES


def _mint_node_id(prefix: str) -> str:
    """Mint a fresh node id sharing the convention used by
    :func:`services.workflow_import.remap_node_ids` and the frontend's
    ``client/src/lib/workflowOps.ts::newId``.

    The minted id is stamped onto ``add_node`` ops as ``minted_id`` so
    the FE applier adopts it instead of generating its own — keeping
    the BE-side ``tool_node_id`` (used for ``update_node_status``
    broadcasts in the rebind path) aligned with the React Flow node id
    the canvas glows on.
    """
    import secrets
    import time

    return f"{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"


def _summary_suffix(ctx: NodeContext) -> str:
    """Trailing sentence on agentBuilder op summaries.

    When the user's "Auto-Rebind Tools After Canvas Changes" toggle is
    on (default), the agent loop rebinds its LLM tool surface after
    every canvas-mutating tool — the new wiring IS callable in the
    same turn. When off, the LLM must wait for the next Run.

    The flag rides on ``ctx.raw["auto_rebind_tools"]`` (set by the
    agent loop dispatcher reading the UserSettings field).
    """
    rebind_on = bool((ctx.raw or {}).get("auto_rebind_tools", True))
    return "Available immediately — call it in your next response." if rebind_on else "Available on your next turn."


def _toggle_skill(
    config: Optional[Dict[str, Dict[str, Any]]],
    skill_name: str,
    enabled: bool,
) -> Dict[str, Dict[str, Any]]:
    """Return a new skills_config dict with ``skill_name`` toggled."""
    base: Dict[str, Dict[str, Any]] = dict(config or {})
    existing = base.get(skill_name, {})
    base[skill_name] = {
        "enabled": enabled,
        "instructions": existing.get("instructions", ""),
        "isCustomized": existing.get("isCustomized", False),
    }
    return base


def _skill_folder_exists(skill_folder: str) -> bool:
    if not _SKILLS_DIR.exists():
        return False
    for path in _SKILLS_DIR.rglob(skill_folder):
        if path.is_dir() and (path / "SKILL.md").exists():
            return True
    return False


# ----------------------------------------------------------------------------
# Duplicate-detection + catalogue helpers
# ----------------------------------------------------------------------------


async def _load_live_canvas(
    ctx: NodeContext,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Reload the canvas snapshot from the persisted ``workflow.data``
    so successive agentBuilder operations in the SAME run see each
    other's mutations.

    The per-tool activity's ``ctx.nodes`` / ``ctx.edges`` is frozen at
    MachinaWorkflow start — without this reload, every add_tool /
    add_skill / add_subagent call inside a single AgentWorkflow run
    would see the same stale snapshot, ``_find_wired_types`` would
    never detect the just-spawned tool, and duplicates would pile up.

    Falls back to ``ctx.nodes`` / ``ctx.edges`` when ``ctx.workflow_id``
    is missing (standalone Run) or the DB lookup fails. Read-only —
    callers still pass the returned tuple into ``_find_wired_types``
    explicitly so the caller controls precedence.
    """
    if ctx.workflow_id:
        try:
            from services.plugin.deps import get_database

            database = get_database()
            workflow = await database.get_workflow(ctx.workflow_id)
            if workflow is not None and workflow.data:
                nodes = list(workflow.data.get("nodes") or [])
                edges = list(workflow.data.get("edges") or [])
                return nodes, edges
        except Exception as exc:  # noqa: BLE001 — defensive: fall back to ctx
            logger.debug(
                "[agentBuilder] live canvas reload failed for %s: %s",
                ctx.workflow_id,
                exc,
            )
    return list(ctx.nodes or []), list(ctx.edges or [])


def _resolve_caller_from(
    self_id: str,
    edges: List[Dict[str, Any]],
) -> str:
    """``_resolve_caller`` variant that takes an explicit edges list so
    callers can resolve against the freshly-reloaded canvas instead of
    ``ctx.edges`` (frozen at workflow start)."""
    for edge in edges or []:
        if edge.get("source") == self_id and edge.get("targetHandle") == _TOOL_HANDLE:
            target = edge.get("target")
            if target:
                logger.info(
                    "[agentBuilder] caller resolved (live canvas): self=%s -> agent=%s",
                    self_id,
                    target,
                )
                return target
    logger.info(
        "[agentBuilder] no input-tools edge found from %s; falling back to self as caller (canvas: %d nodes? edges=%d)",
        self_id,
        0,
        len(edges or []),
    )
    return self_id


def _find_wired_types(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    target_node_id: str,
    target_handle: str,
) -> Dict[str, str]:
    """Walk ``edges`` for entries arriving at ``target_node_id`` on
    ``target_handle`` and return ``{source_node_type: source_node_id}``
    for every match.

    Accepts explicit ``nodes`` / ``edges`` lists so callers can hand in
    a freshly-reloaded canvas (via :func:`_load_live_canvas`) instead
    of the per-tool activity's stale ``ctx`` snapshot. Without this the
    same agentBuilder run can't detect tools it added moments ago in
    the same iteration.
    """
    nodes_by_id = {n.get("id"): n for n in (nodes or []) if n.get("id")}
    wired: Dict[str, str] = {}
    for edge in edges or []:
        if edge.get("target") != target_node_id:
            continue
        if edge.get("targetHandle") != target_handle:
            continue
        source_id = edge.get("source")
        if not source_id:
            continue
        source_node = nodes_by_id.get(source_id)
        if source_node is None:
            continue
        source_type = source_node.get("type")
        if source_type:
            wired[source_type] = source_id
    return wired


def _catalogue_tools() -> List[Dict[str, Any]]:
    """Enrich :func:`_allowed_tool_types` with display_name + description
    so ``inspect_canvas`` can hand the LLM the FULL spawnable set with
    enough context to pick the right one (instead of guessing or
    waiting for an error-message hint capped at 10 examples)."""
    registry = registered_node_classes()
    catalogue: List[Dict[str, Any]] = []
    for ntype in sorted(_allowed_tool_types()):
        cls = registry.get(ntype)
        if cls is None:
            continue
        catalogue.append(
            {
                "type": ntype,
                "display_name": getattr(cls, "display_name", "") or ntype,
                "description": getattr(cls, "tool_description", "") or getattr(cls, "description", "") or "",
            }
        )
    return catalogue


def _catalogue_agents() -> List[Dict[str, Any]]:
    """Same shape as :func:`_catalogue_tools` for the agent registry —
    fed into ``inspect_canvas`` so ``add_subagent`` knows the full set
    of delegate-able agent types."""
    registry = registered_node_classes()
    catalogue: List[Dict[str, Any]] = []
    for ntype in sorted(_allowed_subagent_types()):
        cls = registry.get(ntype)
        if cls is None:
            continue
        catalogue.append(
            {
                "type": ntype,
                "display_name": getattr(cls, "display_name", "") or ntype,
                "description": getattr(cls, "description", "") or "",
            }
        )
    return catalogue


def _disabled_skill_folders() -> set[str]:
    """Skill-folder blocklist from node_allowlist.json. Top-level
    folder names whose every skill is hidden from the catalogue."""
    return set(_allowlist_config().get("disabled_skill_folders") or [])


def _catalogue_skills() -> List[Dict[str, Any]]:
    """Enumerate every skill the agentBuilder can enable via
    ``add_skill``. Reuses the existing :class:`SkillLoader` singleton
    (populated at startup) so we don't re-walk SKILL.md frontmatter
    on every ``inspect_canvas`` call. Honors ``disabled_skill_folders``
    from node_allowlist.json so the LLM-facing catalogue matches the
    UI palette's filter.
    """
    try:
        from services.skill_loader import get_skill_loader

        loader = get_skill_loader()
        registry = loader._registry  # SkillMetadata is the public type; the dict is a frozen-at-startup snapshot
    except Exception as exc:  # noqa: BLE001 — defensive: catalogue is best-effort
        logger.debug("[agentBuilder] skill catalogue load failed: %s", exc)
        return []
    blocked_folders = _disabled_skill_folders()
    catalogue: List[Dict[str, Any]] = []
    for name, meta in sorted(registry.items()):
        # SkillMetadata.path is the leaf SKILL.md directory; the
        # blocklist matches against any ancestor folder under
        # server/skills/ (e.g. "android_agent" blocks every skill
        # under server/skills/android_agent/).
        if meta.path is not None and blocked_folders:
            try:
                ancestors = {p.name for p in meta.path.parents}
                if ancestors & blocked_folders:
                    continue
            except Exception:  # noqa: BLE001
                pass
        folder = meta.path.name if meta.path is not None else name
        catalogue.append(
            {
                "folder": folder,
                "name": meta.name,
                "description": meta.description or "",
            }
        )
    return catalogue


def _key_params(node: Dict[str, Any]) -> Dict[str, Any]:
    params = (node.get("data") or {}).get("parameters") or {}
    return {k: params[k] for k in _KEY_PARAM_FIELDS if k in params}


def _resolve_caller(ctx: NodeContext) -> str:
    """Return the calling agent's node_id, or fall back to self.

    Convention: an agentBuilder is wired to ONE agent's input-tools
    handle. We find that edge and treat the agent as the caller.
    Falls back to ctx.node_id if no such edge exists (standalone Run
    or no agent wired yet) so the operations still produce something
    sensible instead of crashing.
    """
    self_id = ctx.node_id
    for edge in ctx.edges or []:
        if edge.get("source") == self_id and edge.get("targetHandle") == _TOOL_HANDLE:
            target = edge.get("target")
            if target:
                logger.info(
                    "[agentBuilder] caller resolved via input-tools edge: " "self=%s -> agent=%s",
                    self_id,
                    target,
                )
                return target
    logger.info(
        "[agentBuilder] no input-tools edge found from %s; " "falling back to self as caller (canvas: %d nodes, %d edges)",
        self_id,
        len(ctx.nodes or []),
        len(ctx.edges or []),
    )
    return self_id


def _log_op_entry(op: str, ctx: NodeContext, **fields: Any) -> None:
    """Single INFO line per operation invocation. Captures workflow_id,
    self_id, canvas size, and ctx.raw keys so a missing-canvas-data bug
    is visible immediately (e.g. ``nodes=0 edges=0 raw_keys=[...]``
    tells you exactly which plumbing layer dropped the canvas state).
    """
    nodes = ctx.nodes or []
    edges = ctx.edges or []
    extra = " ".join(f"{k}={v!r}" for k, v in fields.items() if v not in (None, ""))
    logger.info(
        "[agentBuilder.%s] workflow_id=%s self=%s nodes=%d edges=%d " "raw_keys=%s%s",
        op,
        ctx.workflow_id,
        ctx.node_id,
        len(nodes),
        len(edges),
        sorted((ctx.raw or {}).keys()),
        f" {extra}" if extra else "",
    )


# ----------------------------------------------------------------------------
# Params + Output schemas
# ----------------------------------------------------------------------------


class AgentBuilderParams(BaseModel):
    """Multi-op schema. The LLM picks ``operation``, then fills the
    fields whose ``displayOptions`` enable them for that op.
    """

    operation: Literal[
        "inspect_canvas",
        "add_tool",
        "add_skill",
        "add_subagent",
        "create_workflow",
    ] = Field(
        default="inspect_canvas",
        description=(
            "Which canvas-mutation to perform. Always call "
            "'inspect_canvas' first to see what's already wired before "
            "spawning new nodes."
        ),
    )

    # add_tool
    node_type: str = Field(
        default="",
        description=(
            "For add_tool: tool node type to spawn (e.g. 'httpRequest', "
            "'braveSearch'). Must be a registered node with "
            "component_kind='tool'."
        ),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_tool"]}}},
    )

    # add_skill
    skill_folder: str = Field(
        default="",
        description=("For add_skill: skill folder name under server/skills/** " "(e.g. 'http-request-skill', 'memory-skill')."),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_skill"]}}},
    )

    # add_subagent
    agent_type: str = Field(
        default="",
        description=(
            "For add_subagent: agent node type to spawn (e.g. "
            "'coding_agent', 'web_agent'). Must be component_kind='agent' "
            "and not a team-lead. Caller must itself be a team-lead "
            "(orchestrator_agent / ai_employee)."
        ),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_subagent"]}}},
    )

    # create_workflow
    workflow_name: str = Field(
        default="",
        description="For create_workflow: display name (non-empty).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )
    workflow_description: str = Field(
        default="",
        description="For create_workflow: optional one-line description.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class AgentBuilderOutput(BaseModel):
    operation: Optional[str] = None
    summary: Optional[str] = None
    operations: Optional[List[Dict[str, Any]]] = None
    # inspect_canvas extras
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    you: Optional[Dict[str, Any]] = None
    # Registry catalogues — populated by ``inspect_canvas`` so the LLM
    # sees the FULL spawnable set instead of guessing or waiting for an
    # error-message hint capped at 10 examples. Each entry carries
    # display_name + description so the model can pick the right one
    # in a single response.
    available_tools: Optional[List[Dict[str, Any]]] = None
    available_agents: Optional[List[Dict[str, Any]]] = None
    available_skills: Optional[List[Dict[str, Any]]] = None
    # create_workflow extras
    workflow_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------
# Node
# ----------------------------------------------------------------------------


class AgentBuilderNode(ToolNode):
    type = _AGENT_BUILDER_TYPE
    display_name = "Agent Builder"
    subtitle = "Runtime canvas-mutation tool"
    group = ("tool", "ai")
    description = (
        "Inspect the workflow canvas and mutate it at runtime: spawn "
        "tools, enable skills, add delegate agents, or create new "
        "workflows. Wire to an AI agent's input-tools handle; the "
        "agent calls operations through the standard tool path."
    )
    component_kind = "tool"
    # Operations walk ctx.edges to resolve the calling agent (input-tools
    # source) and mutate ctx.nodes — the F4.B AgentWorkflow tool-dispatch
    # path reads this flag to forward the parent workflow's canvas into
    # the per-tool activity context. Without it, _resolve_caller falls
    # back to self-as-caller and new nodes wire to agentBuilder instead
    # of the parent AI Agent.
    needs_canvas = True
    tool_name = "agent_builder"
    tool_description = (
        "Inspect and modify the workflow canvas at runtime. ALWAYS call "
        "inspect_canvas FIRST — its response includes the full catalogue "
        "of every tool, agent, and skill you can spawn (with display_name "
        "and description), plus the current canvas state. Operations: "
        "inspect_canvas (read current nodes/edges + available_tools / "
        "available_agents / available_skills catalogues), add_tool (spawn "
        "a tool node + wire it; idempotent if already wired), add_skill "
        "(enable a skill folder on a connected masterSkill; idempotent if "
        "already enabled), add_subagent (add a delegate agent; idempotent "
        "if already wired). NOTE: create_workflow is temporarily disabled "
        "— mutate the current workflow instead."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = AgentBuilderParams
    Output = AgentBuilderOutput

    # ---- inspect_canvas (read-only) ---------------------------------------

    @Operation("inspect_canvas")
    async def inspect_canvas(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("inspect_canvas", ctx)
        # Reload from DB so the snapshot reflects any in-run mutations
        # (per-tool ctx is frozen at MachinaWorkflow start, so without
        # this the LLM never sees nodes it added moments ago).
        nodes, edges = await _load_live_canvas(ctx)
        caller_id = _resolve_caller_from(ctx.node_id, edges)
        type_by_id = {n.get("id"): n.get("type") for n in nodes}

        node_summaries = [
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "label": (n.get("data") or {}).get("label") or n.get("type"),
                "key_params": _key_params(n),
            }
            for n in nodes
        ]
        edge_summaries = [
            {
                "source": e.get("source"),
                "target": e.get("target"),
                "source_handle": e.get("sourceHandle"),
                "target_handle": e.get("targetHandle"),
            }
            for e in edges
        ]
        incoming = [
            {
                "source_id": e.get("source"),
                "source_type": type_by_id.get(e.get("source")),
                "target_handle": e.get("targetHandle"),
            }
            for e in edges
            if e.get("target") == caller_id
        ]
        outgoing = [
            {
                "target_id": e.get("target"),
                "target_type": type_by_id.get(e.get("target")),
                "source_handle": e.get("sourceHandle"),
            }
            for e in edges
            if e.get("source") == caller_id
        ]

        tools = [c for c in incoming if c["target_handle"] == _TOOL_HANDLE]
        skills = [c for c in incoming if c["target_handle"] == _SKILL_HANDLE]
        teammates = [c for c in incoming if c["target_handle"] == _TEAMMATES_HANDLE]
        available_tools = _catalogue_tools()
        available_agents = _catalogue_agents()
        available_skills = _catalogue_skills()
        parts = [f"{len(nodes)} nodes"]
        if tools:
            types = ", ".join(sorted({c["source_type"] or "?" for c in tools}))
            parts.append(f"{len(tools)} tool(s) wired to you ({types})")
        if skills:
            parts.append(f"{len(skills)} skill source(s) wired")
        if teammates:
            parts.append(f"{len(teammates)} teammate(s)")
        parts.append(
            f"{len(available_tools)} tool / {len(available_agents)} agent / "
            f"{len(available_skills)} skill types available to spawn"
        )

        return AgentBuilderOutput(
            operation="inspect_canvas",
            summary=", ".join(parts) + ".",
            nodes=node_summaries,
            edges=edge_summaries,
            you={"node_id": caller_id, "incoming": incoming, "outgoing": outgoing},
            available_tools=available_tools,
            available_agents=available_agents,
            available_skills=available_skills,
        )

    # ---- add_tool ---------------------------------------------------------

    @Operation("add_tool")
    async def add_tool(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("add_tool", ctx, node_type=params.node_type)
        node_type = (params.node_type or "").strip()
        if not node_type:
            return AgentBuilderOutput(
                operation="add_tool",
                summary="add_tool: node_type is required.",
                operations=[],
            )
        allowed = _allowed_tool_types()
        if node_type not in allowed:
            all_types = ", ".join(sorted(allowed))
            return AgentBuilderOutput(
                operation="add_tool",
                summary=(
                    f"add_tool: '{node_type}' is not an allowed tool type. "
                    f"Allowed types: {all_types}. "
                    "Call inspect_canvas for the full catalogue with descriptions."
                ),
                operations=[],
            )

        # Reload the canvas from DB so duplicate detection sees any
        # tool the LLM just spawned earlier in THIS run. The ctx
        # snapshot is frozen at MachinaWorkflow start.
        live_nodes, live_edges = await _load_live_canvas(ctx)
        caller_id = _resolve_caller_from(ctx.node_id, live_edges)
        # Idempotent duplicate check — if this tool type is already wired
        # to the caller's input-tools handle, reuse the existing instance
        # rather than spawning another. Surfaces the existing node id in
        # the summary so the LLM knows it CAN call the tool.
        existing_tools = _find_wired_types(live_nodes, live_edges, caller_id, _TOOL_HANDLE)
        if node_type in existing_tools:
            return AgentBuilderOutput(
                operation="add_tool",
                summary=(
                    f"Tool '{node_type}' is already wired to you "
                    f"(node id={existing_tools[node_type]}). Reusing existing instance."
                ),
                operations=[],
            )
        client_ref = f"new_{node_type}"
        minted_id = _mint_node_id(node_type)
        add_node_op = workflow_ops.add_node(
            client_ref,
            node_type,
            {},
            label=node_type,
            position=workflow_ops.anchored(caller_id, offset_x=200, offset_y=80),
        )
        add_node_op["minted_id"] = minted_id  # FE applier adopts; BE rebind reads.
        ops = [
            add_node_op,
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                caller_id,
                source_handle=_TOOL_OUTPUT_HANDLE,
                target_handle=_TOOL_HANDLE,
            ),
        ]
        await _broadcast(ctx.workflow_id, caller_id, ops)
        return AgentBuilderOutput(
            operation="add_tool",
            summary=f"Added '{node_type}' as a tool. {_summary_suffix(ctx)}",
            operations=ops,
        )

    # ---- add_skill --------------------------------------------------------

    @Operation("add_skill")
    async def add_skill(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("add_skill", ctx, skill_folder=params.skill_folder)
        skill = (params.skill_folder or "").strip()
        if not skill:
            return AgentBuilderOutput(
                operation="add_skill",
                summary="add_skill: skill_folder is required.",
                operations=[],
            )
        if not _skill_folder_exists(skill):
            return AgentBuilderOutput(
                operation="add_skill",
                summary=f"add_skill: skill '{skill}' not found under server/skills.",
                operations=[],
            )

        # Reload from DB so we see any masterSkill spawned earlier in
        # the same run (or skill already toggled on in this run).
        nodes, edges = await _load_live_canvas(ctx)
        caller_id = _resolve_caller_from(ctx.node_id, edges)

        skill_edge = next(
            (e for e in edges if e.get("target") == caller_id and e.get("targetHandle") == _SKILL_HANDLE),
            None,
        )
        master_skill = None
        if skill_edge:
            master_skill = next(
                (n for n in nodes if n.get("id") == skill_edge.get("source") and n.get("type") == _MASTER_SKILL_TYPE),
                None,
            )

        if master_skill:
            existing = ((master_skill.get("data") or {}).get("parameters") or {}).get("skills_config") or {}
            # Idempotent: skill already enabled in this masterSkill's
            # config — no-op + tell the LLM the skill is already active
            # so it doesn't loop trying to enable.
            if existing.get(skill, {}).get("enabled"):
                return AgentBuilderOutput(
                    operation="add_skill",
                    summary=(
                        f"Skill '{skill}' is already enabled on your "
                        f"Master Skill (node id={master_skill['id']}). "
                        "No change needed."
                    ),
                    operations=[],
                )
            new_config = _toggle_skill(existing, skill, True)
            ops = [
                workflow_ops.set_node_parameters(
                    master_skill["id"],
                    {"skills_config": new_config},
                )
            ]
            await _broadcast(ctx.workflow_id, caller_id, ops)
            return AgentBuilderOutput(
                operation="add_skill",
                summary=f"Enabled '{skill}' skill. {_summary_suffix(ctx)}",
                operations=ops,
            )

        new_config = _toggle_skill(None, skill, True)
        client_ref = "new_master_skill"
        minted_id = _mint_node_id(_MASTER_SKILL_TYPE)
        master_skill_op = workflow_ops.add_node(
            client_ref,
            _MASTER_SKILL_TYPE,
            {"skills_config": new_config},
            label=_MASTER_SKILL_LABEL,
            position=workflow_ops.anchored(caller_id, offset_x=-60, offset_y=220),
        )
        master_skill_op["minted_id"] = minted_id
        ops = [
            master_skill_op,
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                caller_id,
                source_handle=_SKILL_OUTPUT_HANDLE,
                target_handle=_SKILL_HANDLE,
            ),
        ]
        await _broadcast(ctx.workflow_id, caller_id, ops)
        return AgentBuilderOutput(
            operation="add_skill",
            summary=f"Created Master Skill node and enabled '{skill}'. {_summary_suffix(ctx)}",
            operations=ops,
        )

    # ---- add_subagent -----------------------------------------------------

    @Operation("add_subagent")
    async def add_subagent(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("add_subagent", ctx, agent_type=params.agent_type)
        agent_type = (params.agent_type or "").strip()
        if not agent_type:
            return AgentBuilderOutput(
                operation="add_subagent",
                summary="add_subagent: agent_type is required.",
                operations=[],
            )

        # Reload from DB so duplicate detection sees teammates spawned
        # earlier in the same run.
        nodes, edges = await _load_live_canvas(ctx)
        caller_id = _resolve_caller_from(ctx.node_id, edges)
        caller = next((n for n in nodes if n.get("id") == caller_id), None)
        caller_type = (caller or {}).get("type") or ""

        if not _is_team_lead(caller_type):
            leads = ", ".join(sorted(_TEAM_LEAD_TYPES))
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(f"add_subagent: only team-lead agents ({leads}) can spawn " f"delegates. This agent is '{caller_type}'."),
                operations=[],
            )
        allowed = _allowed_subagent_types()
        if agent_type not in allowed:
            all_types = ", ".join(sorted(allowed))
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(
                    f"add_subagent: '{agent_type}' is not an allowed agent type. "
                    f"Allowed types: {all_types}. "
                    "Call inspect_canvas for the full catalogue with descriptions."
                ),
                operations=[],
            )
        if _is_team_lead(agent_type):
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(f"add_subagent: cannot spawn another team-lead " f"('{agent_type}'); pick a specialized agent instead."),
                operations=[],
            )

        # Idempotent duplicate check — see add_tool above for rationale.
        existing_teammates = _find_wired_types(nodes, edges, caller_id, _TEAMMATES_HANDLE)
        # Custom aiAgent nodes are intentionally repeatable: their delegation
        # identity is label/node based. Every other agent type has a stable
        # type-wide delegate name and is therefore one-per-team.
        if agent_type != "aiAgent" and agent_type in existing_teammates:
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(
                    f"Teammate '{agent_type}' is already wired to you "
                    f"(node id={existing_teammates[agent_type]}). Reusing existing instance."
                ),
                operations=[],
            )

        client_ref = f"new_{agent_type}"
        minted_id = _mint_node_id(agent_type)
        add_node_op = workflow_ops.add_node(
            client_ref,
            agent_type,
            {},
            label=agent_type,
            position=workflow_ops.anchored(caller_id, offset_x=300, offset_y=200),
        )
        add_node_op["minted_id"] = minted_id
        ops = [
            add_node_op,
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                caller_id,
                source_handle=_TEAMMATES_OUTPUT_HANDLE,
                target_handle=_TEAMMATES_HANDLE,
            ),
        ]
        await _broadcast(ctx.workflow_id, caller_id, ops)
        return AgentBuilderOutput(
            operation="add_subagent",
            summary=(f"Added '{agent_type}' as a teammate. {_summary_suffix(ctx)} " "(configure provider/model first)."),
            operations=ops,
        )

    # ---- create_workflow --------------------------------------------------

    @Operation("create_workflow")
    async def create_workflow(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry(
            "create_workflow",
            ctx,
            workflow_name=params.workflow_name,
            workflow_description=params.workflow_description,
        )
        # Temporary disable — flip ``_CREATE_WORKFLOW_ENABLED`` at the
        # module top to restore. The body below is intact so the
        # feature can be re-enabled without rewriting validation +
        # persistence logic.
        if not _CREATE_WORKFLOW_ENABLED:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary=(
                    "create_workflow is temporarily disabled. Mutate the "
                    "current workflow instead — add_tool / add_skill / "
                    "add_subagent are still available."
                ),
            )
        name = (params.workflow_name or "").strip()
        if not name:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary="create_workflow: workflow_name is required.",
            )

        from services.plugin.deps import get_database

        database = get_database()
        workflow_id = await database.allocate_workflow_id()
        slug = await next_available_slug(name, database)
        start_node_id = f"{workflow_id}:start:1"
        description = (params.workflow_description or "").strip()
        workflow_data = {
            "id": workflow_id,
            "name": name,
            "slug": slug,
            "description": description,
            "nodes": [
                {
                    "id": start_node_id,
                    "type": "start",
                    "position": {"x": 200, "y": 200},
                    "data": {"label": "Start"},
                }
            ],
            "edges": [],
            "nodeParameters": {},
        }

        ok = await database.save_workflow(
            workflow_id,
            name,
            slug,
            workflow_data,
            description=description or None,
        )
        if not ok:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary=f"create_workflow: failed to persist '{name}'.",
            )
        return AgentBuilderOutput(
            operation="create_workflow",
            summary=(f"Created workflow '{name}' (slug: {slug}). " "User can switch to it from the toast notification."),
            workflow_id=workflow_id,
        )
