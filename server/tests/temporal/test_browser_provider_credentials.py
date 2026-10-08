"""Provider secrets resolve under the calling principal at the Activity boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.temporal.agent_activities import _resolve_activity_api_key


@pytest.mark.parametrize("distributed", [False, True])
async def test_runtime_resolution_uses_calling_principal_and_never_legacy_key_reader(monkeypatch, distributed):
    auth = SimpleNamespace(distributed_credentials=distributed,
        resolve_api_key=AsyncMock(return_value="PRIVATE-CANARY"),
        get_api_key=AsyncMock(side_effect=AssertionError("Cannot read a cached credential")))
    database = SimpleNamespace(get_node_parameters=AsyncMock(side_effect=AssertionError("Cannot use inline fallback")))
    monkeypatch.setattr("core.container.container", SimpleNamespace(auth_service=lambda: auth, database=lambda: database))
    payload = {"provider": "openai", "user_id": "tenant-a", "node_id": "browser-agent", "workflow_id": "wf"}
    assert await _resolve_activity_api_key(payload) == "PRIVATE-CANARY"
    auth.resolve_api_key.assert_awaited_once_with("openai", principal="tenant-a")
    auth.get_api_key.assert_not_awaited()
    database.get_node_parameters.assert_not_awaited()
    assert "PRIVATE-CANARY" not in repr(payload)


async def test_revoked_binding_does_not_fall_back_to_a_local_inline_key(monkeypatch):
    from services.credentials.onepassword import CredentialSourceError
    auth = SimpleNamespace(distributed_credentials=False,
        resolve_api_key=AsyncMock(side_effect=CredentialSourceError("revoked", "The credential is unavailable.")),
        get_api_key=AsyncMock())
    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={"api_key": "INLINE-CANARY"}))
    monkeypatch.setattr("core.container.container", SimpleNamespace(auth_service=lambda: auth, database=lambda: database))
    with pytest.raises(CredentialSourceError, match="unavailable"):
        await _resolve_activity_api_key({"provider": "openai", "user_id": "tenant-a", "node_id": "agent"})
    database.get_node_parameters.assert_not_awaited()
