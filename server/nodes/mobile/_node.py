"""Mobile-use agent: one connector and one broker-owned Android device."""

from datetime import timedelta
import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, Field
from services.plugin import ActionNode, NodeContext, Operation, NodeUserError
from services.plugin.scaling import RetryPolicy, TaskQueue


class MobileParams(BaseModel):
    prompt: str = Field(default="", max_length=20000, json_schema_extra={"rows": 4})
    model_source: Literal["global", "custom"] = Field(
        default="global", title="Model selection",
        description="Use the global model or choose one for this phone. A connected Model node always takes priority.",
    )
    provider: str = Field(
        default="openai", title="AI provider",
        json_schema_extra={"enum": ["openai", "anthropic", "gemini"], "displayOptions": {"show": {"model_source": ["custom"]}}},
    )
    model: str = Field(default="", title="Model", json_schema_extra={
        "placeholder": "Choose a model", "displayOptions": {"show": {"model_source": ["custom"]}},
    })
    max_steps: int = Field(default=40, ge=1, le=200)
    timeout_s: int = Field(default=900, ge=30, le=3600)


class MobileOutput(BaseModel):
    response: Any = None
    outcome: str = "completed"
    run_id: str
    artifacts: list = Field(default_factory=list)


def task_identity(ctx: NodeContext) -> str:
    # Direct Workspace invocations already carry a principal/workflow/node UUID
    # hash used by the status/cancel endpoints. Graph and delegated executions
    # share their parent execution ID, so include the node and tool call too.
    if (ctx.execution_id or "").startswith("node-invoke-"):
        return ctx.execution_id
    identity = [ctx.user_id, ctx.workflow_id, ctx.execution_id, ctx.node_id, ctx.raw.get("tool_call_id")]
    return "mobile-task-" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


async def require_mobile_owner(principal: str) -> None:
    from core.container import container
    from constants import OWNER_PRINCIPAL_ID

    settings = container.settings()
    if settings.deployment_mode != "local":
        raise NodeUserError("Mobile Workspace requires a local installation with an embedded worker")
    if str(settings.vite_auth_enabled).lower() == "false" and principal == OWNER_PRINCIPAL_ID:
        return
    try:
        user = await container.user_auth_service().get_user_by_id(int(principal))
    except (ValueError, TypeError):
        user = None
    if user is None or not user.is_active or not user.is_owner:
        raise NodeUserError("The shared mobile device is available to the installation owner only")


async def resolve_model(ctx: NodeContext, params: MobileParams | None = None) -> dict:
    from services.plugin.deps import get_ai_service, get_database
    from services.llm.config import get_default_model_async
    from services.node_registry import get_node_class
    from services.plugin.edge_walker import edge_target_handle
    from constants import detect_ai_provider

    connections = [edge for edge in ctx.edges if edge.get("target") == ctx.node_id and edge_target_handle(edge) == "input-model"]
    if len(connections) > 1:
        raise NodeUserError("Connect exactly one AI model to this phone node's Model input, or remove connections to use the global model")
    database = get_database()
    if connections:
        source = next((node for node in ctx.nodes if node.get("id") == connections[0].get("source")), None)
        cls = get_node_class(source.get("type", "")) if source else None
        if cls is None or cls.component_kind != "model" or (source.get("data") or {}).get("disabled"):
            raise NodeUserError("Connect an enabled AI model node to this phone node")
        parameters = await database.get_node_parameters(source["id"]) or {}
        provider_ref = detect_ai_provider(source["type"], parameters)
        selected_model = parameters.get("model")
    else:
        config = params or MobileParams.model_validate(await database.get_node_parameters(ctx.node_id) or {})
        if config.model_source == "custom":
            provider_ref, selected_model = config.provider, config.model
        else:
            # The toolbar and Settings handlers persist the installation-wide
            # selection in the default settings row, separate from auth identity.
            settings = await database.get_user_settings("default") or {}
            provider_ref = settings.get("default_llm_provider")
            selected_model = settings.get("default_llm_model")
            if not provider_ref or not selected_model:
                raise NodeUserError("Choose a global AI model in the toolbar, select Custom in this phone's Model selection, or connect a Model node")
    adapters = {
        "openai": ("openai", "OPENAI_API_KEY"),
        "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
        "gemini": ("google", "GOOGLE_API_KEY"),
    }
    if provider_ref not in adapters:
        raise NodeUserError(f"The selected provider '{provider_ref}' is not supported by the phone engine. Choose an OpenAI, Anthropic, or Gemini model in the global selector or this phone's settings")
    auth = get_ai_service().auth
    key = await auth.resolve_api_key(provider_ref, "default", principal=ctx.raw.get("user_id"))
    if not key:
        raise NodeUserError("Connect the selected model provider in Settings before running Mobile")
    model = selected_model or await get_default_model_async(provider_ref, database)
    provider, variable = adapters[provider_ref]
    model_env = {variable: key}
    endpoint_variables = {"openai": "OPENAI_BASE_URL", "anthropic": "ANTHROPIC_BASE_URL"}
    if provider_ref in endpoint_variables:
        from services.llm.endpoints import base_url_key

        endpoint = await auth.resolve_api_key(base_url_key(provider_ref), "default", principal=ctx.raw.get("user_id"))
        if endpoint:
            # Use the same saved endpoint as the native provider, unchanged.
            # It may contain credentials, so it travels only through private IPC.
            model_env[endpoint_variables[provider_ref]] = endpoint
    if provider_ref == "gemini":
        from services.llm.vertex import is_vertex_express_key

        # Match native Gemini routing; do not inherit a host's backend mode.
        model_env["GOOGLE_GENAI_USE_VERTEXAI"] = "true" if is_vertex_express_key(key) else "false"
    return {"provider": provider, "model": model, "model_env": model_env}


