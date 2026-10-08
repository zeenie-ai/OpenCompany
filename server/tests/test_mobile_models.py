"""Global/default/custom model precedence, without provider requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.plugin import NodeContext, NodeUserError
from nodes.mobile._node import MobileParams, resolve_model


@pytest.fixture
def models(monkeypatch):
    import services.plugin.deps as deps
    import services.node_registry as registry
    import constants
    database = SimpleNamespace(
        get_node_parameters=AsyncMock(return_value={}),
        get_user_settings=AsyncMock(return_value={"default_llm_provider": "gemini", "default_llm_model": "global-vision"}),
        get_provider_defaults=AsyncMock(return_value={"default_model": "provider-default"}),
    )
    auth = SimpleNamespace(resolve_api_key=AsyncMock(return_value="selected-key"))
    auth.resolve_api_key.side_effect = lambda provider, *_, **kw: None if provider.endswith("_proxy") else auth.resolve_api_key.return_value
    monkeypatch.setattr(deps, "get_database", lambda: database)
    monkeypatch.setattr(deps, "get_ai_service", lambda: SimpleNamespace(auth=auth))
    monkeypatch.setattr(registry, "get_node_class", lambda _: SimpleNamespace(component_kind="model"))
    monkeypatch.setattr(constants, "detect_ai_provider", lambda *_: "anthropic")
    return database, auth


def context():
    return NodeContext(node_id="phone", node_type="android_tool", workflow_id="wf", execution_id="run")


async def test_global_selection_is_read_fresh_and_ignores_drag_defaults(models):
    db, auth = models
    params = MobileParams(provider="openrouter", model="old-drag-default")
    result = await resolve_model(context(), params)
    assert result == {"provider": "google", "model": "global-vision", "model_env": {"GOOGLE_API_KEY": "selected-key", "GOOGLE_GENAI_USE_VERTEXAI": "false"}}
    db.get_user_settings.assert_awaited_once_with("default")
    auth.resolve_api_key.assert_awaited_once_with("gemini", "default", principal=None)
    db.get_user_settings.return_value = {"default_llm_provider": "openai", "default_llm_model": "new-global"}
    assert (await resolve_model(context(), params))["model"] == "new-global"


async def test_custom_selection_does_not_read_global(models):
    db, _ = models
    result = await resolve_model(context(), MobileParams(model_source="custom", provider="openai", model="custom-vision"))
    assert result["provider"] == "openai"
    assert result["model"] == "custom-vision"
    db.get_user_settings.assert_not_awaited()


async def test_connected_model_overrides_global_and_custom(models):
    db, _ = models
    db.get_node_parameters.return_value = {"model": "connected-vision", "api_key": "do-not-use"}
    ctx = context()
    ctx.nodes = [{"id": "model", "type": "anthropicChatModel"}]
    ctx.edges = [{"source": "model", "target": "phone", "targetHandle": "input-model"}]
    result = await resolve_model(ctx, MobileParams(model_source="custom", provider="openai", model="custom"))
    assert result == {"provider": "anthropic", "model": "connected-vision", "model_env": {"ANTHROPIC_API_KEY": "selected-key"}}
    db.get_user_settings.assert_not_awaited()


async def test_disabled_connector_does_not_silently_fallback(models):
    ctx = context()
    ctx.nodes = [{"id": "model", "type": "anthropicChatModel", "data": {"disabled": True}}]
    ctx.edges = [{"source": "model", "target": "phone", "targetHandle": "input-model"}]
    with pytest.raises(NodeUserError, match="enabled"):
        await resolve_model(ctx)


@pytest.mark.parametrize("settings,match", [({}, "Choose a global"), ({"default_llm_provider": "ollama", "default_llm_model": "local"}, "not supported")])
async def test_missing_or_unsupported_global_is_actionable(models, settings, match):
    db, auth = models
    db.get_user_settings.return_value = settings
    with pytest.raises(NodeUserError, match=match):
        await resolve_model(context(), MobileParams())
    auth.resolve_api_key.assert_not_awaited()


async def test_missing_key_is_actionable(models):
    _, auth = models
    auth.resolve_api_key.return_value = None
    with pytest.raises(NodeUserError, match="Settings"):
        await resolve_model(context(), MobileParams())


async def test_blank_custom_model_uses_provider_default(models):
    result = await resolve_model(context(), MobileParams(model_source="custom", provider="openai"))
    assert result["model"] == "provider-default"


async def test_private_runtime_resolution_uses_trusted_execution_principal(models):
    _, auth = models
    ctx = context()
    ctx.raw = {"user_id": "alice"}
    await resolve_model(ctx, MobileParams(model_source="custom", provider="openai", model="vision"))
    assert [call.args[0] for call in auth.resolve_api_key.await_args_list] == ["openai", "openai_proxy"]
    assert all(call.kwargs["principal"] == "alice" for call in auth.resolve_api_key.await_args_list)


@pytest.mark.parametrize("key,mode", [("AQ.synthetic-express-key", "true"), ("AIza-synthetic-developer-key", "false")])
@pytest.mark.parametrize("source", ["global", "custom", "connected"])
async def test_gemini_backend_matches_saved_key_type(models, monkeypatch, key, mode, source):
    import constants
    db, auth = models
    auth.resolve_api_key.return_value = key
    ctx = context()
    params = MobileParams()
    if source == "custom":
        params = MobileParams(model_source="custom", provider="gemini", model="chosen-model")
    elif source == "connected":
        monkeypatch.setattr(constants, "detect_ai_provider", lambda *_: "gemini")
        db.get_node_parameters.return_value = {"model": "chosen-model"}
        ctx.nodes = [{"id": "model", "type": "geminiChatModel"}]
        ctx.edges = [{"source": "model", "target": "phone", "targetHandle": "input-model"}]
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false" if mode == "true" else "true")
    result = await resolve_model(ctx, params)
    assert result["provider"] == "google"
    assert result["model_env"] == {"GOOGLE_API_KEY": key, "GOOGLE_GENAI_USE_VERTEXAI": mode}


@pytest.mark.parametrize("provider,key_var,url_var", [("openai", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
                                                     ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")])
@pytest.mark.parametrize("source", ["global", "custom", "connected"])
@pytest.mark.parametrize("endpoint", [None, "https://model-relay.example/custom/v1"])
async def test_openai_and_claude_use_selected_model_credentials_and_endpoint(models, monkeypatch, provider, key_var, url_var, source, endpoint):
    import constants
    db, auth = models
    db.get_user_settings.return_value = {"default_llm_provider": provider, "default_llm_model": "selected-vision-model"}
    auth.resolve_api_key.side_effect = lambda name, *_, **kw: endpoint if name == provider + "_proxy" else "selected-key"
    ctx = context()
    params = MobileParams()
    if source == "custom":
        params = MobileParams(model_source="custom", provider=provider, model="selected-vision-model")
    elif source == "connected":
        monkeypatch.setattr(constants, "detect_ai_provider", lambda *_: provider)
        db.get_node_parameters.return_value = {"model": "selected-vision-model"}
        ctx.nodes = [{"id": "model", "type": provider + "ChatModel"}]
        ctx.edges = [{"source": "model", "target": "phone", "targetHandle": "input-model"}]
    result = await resolve_model(ctx, params)
    expected_env = {key_var: "selected-key"}
    if endpoint:
        expected_env[url_var] = endpoint
    assert result == {"provider": provider, "model": "selected-vision-model", "model_env": expected_env}
