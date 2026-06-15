"""Node Executor - Single node execution with handler dispatch.

Uses a registry pattern for clean handler dispatch without if-else chains.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, Callable, TYPE_CHECKING

from core.logging import get_logger
from constants import (
    ANDROID_SERVICE_NODE_TYPES,
    AI_MODEL_TYPES,
    GOOGLE_MAPS_TYPES,
    detect_ai_provider,
)
from pydantic import ValidationError
from services.node_registry import get_node_class
# Wave 11.D.13 sunset: every handler that was imported here is now
# either (a) called lazily from a plugin's execute_op / execute method,
# or (b) retired entirely. The dispatcher itself only needs the
# plugin-class registry populated via register_node side effects.
# Triggering that population is what ``import nodes`` does at server
# startup (see main.py lifespan).

if TYPE_CHECKING:
    from core.config import Settings
    from core.database import Database
    from services.ai import AIService
    from nodes.location._service import MapsService
    from services.text import TextService
    from nodes.android._dispatcher import AndroidService

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """Standardized execution result."""

    success: bool
    node_id: str
    node_type: str
    result: Optional[Dict] = None
    error: Optional[str] = None
    execution_id: str = ""
    execution_time: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "success": self.success,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "execution_id": self.execution_id,
            "execution_time": self.execution_time,
            "timestamp": self.timestamp or datetime.now().isoformat(),
        }
        if self.success:
            d["result"] = self.result or {}
        else:
            d["error"] = self.error
        return d


class NodeExecutor:
    """Executes individual workflow nodes using registry-based dispatch."""

    def __init__(
        self,
        database: "Database",
        ai_service: "AIService",
        maps_service: "MapsService",
        text_service: "TextService",
        android_service: "AndroidService",
        settings: "Settings",
        output_store: Optional[Callable] = None,
    ):
        self.database = database
        self.ai_service = ai_service
        self.maps_service = maps_service
        self.text_service = text_service
        self.android_service = android_service
        self.settings = settings
        self._output_store = output_store
        self._handlers = self._build_handler_registry()

    def _build_handler_registry(self) -> Dict[str, Callable]:
        """Build handler registry with service dependencies bound via partial.

        Plugin handlers registered via ``services.node_registry.register_node``
        win over the legacy hardcoded entries below; this lets per-node
        plugin modules in ``server/nodes/*.py`` override the dispatcher
        without touching this file. Plugin handlers are closed over their
        own dependencies at module import time, so no ``partial`` wiring
        is needed here.
        """
        from services.node_registry import _HANDLER_REGISTRY as _PLUGIN_HANDLERS

        registry = {
            # Workflow control
            # start / cronScheduler / timer — migrated to nodes/{workflow,scheduler}/*.py (Wave 11.C).
            # AI agents — all migrated to nodes/agent/*.py (Wave 11.C).
            # Plugin handlers win via registry.update(_PLUGIN_HANDLERS) merge below.
            # simpleMemory + masterSkill: migrated to nodes/skill/*.py (Wave 11.C).
            # Maps — all 3 migrated to nodes/location/*.py (Wave 11.C).
            # All other node types migrated to plugin classes under nodes/*/.
            # Plugin handlers register themselves into _PLUGIN_HANDLERS via
            # BaseNode.__init_subclass__ at import time. The merge below is
            # now the sole population of the registry.
        }

        # AI chat models — all 9 migrated to nodes/model/*.py (Wave 11.C).
        # Plugin handlers register themselves via _PLUGIN_HANDLERS merge below.

        # Android services — all 16 migrated to nodes/android/*.py (Wave 11.C).
        # Plugin handlers register themselves via _PLUGIN_HANDLERS merge.

        # Plugin handlers last: they win over the legacy entries above,
        # enabling incremental migration (Wave 10.C strangler fig).
        registry.update(_PLUGIN_HANDLERS)

        return registry

    async def execute(
        self,
        node_id: str,
        node_type: str,
        parameters: Dict[str, Any],
        context: Dict[str, Any],
        resolve_params_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Execute a single workflow node."""
        start_time = time.time()
        session_id = context.get("session_id", "default")
        execution_id = context.get("execution_id") or str(uuid.uuid4())[:8]

        try:
            # Load, validate, enhance parameters
            params = await self._prepare_parameters(node_id, node_type, parameters, session_id)

            # Resolve templates if resolver provided
            nodes = context.get("nodes")
            edges = context.get("edges")
            logger.debug(
                f"[NodeExecutor] Template resolution check: resolve_fn={resolve_params_fn is not None}, nodes={len(nodes) if nodes else 'None'}, edges={len(edges) if edges else 'None'}"
            )

            if resolve_params_fn and nodes is not None and edges is not None:
                logger.debug(f"[NodeExecutor] Before resolution: params={list(params.keys())}")
                params = await resolve_params_fn(params, node_id, nodes, edges, session_id)
                logger.debug(f"[NodeExecutor] After resolution: params keys={list(params.keys())}")

            # Build handler context
            handler_ctx = {
                **context,
                "start_time": start_time,
                "execution_id": execution_id,
            }
            logger.info("NodeExecutor context", node_id=node_id, workflow_id=context.get("workflow_id"))

            # Execute via registry or special handlers
            result = await self._dispatch(node_id, node_type, params, handler_ctx)
            result["execution_id"] = execution_id

            # Store output if successful
            if result.get("success") and self._output_store:
                output_data = result.get("result", {})

                # For Android service nodes, extract the nested 'data' field for cleaner template access
                # This allows {{batterymonitor.battery_level}} instead of {{batterymonitor.data.battery_level}}
                if node_type in ANDROID_SERVICE_NODE_TYPES and isinstance(output_data, dict):
                    # Flatten: promote 'data' contents to top level while preserving metadata
                    nested_data = output_data.get("data", {})
                    if isinstance(nested_data, dict):
                        # Merge nested data with metadata (service_id, action, timestamp, etc.)
                        output_data = {**output_data, **nested_data}
                        logger.debug(f"[NodeExecutor] Flattened Android output for {node_id}: keys={list(output_data.keys())}")

                # For socialReceive, store 4 outputs for different handle connections
                # - output_message: Text for LLM input
                # - output_media: Media data
                # - output_contact: Sender/contact info
                # - output_metadata: Message metadata
                if node_type == "socialReceive" and isinstance(output_data, dict):
                    await self._output_store(session_id, node_id, "output_message", output_data.get("message", ""))
                    await self._output_store(session_id, node_id, "output_media", output_data.get("media", {}))
                    await self._output_store(session_id, node_id, "output_contact", output_data.get("contact", {}))
                    await self._output_store(session_id, node_id, "output_metadata", output_data.get("metadata", {}))

                # Store with multiple keys for different handle IDs used by frontend components:
                # - output_main: SquareNode, GenericNode, TriggerNode, StartNode
                # - output_top: AIAgentNode (agents use top output handle)
                # - output_0: backward compatibility
                await self._output_store(session_id, node_id, "output_main", output_data)
                await self._output_store(session_id, node_id, "output_top", output_data)
                await self._output_store(session_id, node_id, "output_0", output_data)

            return result

        except asyncio.CancelledError:
            return ExecutionResult(
                False, node_id, node_type, error="Cancelled", execution_id=execution_id, execution_time=time.time() - start_time
            ).to_dict()
        except Exception as e:
            logger.error("Node execution error", node_id=node_id, error=str(e))
            return ExecutionResult(
                False, node_id, node_type, error=str(e), execution_id=execution_id, execution_time=time.time() - start_time
            ).to_dict()

    async def _prepare_parameters(self, node_id: str, node_type: str, params: Dict, session_id: str) -> Dict:
        """Load from DB, validate, inject API keys."""
        # Merge with DB parameters (DB provides defaults, frontend can override)
        db_params = await self.database.get_node_parameters(node_id) or {}
        merged = {**db_params, **params} if params else db_params

        # Validate via plugin Params (snake_case, plugin-only path).
        node_cls = get_node_class(node_type)
        params_model = getattr(node_cls, "Params", None) if node_cls else None
        if params_model is not None:
            try:
                validated = params_model.model_validate(merged)
                merged = {**merged, **validated.model_dump(exclude_unset=True)}
            except ValidationError as e:
                logger.warning("Validation warning", node_type=node_type, errors=str(e))

        # Inject API keys
        return await self._inject_api_keys(node_type, merged)

    async def _inject_api_keys(self, node_type: str, params: Dict) -> Dict:
        """Auto-inject API keys for AI and Maps nodes."""
        result = params.copy()

        if node_type in AI_MODEL_TYPES:
            provider = detect_ai_provider(node_type, params)
            if not result.get("api_key"):
                key = await self.ai_service.auth.get_api_key(provider, "default")
                if key:
                    result["api_key"] = key
            if not result.get("model"):
                models = await self.ai_service.auth.get_stored_models(provider, "default")
                if models:
                    result["model"] = models[0]

        elif node_type in GOOGLE_MAPS_TYPES:
            if not result.get("api_key"):
                # Try database first, then fall back to environment variable
                key = await self.ai_service.auth.get_api_key("google_maps", "default")
                if key:
                    result["api_key"] = key
                elif self.settings.google_maps_api_key:
                    result["api_key"] = self.settings.google_maps_api_key

        return result

    # Node types that need outputs from connected upstream nodes.
    # Computed once so plugin handlers (registered via @register_node)
    # can read context['connected_outputs'] / context['source_nodes']
    # without re-implementing the graph walk.
    _NEEDS_CONNECTED_OUTPUTS = frozenset(
        {
            "pythonExecutor",
            "montyExecutor",
            "javascriptExecutor",
            "typescriptExecutor",
            "webhookResponse",
            "console",
            "socialReceive",
        }
    )

    async def _dispatch(self, node_id: str, node_type: str, params: Dict, context: Dict) -> Dict:
        """Dispatch to handler from registry or special handlers."""

        # Pre-enrich context for nodes that consume upstream outputs.
        # Both plugin handlers and legacy handlers can read these keys.
        if node_type in self._NEEDS_CONNECTED_OUTPUTS:
            outputs, source_nodes = await self._get_connected_outputs_with_info(context, node_id)
            context = {**context, "connected_outputs": outputs, "source_nodes": source_nodes}

        # Check registry first (plugin handlers win — they were merged in
        # via _build_handler_registry's registry.update(_PLUGIN_HANDLERS)).
        handler = self._handlers.get(node_type)
        if handler:
            return await handler(node_id, node_type, params, context)

        # All trigger + special node types are now plugin classes
        # (Wave 11.C). Trigger nodes live in _handlers via their
        # plugin's register_node side effect and have already been
        # handled above. Anything that falls through here is a
        # workflow node type the backend genuinely doesn't know about.
        return {
            "success": True,
            "node_id": node_id,
            "node_type": node_type,
            "result": {"message": f"Node {node_id} executed", "parameters": params},
            "execution_time": time.time() - context.get("start_time", time.time()),
            "timestamp": datetime.now().isoformat(),
        }

    async def _get_connected_outputs(self, context: Dict, node_id: str) -> Dict[str, Any]:
        """Get outputs from connected upstream nodes with handle-aware routing."""
        get_output = context.get("get_output_fn")
        if not get_output:
            return {}

        nodes = context.get("nodes", [])
        edges = context.get("edges", [])
        session_id = context.get("session_id", "default")
        result = {}

        for edge in edges:
            if edge.get("target") == node_id:
                source_id = edge.get("source")
                source_handle = edge.get("sourceHandle")

                # Map sourceHandle to output key
                if source_handle and source_handle.startswith("output-"):
                    handle_name = source_handle.replace("output-", "")
                    output_key = f"output_{handle_name}"
                else:
                    output_key = "output_0"

                output = await get_output(session_id, source_id, output_key)

                # Fallback to output_0 if specific handle output not found
                if output is None and output_key != "output_0":
                    output = await get_output(session_id, source_id, "output_0")

                if output:
                    source = next((n for n in nodes if n.get("id") == source_id), {})
                    result[source.get("type", "unknown")] = output

        return result

    def _get_source_nodes_info(self, context: Dict, node_id: str) -> list:
        """Get source node info (id, type, label) for edges targeting this node.

        This is used for display purposes (e.g., showing source in Console panel).
        Does NOT filter by output availability - just returns edge source info.
        """
        nodes = context.get("nodes", [])
        edges = context.get("edges", [])
        source_nodes = []

        for edge in edges:
            if edge.get("target") == node_id:
                source_id = edge.get("source")
                source = next((n for n in nodes if n.get("id") == source_id), {})
                source_type = source.get("type", "unknown")
                source_data = source.get("data", {})
                source_label = source_data.get("label") or source_type
                source_nodes.append({"id": source_id, "type": source_type, "label": source_label})

        return source_nodes

    async def _get_connected_outputs_with_info(self, context: Dict, node_id: str) -> tuple:
        """Get outputs from connected upstream nodes with source node info.

        Returns:
            Tuple of (outputs dict, source_nodes list with id/type/label info)
        """
        get_output = context.get("get_output_fn")
        if not get_output:
            logger.warning(f"[_get_connected_outputs_with_info] No get_output_fn in context for {node_id}")
            return {}, []

        nodes = context.get("nodes", [])
        edges = context.get("edges", [])
        session_id = context.get("session_id", "default")
        outputs = {}
        source_nodes = []

        logger.debug(f"[_get_connected_outputs_with_info] node_id={node_id}, edges={len(edges)}, session={session_id}")

        for edge in edges:
            logger.debug(
                f"[_get_connected_outputs_with_info] Checking edge: source={edge.get('source')}, target={edge.get('target')}, sourceHandle={edge.get('sourceHandle')}, targetHandle={edge.get('targetHandle')}"
            )
            if edge.get("target") == node_id:
                source_id = edge.get("source")
                source_handle = edge.get("sourceHandle")

                # Map sourceHandle to output key
                # Frontend uses "output-<name>", backend stores as "output_<name>"
                if source_handle and source_handle.startswith("output-"):
                    handle_name = source_handle.replace("output-", "")
                    output_key = f"output_{handle_name}"
                else:
                    output_key = "output_0"

                logger.debug(
                    f"[_get_connected_outputs_with_info] Found edge from {source_id} to {node_id}, sourceHandle={source_handle}, using output_key={output_key}"
                )
                output = await get_output(session_id, source_id, output_key)
                logger.debug(f"[_get_connected_outputs_with_info] First lookup (key={output_key}): {'FOUND' if output else 'NOT_FOUND'}")

                # Fallback to output_0 if specific handle output not found
                if output is None and output_key != "output_0":
                    logger.debug(f"[_get_connected_outputs_with_info] output_key {output_key} not found, falling back to output_0")
                    output = await get_output(session_id, source_id, "output_0")
                    logger.debug(f"[_get_connected_outputs_with_info] Fallback lookup (output_0): {'FOUND' if output else 'NOT_FOUND'}")

                logger.debug(f"[_get_connected_outputs_with_info] Final output from {source_id}: {'FOUND' if output else 'NOT FOUND'}")
                if output:
                    source = next((n for n in nodes if n.get("id") == source_id), {})
                    source_type = source.get("type", "unknown")
                    outputs[source_type] = output
                    # Get label from node data if available
                    source_data = source.get("data", {})
                    source_label = source_data.get("label") or source_type
                    source_nodes.append({"id": source_id, "type": source_type, "label": source_label})

        logger.debug(f"[_get_connected_outputs_with_info] Returning {len(outputs)} outputs, {len(source_nodes)} source_nodes")
        return outputs, source_nodes