class MobileUseAgent(ActionNode):
    type = "mobile_use_agent"
    display_name = "Mobile Agent"
    description = "Run tasks on the shared Android device with mobile-use"
    group = ("agent",)
    component_kind = "agent"
    supports_delegation = True
    requires_context = True
    needs_canvas = True
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Task", "role": "main"},
        {"name": "input-model", "kind": "input", "position": "bottom", "label": "Model", "role": "model"},
        {"name": "input-context", "kind": "input", "position": "left", "offset": "65%", "label": "Context", "role": "context"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
        {"name": "output-top", "kind": "output", "position": "top", "label": "Delegate", "role": "main"},
    )
    workspace_task = True
    ui_hints = {"workspace": {"kind": "mobile"}, "width": 300, "height": 200}
    task_queue = TaskQueue.ANDROID
    retry_policy = RetryPolicy(maximum_attempts=1)
    # Allow shared-phone queueing and manual-control waits before the bounded task.
    start_to_close_timeout = timedelta(hours=3)
    Params = MobileParams
    Output = MobileOutput

    @classmethod
    async def reset_execution_state(cls, *, node_id: str, workflow_id: str, execution_id: str,
                                    generation: int, graph: dict, database: Any) -> dict:
        from ._runtime import peek_runtime

        runtime = peek_runtime()
        if runtime is None:
            return {"reset": False}
        return await runtime.reset_execution_state(workflow_id=workflow_id, node_id=node_id, generation=generation)

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: MobileParams) -> dict:
        from ._runtime import get_runtime
        from ._control import MobileError

        await require_mobile_owner(ctx.user_id)
        if not ctx.workflow_id or not ctx.execution_id:
            raise NodeUserError("Mobile tasks require a saved workflow and execution identity")
        if not params.prompt.strip():
            raise NodeUserError("Enter a task for the mobile agent")
        runtime = get_runtime()
        model = await resolve_model(ctx, params)
        config = params.model_dump()
        directive = (ctx.raw.get("_raw_parameters") or {}).get("system_message")
        if directive and directive != config["prompt"]:
            config["prompt"] = f"{directive}\n\n{config['prompt']}"
        try:
            broker_url = await runtime.ensure_broker()
            raw_generation = ctx.raw.get("generation", 0)
            generation = raw_generation if isinstance(raw_generation, int) and not isinstance(raw_generation, bool) else 0
            if generation > 0:
                from services.plugin.deps import get_database

                control = await get_database().get_latest_workflow_control(ctx.workflow_id)
                if control is None or control.generation != generation or control.status in {"resetting", "reset"}:
                    raise NodeUserError("This mobile task belongs to an inactive workflow generation")
            return await runtime.run(
                principal=ctx.user_id,
                workflow_id=ctx.workflow_id,
                node_id=ctx.node_id,
                run_id=task_identity(ctx),
                execution_id=ctx.execution_id,
                generation=generation,
                params=config,
                model=model,
                broker_url=broker_url,
            )
        except MobileError as exc:
            raise NodeUserError(str(exc)) from None
