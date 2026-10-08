"""Authenticated 1Password enrollment, metadata and installation commands."""

from __future__ import annotations

from typing import Any

from core.container import container
from services.authz.ws_surface import execution_principal
from services.ws_handler_registry import current_load_options_principal, register_option_loader, ws_handler

from .onepassword import CredentialSourceError, doctor, read_secret, validate_reference
from .sources import CredentialSources, public_base_url


def _principal(websocket) -> str:
    if (getattr(websocket, "scope", {}) or {}).get("path") == "/ws/internal":
        raise CredentialSourceError("access_denied", "Credential enrollment requires an authenticated client.")
    return execution_principal({}, websocket)


@ws_handler()
async def handle_onepassword_status(data: dict, websocket) -> dict:
    _principal(websocket)
    auth = container.auth_service()
    try:
        result = await doctor(auth.settings)
    except CredentialSourceError as exc:
        result = {"available": False, "error": str(exc), "code": exc.code}
    return {**result, "sourceRequired": auth.distributed_credentials}


@ws_handler()
async def handle_onepassword_install(data: dict, websocket) -> dict:
    _principal(websocket)
    from .provision import ensure_cli
    await ensure_cli()
    return await handle_onepassword_status({}, websocket)


@ws_handler("provider", "reference", "base_url")
async def handle_onepassword_endpoint_validate(data: dict, websocket) -> dict:
    """The supported static-key adapter for named OpenAI-compatible endpoints."""
    import re
    from services.llm.endpoints import SERVER_META_KEY, resolve_base_url
    from services.status_broadcaster import get_status_broadcaster
    principal = _principal(websocket)
    provider = str(data["provider"]).lower()
    if not re.fullmatch(r"openai_compatible:[a-z0-9][a-z0-9_-]{0,63}", provider):
        raise CredentialSourceError("unsupported_provider", "Use a named OpenAI-compatible endpoint reference.")
    reference = validate_reference(data["reference"])
    url = public_base_url(data["base_url"])
    auth = container.auth_service()
    await auth.get_credential_source(provider, principal=principal)
    key = await read_secret(reference, auth.settings)
    try:
        outcome = await resolve_base_url(url, api_key=key)
    except Exception:
        raise CredentialSourceError("probe_failed", "The endpoint probe failed. Check the endpoint and API credential.") from None
    if not outcome.ok:
        return {"success": False, "valid": False, "error": "The endpoint did not return an authorized OpenAI model list."}
    models = [str(model.id) for model in outcome.models if getattr(model, "id", None)]
    if any(key in model for model in models):
        raise CredentialSourceError("unsafe_metadata", "The endpoint returned unsafe model metadata.")
    label = str(data.get("label") or provider.split(":", 1)[1])[:128]
    params = {SERVER_META_KEY: {"label": label, "base_url": outcome.base_url, "kind": "generic"}}
    await auth.store_credential_source(provider, reference, principal=principal, models=models, model_params=params, base_url=outcome.base_url)
    await get_status_broadcaster().update_api_key_status(provider, valid=True, has_key=True, message="1Password binding validated", models=models)
    return {"valid": True, "provider": provider, "models": models, "message": "1Password binding validated"}


@ws_handler()
async def handle_browser_credential_bindings_list(data: dict, websocket) -> dict:
    return {"bindings": await CredentialSources(container.auth_service().database).list_browser(_principal(websocket))}


async def _validate_resource_scopes(principal: str, data: dict, database) -> None:
    if data.get("profile_id"):
        from nodes.browser._profiles import ProfileStore
        await ProfileStore(database).get(principal, str(data["profile_id"]))
    if data.get("workflow_id"):
        saved = await database.get_workflow(str(data["workflow_id"]))
        graph = saved.data if saved is not None and hasattr(saved, "data") else (saved or {}).get("data", {})
        if not saved or str(graph.get("owner_id") or "owner") != principal:
            raise CredentialSourceError("access_denied", "Workflow credential scope access denied.")
    if data.get("employee_id"):
        from models.employees import Employee
        async with database.get_session() as session:
            employee = await session.get(Employee, str(data["employee_id"]))
        owner = getattr(employee, "owner_id", None) if employee else None
        if str(owner or "") != principal:
            raise CredentialSourceError("access_denied", "Employee credential scope access denied.")


@ws_handler("label", "origin", "username_reference", "password_reference", "success_path")
async def handle_browser_credential_binding_save(data: dict, websocket) -> dict:
    principal = _principal(websocket)
    auth = container.auth_service()
    await _validate_resource_scopes(principal, data, auth.database)
    # Saving a website binding never reads passwords or creates an auth grant.
    binding = await CredentialSources(auth.database).save_browser(principal, data)
    auth._bump_catalogue_version()
    return {"binding": binding}


@ws_handler("binding_id")
async def handle_browser_credential_binding_delete(data: dict, websocket) -> dict:
    await CredentialSources(container.auth_service().database).delete_browser(_principal(websocket), str(data["binding_id"]))
    return {}


async def browser_credential_bindings(params: dict[str, Any]) -> list[dict]:
    principal = current_load_options_principal()
    if not principal:
        return []
    bindings = await CredentialSources(container.auth_service().database).list_browser(principal)
    return [{"value": row["id"], "label": f'{row["label"]} ({row["origin"]})'} for row in bindings]


register_option_loader("browserCredentialBindings", browser_credential_bindings)
WS_HANDLERS = {
    "onepassword_status": handle_onepassword_status,
    "onepassword_install": handle_onepassword_install,
    "onepassword_endpoint_validate": handle_onepassword_endpoint_validate,
    "browser_credential_bindings_list": handle_browser_credential_bindings_list,
    "browser_credential_binding_save": handle_browser_credential_binding_save,
    "browser_credential_binding_delete": handle_browser_credential_binding_delete,
}
