"""HTTP endpoint for node output schemas.

Mirrors n8n's static-asset pattern: the editor fetches
``GET /api/schemas/nodes/{node_type}.json`` on demand (no auth required,
long-cacheable). See docs-internal/schema_source_of_truth_rfc.md for
the design rationale and docs-internal/schema_source_of_truth_rfc.md
for the frontend consumer (useNodeOutputSchemaQuery).
"""

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from nodes._visuals import get_plugin_icon_path
from services.node_input_schemas import (
    get_node_input_schema,
    list_node_types_with_input_schema,
)
from services.node_output_schemas import (
    get_node_output_schema,
    list_node_types_with_schema,
)
from services.node_spec import get_node_spec, list_node_groups, list_node_types_with_spec

router = APIRouter(prefix="/api/schemas", tags=["schemas"])

_LONG_CACHE = "public, max-age=86400"


@router.get("/nodes/{node_type}.json")
async def get_node_schema(node_type: str, response: Response):
    """Return the JSON Schema for a node type's runtime output.

    - 200 + JSON Schema: when a schema is declared.
    - 404: when no schema exists for the node type. Frontend falls back
      to real run data / empty state.

    Long cache: these schemas change only when the app ships a new
    release, so we set a 24h Cache-Control. nginx / CDN can cache too.
    """

    schema = get_node_output_schema(node_type)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"No schema for node type {node_type!r}")
    response.headers["Cache-Control"] = _LONG_CACHE
    return schema


@router.get("/nodes")
async def list_schemas():
    """List every node type that has a declared output schema. Editor
    uses this to know which types it can query without probing 404s."""

    return {"node_types": list_node_types_with_schema()}


@router.get("/nodes/{node_type}/input.json")
async def get_node_input(node_type: str, response: Response):
    """Return the JSON Schema for a node type's input parameters.

    Parallels the output endpoint above. 404 when no Pydantic model is
    registered for the type — the frontend's ``lib/nodeSpec.ts`` adapter
    treats a missing input schema as an empty parameter set.
    """

    schema = get_node_input_schema(node_type)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"No input schema for {node_type!r}")
    response.headers["Cache-Control"] = _LONG_CACHE
    return schema


@router.get("/nodes/{node_type}/spec.json")
async def get_node_spec_endpoint(node_type: str, response: Response):
    """Return the unified NodeSpec envelope (display metadata + input
    schema + output schema) for a node type.

    Wave 6 Phase 1: this is the single endpoint Phase 2's editor consumer
    talks to. 404 when neither an input model nor an output schema is
    registered (unknown type)."""

    spec = get_node_spec(node_type)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"No NodeSpec for {node_type!r}")
    response.headers["Cache-Control"] = _LONG_CACHE
    return spec


@router.get("/nodes/{node_type}/icon")
async def get_node_icon(
    node_type: str,
    variant: str = Query("light", pattern="^(light|dark)$"),
):
    """Return the plugin folder's co-located ``icon.svg``.

    Per RFC §6.5 — backend is the sole authority on plugin assets.
    The frontend resolver dispatches by wire format: URLs route to
    ``<img src=...>``; ``asset:<key>`` / ``lobehub:<brand>`` / emoji
    each have their own branch in ``client/src/assets/icons/index.ts``.

    - ``?variant=dark``: serve ``icon.dark.svg`` if present, fall back
      to ``icon.svg``.
    - 200: SVG bytes with ``image/svg+xml`` + long cache headers.
    - 404: no per-folder icon — frontend falls back to the bundled
      ``ICON_REGISTRY`` (Wave 11 legacy path) via the ``asset:<key>``
      string in ``visuals.json``.
    """

    path = get_plugin_icon_path(node_type, variant=variant)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No icon for {node_type!r}")
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Cache-Control": _LONG_CACHE},
    )


@router.get("/nodes/specs")
async def list_specs():
    """List every node type with at least an input model or an output
    schema. Editor uses this on boot to prefetch the full set."""

    return {"node_types": list_node_types_with_spec()}


@router.post("/nodes/options/{method}")
async def load_options(method: str, body: dict | None = None):
    """Wave 6 Phase 4: REST mirror of the load_options WS handler.

    Resolves a ``loadOptionsMethod`` string against the registry and
    returns the dynamic dropdown contents. Body is the per-method
    parameter map (e.g. ``{"group_id": "..."}`` for
    whatsappGroupMembers).
    """
    from services.ws_handler_registry import dispatch_load_options

    params = (body or {}).get("params", body or {}) if isinstance(body, dict) else {}
    options = await dispatch_load_options(method, params)
    return {"method": method, "options": options}


@router.get("/nodes/options")
async def list_load_options():
    """Registered loadOptionsMethod names. Editor probes this on boot
    to know which dynamic-option loaders are wired backend-side."""
    from services.ws_handler_registry import list_load_options_methods

    return {"methods": list_load_options_methods()}


@router.get("/nodes/groups")
async def get_node_groups(response: Response):
    """Wave 6 Phase 5: {group_name: [node_type, ...]} index derived from
    every NodeSpec's ``group`` array. Replaces the 34 hand-rolled
    ``*_NODE_TYPES`` arrays scattered across the frontend - palette
    filters, console sink detection, tool-capability checks, etc all
    read from one TanStack Query against this endpoint."""

    response.headers["Cache-Control"] = _LONG_CACHE
    return {"groups": list_node_groups()}
