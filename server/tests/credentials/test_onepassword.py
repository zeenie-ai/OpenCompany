"""Canaries for private resolution, saved metadata and scoped website bindings."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.credential_sources import BrowserCredentialBinding, CredentialSource
from services.auth import AuthService
from services.credentials import onepassword
from services.credentials.onepassword import CredentialSourceError
from services.credentials.sources import CredentialSources, exact_origin, public_base_url

REF = "op://" + "a" * 26 + "/" + "b" * 26 + "/password"
USER_REF = REF.replace("password", "username")
CANARY = "  密碼-secret-CANARY\n "


class Process:
    def __init__(self, output: bytes, error: bytes = b"", code: int = 0):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.stdout.feed_eof()
        self.stderr.feed_data(error)
        self.stderr.feed_eof()
        self.returncode = code

    async def wait(self):
        return self.returncode


@pytest_asyncio.fixture
async def source_auth():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: SQLModel.metadata.create_all(conn, tables=[CredentialSource.__table__, BrowserCredentialBinding.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_session():
        async with factory() as session:
            yield session

    database = SimpleNamespace(get_session=get_session, engine=engine)
    local = SimpleNamespace(get_api_key=AsyncMock(return_value="old-local-key"), delete_api_key=AsyncMock(), get_api_key_models=AsyncMock(return_value=["old-model"]), get_api_key_model_params=AsyncMock(return_value={}), list_api_keys=AsyncMock(return_value=[]), list_key_scopes=AsyncMock(return_value=[]))
    auth = AuthService(local, None, database, SimpleNamespace(distributed_mode=False))
    try:
        yield auth
    finally:
        await engine.dispose()


def test_references_are_unambiguous_and_cannot_add_cli_options():
    assert onepassword.validate_reference(REF) == REF
    for bad in ("op://named-vault/item/password", REF + "?attribute=otp", REF + "\n--out-file /tmp/leak", REF.replace("op://", "--")):
        with pytest.raises(CredentialSourceError):
            onepassword.validate_reference(bad)


def test_origin_and_public_endpoint_reject_credentials():
    assert exact_origin("https://EXAMPLE.com:443/") == "https://example.com"
    assert exact_origin("http://[::1]:8000") == "http://[::1]:8000"
    assert public_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    for bad in ("https://user:password@example.com", "https://example.com/?secret=123", "https://example.com/path", "https://example.com/#token"):
        with pytest.raises(CredentialSourceError):
            exact_origin(bad)


def test_auth_modes_exclude_connect_and_ambient_secrets(monkeypatch):
    for name, value in {"OP_CONNECT_HOST": "https://connect", "OP_CONNECT_TOKEN": "connect-canary", "OP_SERVICE_ACCOUNT_TOKEN": "bootstrap-canary", "OP_SESSION_bad": "session-canary", "OPENAI_API_KEY": "api-canary"}.items():
        monkeypatch.setenv(name, value)
    desktop = onepassword.resolver_environment(SimpleNamespace(onepassword_auth_mode="desktop", onepassword_account="work", distributed_mode=False))
    assert desktop["OP_ACCOUNT"] == "work"
    assert desktop["OP_BIOMETRIC_UNLOCK_ENABLED"] == "true"
    assert not any(name in desktop for name in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_SESSION_bad", "OPENAI_API_KEY"))
    service = onepassword.resolver_environment(SimpleNamespace(onepassword_auth_mode="service_account", distributed_mode=True))
    assert service["OP_SERVICE_ACCOUNT_TOKEN"] == "bootstrap-canary"
    assert "OP_CONNECT_HOST" not in service and "OP_CONNECT_TOKEN" not in service


async def test_reader_preserves_exact_value_and_fixed_argument_vector(monkeypatch):
    called = []
    async def spawn(executable, *args, **kwargs):
        called.append((executable, args, kwargs))
        return Process(CANARY.encode())
    monkeypatch.setattr(onepassword, "cli_path", lambda settings: "/approved/op")
    monkeypatch.setattr(onepassword, "doctor", AsyncMock(return_value={}))
    monkeypatch.setattr(onepassword.asyncio, "create_subprocess_exec", spawn)
    result = await onepassword.read_secret(REF, SimpleNamespace(onepassword_auth_mode="desktop"))
    assert result == CANARY
    assert called[0][1] == ("read", "--no-newline", REF)
    assert called[0][2]["stdin"] == asyncio.subprocess.DEVNULL
    assert CANARY not in str(called)


async def test_raw_cli_error_and_secret_output_are_not_exposed(monkeypatch, caplog):
    monkeypatch.setattr(onepassword.asyncio, "create_subprocess_exec", AsyncMock(return_value=Process(CANARY.encode(), ("429 rate limit " + CANARY).encode(), 1)))
    with pytest.raises(CredentialSourceError) as failure:
        await onepassword._private_command("/approved/op", ("read", "--no-newline", REF), {}, 1)
    assert failure.value.code == "rate_limited"
    assert CANARY not in str(failure.value) and CANARY not in caplog.text


async def test_pipe_size_limit_terminates_process(monkeypatch):
    process = Process(b"x" * 65537)
    process.returncode = None
    def terminate():
        process.returncode = -1
    process.terminate = terminate
    monkeypatch.setattr(onepassword.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(CredentialSourceError) as failure:
        await onepassword._private_command("/approved/op", ("read", "--no-newline", REF), {}, 1)
    assert failure.value.code == "invalid_value"
    assert process.returncode == -1


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_terminate_and_wait(monkeypatch, cancel):
    process = Process(b"")
    process.stdout = asyncio.StreamReader()  # deliberately never reaches EOF
    process.returncode = None
    waited = []
    def terminate():
        process.returncode = -1
    async def wait():
        waited.append(True)
        return process.returncode
    process.terminate, process.wait = terminate, wait
    monkeypatch.setattr(onepassword.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(onepassword._private_command("/approved/op", ("read",), {}, .01))
    if cancel:
        await asyncio.sleep(0)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else CredentialSourceError):
        await task
    assert process.returncode == -1 and waited


async def test_sources_never_resolve_in_forms_models_or_existence_checks(source_auth, monkeypatch):
    await source_auth.store_credential_source("openai", REF, principal="owner", models=["gpt-test"], model_params={"gpt-test": {"context_length": 1000}})
    reader = AsyncMock(return_value=CANARY)
    monkeypatch.setattr(onepassword, "read_secret", reader)
    assert await source_auth.get_api_key("openai") is None
    assert await source_auth.has_valid_key("openai")
    assert await source_auth.get_stored_models("openai") == ["gpt-test"]
    assert (await source_auth.get_model_params("openai"))["gpt-test"]["context_length"] == 1000
    assert (await source_auth.get_credential_source("openai"))["reference"] == REF
    reader.assert_not_awaited()
    assert await source_auth.resolve_api_key("openai") == CANARY
    assert await source_auth.resolve_api_key("openai") == CANARY
    assert reader.await_count == 2  # no decrypted value cache
    assert not source_auth._api_key_cache
    source_auth.credentials_db.get_api_key.assert_not_awaited()


async def test_cluster_rejects_fallback_and_handles_default_endpoint_metadata(source_auth):
    source_auth.settings.distributed_mode = True
    with pytest.raises(CredentialSourceError):
        await source_auth.resolve_api_key("openai")
    with pytest.raises(CredentialSourceError):
        await source_auth.store_api_key("openai", "inline", [])
    with pytest.raises(CredentialSourceError):
        await source_auth.get_oauth_tokens("google")
    source_auth.credentials_db.get_api_key.assert_not_awaited()
    await source_auth.store_credential_source("openai", REF, principal="owner")
    assert await source_auth.resolve_api_key("openai_proxy") is None
    await source_auth.store_credential_source("openai_compatible:lab", REF, principal="owner", base_url="https://models.example.com/v1")
    assert await source_auth.resolve_api_key("openai_compatible:lab_proxy") == "https://models.example.com/v1"


async def test_source_permissions_precede_resolution(source_auth, monkeypatch):
    await source_auth.store_credential_source("openai", REF, principal="alice")
    reader = AsyncMock(return_value=CANARY)
    monkeypatch.setattr(onepassword, "read_secret", reader)
    with pytest.raises(CredentialSourceError):
        await source_auth.resolve_api_key("openai", principal="bob")
    with pytest.raises(CredentialSourceError):
        await source_auth.resolve_api_key("openai")
    with pytest.raises(CredentialSourceError):
        await source_auth.remove_api_key("openai", principal="bob")
    reader.assert_not_awaited()


async def test_shared_catalogue_metadata_is_scoped_without_resolving(source_auth, monkeypatch):
    from services.llm.endpoints import list_endpoints
    await source_auth.store_credential_source("openai_compatible:alice", REF, principal="alice", models=["alice-model"], model_params={"_endpoint": {"label": "Alice", "base_url": "https://alice.example/v1"}}, base_url="https://alice.example/v1")
    reader = AsyncMock(return_value=CANARY)
    monkeypatch.setattr(onepassword, "read_secret", reader)
    assert await source_auth.has_valid_key("openai_compatible:alice", principal="bob") is False
    assert await source_auth.get_stored_models("openai_compatible:alice", principal="bob") == []
    assert await source_auth.get_model_params("openai_compatible:alice", principal="bob") == {}
    assert await source_auth.list_api_key_providers(principal="bob") == []
    assert await list_endpoints(source_auth, principal="bob") == []
    endpoints = await list_endpoints(source_auth, principal="alice")
    assert len(endpoints) == 1 and endpoints[0].label == "Alice" and endpoints[0].models == ["alice-model"]
    reader.assert_not_awaited()


async def test_api_revocation_while_authorizing_does_not_return_old_value(source_auth, monkeypatch):
    await source_auth.store_credential_source("openai", REF, principal="owner")
    async def read(reference, settings):
        await source_auth._sources().delete("openai", principal="owner")
        return CANARY
    monkeypatch.setattr(onepassword, "read_secret", read)
    with pytest.raises(CredentialSourceError) as failure:
        await source_auth.resolve_api_key("openai")
    assert failure.value.code == "binding_changed"
    assert CANARY not in str(failure.value)


async def test_explicit_disconnect_does_not_uncover_a_shadowed_local_key(source_auth):
    await source_auth.store_credential_source("openai", REF, principal="owner")
    await source_auth.remove_api_key("openai", principal="owner")
    source_auth.credentials_db.delete_api_key.assert_awaited_once_with("openai", "default")
    assert await source_auth.get_credential_source("openai") is None


async def test_website_binding_scopes_and_opaque_metadata(source_auth, monkeypatch):
    store = CredentialSources(source_auth.database)
    metadata = await store.save_browser("alice", {"label": "Test", "origin": "https://example.com", "success_path": "/account", "username_reference": USER_REF, "password_reference": REF, "profile_id": "profile", "workflow_id": "workflow", "employee_id": "employee"})
    assert "reference" not in str(metadata)
    assert (await store.list_browser("alice")) == [metadata]
    reader = AsyncMock(side_effect=["user", CANARY])
    monkeypatch.setattr(onepassword, "read_secret", reader)
    for scope in ({"principal": "bob"}, {"principal": "alice", "profile_id": "other"}, {"principal": "alice", "profile_id": "profile", "workflow_id": "workflow", "employee_id": "other"}):
        with pytest.raises(CredentialSourceError):
            await source_auth.resolve_browser_credentials(metadata["id"], **scope)
    reader.assert_not_awaited()
    resolved = await source_auth.resolve_browser_credentials(metadata["id"], "alice", profile_id="profile", workflow_id="workflow", employee_id="employee")
    assert resolved["username"] == "user" and resolved["password"] == CANARY
    assert CANARY not in str(await store.list_browser("alice"))


async def test_revocation_during_desktop_authorization_prevents_fill(source_auth, monkeypatch):
    store = CredentialSources(source_auth.database)
    saved = await store.save_browser("owner", {"label": "Test", "origin": "https://example.com", "success_path": "/account", "username_reference": USER_REF, "password_reference": REF})
    async def read(reference, settings):
        if reference == REF:
            await store.delete_browser("owner", saved["id"])
        return CANARY
    monkeypatch.setattr(onepassword, "read_secret", read)
    with pytest.raises(CredentialSourceError):
        await source_auth.resolve_browser_credentials(saved["id"], "owner")


async def test_api_validation_persists_only_reference_and_never_echoed_secrets(source_auth, monkeypatch, caplog):
    from core.container import container
    from services.credentials import handlers
    from services.plugin.credential import ApiKeyCredential, ProbeResult

    class CanaryCredential(ApiKeyCredential):
        id = "onepassword_canary_test"
        @classmethod
        async def _probe(cls, key):
            assert key == CANARY
            return ProbeResult(valid=True, models=["safe-model"])

    broadcaster = SimpleNamespace(update_api_key_status=AsyncMock())
    monkeypatch.setattr(container, "auth_service", lambda: source_auth)
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    monkeypatch.setattr(onepassword, "read_secret", AsyncMock(return_value=CANARY))
    socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
    result = await handlers.handle_validate_api_key({"provider": CanaryCredential.id, "credential_source": "onepassword", "reference": REF, "_principal": "attacker"}, socket)
    assert result["valid"]
    assert (await source_auth.get_credential_source(CanaryCredential.id, principal="owner"))["reference"] == REF
    assert CANARY not in str(result) and CANARY not in str(broadcaster.update_api_key_status.await_args)
    assert CANARY not in caplog.text

    async def echo(cls, key):
        return ProbeResult(valid=True, models=[key])
    monkeypatch.setattr(CanaryCredential, "_probe", classmethod(echo))
    result = await handlers.handle_validate_api_key({"provider": CanaryCredential.id, "credential_source": "onepassword", "reference": REF}, socket)
    assert not result["valid"]
    assert CANARY not in str(result)
    assert await source_auth.get_stored_models(CanaryCredential.id) == ["safe-model"]


async def test_api_form_reads_reference_metadata_without_secret_resolution(source_auth, monkeypatch):
    from core.container import container
    from services.credentials import handlers
    await source_auth.store_credential_source("openai", REF, principal="owner", models=["model"])
    monkeypatch.setattr(container, "auth_service", lambda: source_auth)
    reader = AsyncMock(return_value=CANARY)
    monkeypatch.setattr(onepassword, "read_secret", reader)
    socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
    result = await handlers.handle_get_stored_api_key({"provider": "openai"}, socket)
    assert result["hasKey"] is True and "apiKey" not in result
    assert result["credentialSource"]["reference"] == REF
    reader.assert_not_awaited()


def test_signature_verification_uses_fixed_windows_script_and_private_path(monkeypatch):
    from pathlib import Path
    from services.credentials import provision
    calls = []
    monkeypatch.setattr(provision.platform, "system", lambda: "Windows")
    monkeypatch.setattr(provision.shutil, "which", lambda name: "powershell")
    monkeypatch.setattr(provision, "_run", lambda args, env=None: calls.append((args, env)))
    path = Path("C:/Program Files/op ' $(unsafe).exe")
    provision.verify_publisher(path)
    arguments, environment = calls[0]
    assert str(path) not in arguments[-1]
    assert environment == {"OPENCOMPANY_SIGNATURE_TARGET": str(path)}
    assert "Get-AuthenticodeSignature" in arguments[-1] and "Valid" in arguments[-1]


@pytest.mark.parametrize("subject,approved", [
    ("CN=Agilebits, O=Agilebits, L=Toronto, S=Ontario, C=CA", True),
    ("CN=1Password, O=1Password Inc., C=CA", True),
    ("CN=AgileBits, O=AgileBits Inc, C=CA", True),
    ("CN=Agilebits, O=Someone Else, C=CA", False),
    ("CN=Unknown, O=Agilebits Malware Inc, C=CA", False),
    ("CN=Unknown, O=Fake 1Password Inc., C=CA", False),
])
def test_windows_signature_organization_is_exact(monkeypatch, subject, approved):
    import re
    from pathlib import Path
    from services.credentials import provision
    calls = []
    monkeypatch.setattr(provision.platform, "system", lambda: "Windows")
    monkeypatch.setattr(provision.shutil, "which", lambda name: "powershell")
    monkeypatch.setattr(provision, "_run", lambda args, env=None: calls.append(args))
    provision.verify_publisher(Path("op.exe"))
    pattern = calls[0][-1].split("-notmatch '")[1].split("'")[0]
    assert bool(re.search(pattern, subject, re.IGNORECASE)) is approved


async def test_doctor_verifies_integrity_before_version_without_bootstrap(monkeypatch):
    from services.credentials import provision
    events = []
    monkeypatch.setattr(onepassword, "cli_path", lambda settings: "/approved/op")
    monkeypatch.setattr(provision, "verify_install", lambda path: events.append("verified"))
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "bootstrap-canary")
    async def private(executable, args, env, timeout):
        events.append("version")
        assert args == ("--version",)
        assert "OP_SERVICE_ACCOUNT_TOKEN" not in env
        return b"2.40.0\n"
    monkeypatch.setattr(onepassword, "_private_command", private)
    assert (await onepassword.doctor(SimpleNamespace(onepassword_auth_mode="desktop")))["available"]
    assert events == ["verified", "version"]


def test_signature_subprocess_has_native_module_path_without_bootstrap(monkeypatch):
    from services.credentials import provision
    calls = []
    monkeypatch.setenv("PSModulePath", "incompatible-shell-modules")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "bootstrap-canary")
    monkeypatch.setattr(provision.subprocess, "run", lambda args, **kwargs: calls.append(kwargs) or SimpleNamespace(returncode=0))
    provision._run(["powershell", "-NoProfile", "-Command", "verified"], {"op_connect_token": "override-canary", "OPENCOMPANY_SIGNATURE_TARGET": "op.exe"})
    env = calls[0]["env"]
    assert "PSModulePath" not in env
    assert not any(key.upper().startswith("OP_") for key in env)
    assert env["OPENCOMPANY_SIGNATURE_TARGET"] == "op.exe"


async def test_catalogue_since_changes_with_shared_binding_and_keeps_principal_scope(source_auth, monkeypatch):
    from services import credential_registry
    import routers.websocket as websocket
    await source_auth.store_credential_source("openai", REF, principal="alice", models=["model-a"])
    registry = SimpleNamespace(
        get_live_version=lambda: "fixed-replica-local-version",
        get_catalogue=lambda: {"providers": [{"id": "openai", "kind": "apiKey"}], "categories": []},
        # No card templates: no custom connector cards (nodes/mcp).
        get_template=lambda provider_id: None,
    )
    monkeypatch.setattr(credential_registry, "get_credential_registry", lambda: registry)
    monkeypatch.setattr(websocket.container, "auth_service", lambda: source_auth)
    state = AsyncMock(wraps=credential_registry.provider_connection_state)
    monkeypatch.setattr(credential_registry, "provider_connection_state", state)
    reader = AsyncMock(side_effect=AssertionError("Catalogue reads must never resolve secrets"))
    monkeypatch.setattr(onepassword, "read_secret", reader)
    alice = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="alice"))
    first = await websocket.handle_get_credential_catalogue({}, alice)
    assert first["providers"][0]["stored"] is True
    assert await websocket.handle_get_credential_catalogue({"since": first["version"]}, alice) == {"success": True, "unchanged": True, "version": first["version"]}
    assert state.await_count == 1

    # Another replica writes the shared row without bumping this process's
    # registry version or broadcasting a local cache invalidation.
    await source_auth._sources().save("openai", USER_REF, owner_id="alice", models=["model-b"])
    refreshed = await websocket.handle_get_credential_catalogue({"since": first["version"]}, alice)
    assert "unchanged" not in refreshed and refreshed["version"] != first["version"]
    assert refreshed["providers"][0]["stored"] is True
    assert all(call.kwargs["principal"] == "alice" for call in state.await_args_list)

    bob = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="bob"))
    foreign = await websocket.handle_get_credential_catalogue({"since": refreshed["version"]}, bob)
    assert foreign["version"] != refreshed["version"]
    assert foreign["providers"][0]["stored"] is False
    assert state.await_args.kwargs["principal"] == "bob"
    reader.assert_not_awaited()
