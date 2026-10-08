"""Static providers resolve privately in their calling runtime, with identity."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.credentials.onepassword import CredentialSourceError
from services.plugin import NodeContext


def runtime_auth(monkeypatch, key="runtime-key"):
    from services.plugin import deps
    auth = SimpleNamespace(
        resolve_api_key=AsyncMock(return_value=key),
        get_api_key=AsyncMock(side_effect=AssertionError("Legacy form getter is not a runtime resolver")),
        require_local_credentials=MagicMock(),
    )
    monkeypatch.setattr(deps, "get_auth_service", lambda: auth)
    monkeypatch.setattr(deps, "get_ai_service", lambda: SimpleNamespace(auth=auth))
    return auth


async def test_apify_activity_passes_trusted_principal_to_private_resolution(monkeypatch):
    from nodes.scraper.apify_actor import ApifyActorNode, ApifyActorParams
    auth = runtime_auth(monkeypatch)
    actor = SimpleNamespace(call=AsyncMock(return_value={"id": "run", "status": "SUCCEEDED"}))
    sdk = MagicMock(return_value=SimpleNamespace(actor=lambda name: actor))
    monkeypatch.setitem(sys.modules, "apify_client", SimpleNamespace(ApifyClientAsync=sdk))
    ctx = NodeContext(node_id="scraper", node_type="apifyActor", raw={"user_id": "alice"})
    result = await ApifyActorNode().run(ctx, ApifyActorParams(actor_id="apify/instagram-scraper"))
    assert result.status == "SUCCEEDED"
    auth.resolve_api_key.assert_awaited_once_with("apify", "default", principal="alice")
    sdk.assert_called_once_with("runtime-key")
    auth.get_api_key.assert_not_awaited()


async def test_vertex_admin_resolves_gemini_and_does_not_hide_revocation(monkeypatch):
    from nodes.agent import vertex_agent_admin as admin
    auth = runtime_auth(monkeypatch, key="AIza-runtime-key")
    build = MagicMock(return_value="private-client")
    monkeypatch.setattr(admin, "build_genai_client", build)
    ctx = NodeContext(node_id="admin", node_type="vertex_agent_admin", raw={"user_id": "alice"})
    params = admin.VertexAgentAdminParams(operation="list")
    assert await admin.VertexAgentAdminNode()._client(params, ctx) == "private-client"
    auth.resolve_api_key.assert_awaited_once_with("gemini", "default", principal="alice")
    auth.resolve_api_key.side_effect = CredentialSourceError("resolution_failed", "Approved reference unavailable.")
    with pytest.raises(CredentialSourceError, match="unavailable"):
        await admin.VertexAgentAdminNode()._client(params, ctx)
    assert build.call_count == 1
    auth.get_api_key.assert_not_awaited()


def test_vertex_adc_is_a_local_only_connection(monkeypatch):
    from nodes.agent import _vertex
    auth = runtime_auth(monkeypatch)
    auth.require_local_credentials.side_effect = CredentialSourceError("unsupported_source", "ADC requires local execution.")
    with pytest.raises(CredentialSourceError, match="local execution"):
        _vertex.build_genai_client("", "gcp-project")


async def test_live_voice_resolution_is_scoped_and_revocation_does_not_use_static_fallback(monkeypatch):
    from nodes.speech import _option_loaders, _unifier
    from services import ws_handler_registry
    auth = runtime_auth(monkeypatch)
    monkeypatch.setattr(ws_handler_registry, "current_load_options_principal", lambda: "alice")
    voice = SimpleNamespace(as_option=lambda: {"value": "voice", "label": "Approved voice"})
    voices = AsyncMock(return_value=[voice])
    monkeypatch.setattr(_unifier, "list_voices", voices)
    assert await _option_loaders.load_speech_voices({"provider": "elevenlabs"}) == [voice.as_option()]
    auth.resolve_api_key.assert_awaited_once_with("elevenlabs", principal="alice")
    auth.resolve_api_key.side_effect = CredentialSourceError("resolution_failed", "Approved reference unavailable.")
    with pytest.raises(CredentialSourceError):
        await _option_loaders.load_speech_voices({"provider": "elevenlabs"})
    assert voices.await_count == 1


async def test_dictation_status_reads_metadata_and_never_resolves(monkeypatch):
    from core.container import container
    from nodes.speech import _handlers
    auth = runtime_auth(monkeypatch)
    auth.has_valid_key = AsyncMock(side_effect=lambda provider, **kwargs: provider == "groq")
    monkeypatch.setattr(container, "auth_service", lambda: auth)
    assert await _handlers.dictation_provider(principal="alice") == "groq"
    assert all(call.kwargs["principal"] == "alice" for call in auth.has_valid_key.await_args_list)
    auth.resolve_api_key.assert_not_awaited()
    auth.get_api_key.assert_not_awaited()


async def test_employee_connections_scope_provider_and_endpoint_metadata(monkeypatch):
    from services.employees import connections
    from services.llm import endpoints
    auth = object()
    state = AsyncMock(return_value={"stored": True, "connected": True})
    listing = AsyncMock(return_value=[])
    registry = SimpleNamespace(get_provider=lambda provider: {"id": provider})
    monkeypatch.setattr(connections, "get_credential_registry", lambda: registry)
    monkeypatch.setattr(connections, "provider_connection_state", state)
    monkeypatch.setattr(endpoints, "list_endpoints", listing)
    current = connections.Connections(auth, principal="alice")
    await current.ai_providers()
    await current.state("openai")
    assert all(call.kwargs["principal"] == "alice" for call in state.await_args_list)
    assert sum(call.args[0]["id"] == "openai" for call in state.await_args_list) == 1
    listing.assert_awaited_once_with(auth, principal="alice")


async def test_employee_setup_resolves_with_its_owner_without_legacy_fallback(monkeypatch):
    from services.employees.llm import LLMChoice, employees_chat
    auth = runtime_auth(monkeypatch)
    chat = AsyncMock(return_value="safe-result")
    assert await employees_chat(SimpleNamespace(chat=chat), auth, LLMChoice(provider="openai", model="gpt-test", local=False), [], principal="alice") == "safe-result"
    auth.resolve_api_key.assert_awaited_once_with("openai", principal="alice")
    assert chat.await_args.kwargs["api_key"] == "runtime-key"
    auth.get_api_key.assert_not_awaited()


async def test_additional_credential_fields_require_an_explicit_adapter(monkeypatch):
    from core.container import container
    from services import credential_registry
    from services.credentials import onepassword
    from services.plugin.credential import ApiKeyCredential

    class AdditionalSecretCredential(ApiKeyCredential):
        id = "additional_secret_canary"
        extra_fields = ("additional-secret",)

    reader = AsyncMock(side_effect=AssertionError("Unsupported enrollment must not read secrets"))
    monkeypatch.setattr(onepassword, "read_secret", reader)
    monkeypatch.setattr(container, "auth_service", lambda: SimpleNamespace(distributed_credentials=True))
    state = await credential_registry.provider_connection_state({"id": AdditionalSecretCredential.id, "kind": "apiKey"}, container.auth_service(), principal="alice")
    assert not state["sourceSupported"] and not state["connected"]
    result = await AdditionalSecretCredential.validate({"credential_source": "onepassword", "reference": "unused", "_principal": "alice"})
    assert result["code"] == "unsupported_source" and not result["valid"]
    reader.assert_not_awaited()
