"""Distributed credential compatibility checked before durable admission."""

from __future__ import annotations

from .onepassword import CredentialSourceError


async def assert_cluster_credentials(database, graph: dict, context: dict | None = None) -> None:
    from core.container import container
    from services.node_registry import get_node_class
    from services.plugin.credential import ApiKeyCredential, Credential

    auth = container.auth_service()
    if not auth.distributed_credentials:
        return
    context = context or {}
    nodes = {str(node.get("id")): node for node in graph.get("nodes", [])}
    target = context.get("node_id")
    selected = set(nodes)
    if target and str(target) in nodes:
        # Inspect only the capability's incoming tool/skill/model dependencies.
        # Outgoing teammate edges lead to unrelated employee capabilities.
        selected, pending = set(), [str(target)]
        handles = {"input-tools", "input-skill", "input-context", "input-memory", "input-teammates", "input-model", "input-llm"}
        links = [(str(edge.get("source")), str(edge.get("target"))) for edge in graph.get("edges", [])
                 if (edge.get("targetHandle") or edge.get("target_handle")) in handles]
        while pending:
            current = pending.pop()
            if current in selected:
                continue
            selected.add(current)
            pending.extend(left for left, right in links if right == current)
    for node_id in selected:
        node = nodes.get(node_id, {})
        node_type = str(node.get("type") or (node.get("data") or {}).get("type") or "")
        cls = get_node_class(node_type)
        if cls is None:
            continue
        data = dict(node.get("data") or {})
        params = await database.get_node_parameters(node_id) or {}
        data.update(params)
        if any(data.get(key) for key in ("api_key", "apiKey", "access_token", "refresh_token")):
            raise CredentialSourceError("unsupported_source", "Remove inline credentials and configure approved 1Password bindings before distributed execution.")
        # CLI login/session caches are machine-local even if an auth class is
        # absent. These backends cannot be approved by storing a token in op.
        if node_type in {"claude_code_agent", "codex_agent", "gemini_cli_agent", "openai_codex", "google_gemini"} or getattr(cls, "cli_backend", None):
            raise CredentialSourceError("unsupported_source", "CLI-managed login connections require local execution.")
        for cred in getattr(cls, "credentials", ()) or ():
            if not isinstance(cred, type) or not issubclass(cred, ApiKeyCredential):
                raise CredentialSourceError("unsupported_source", "This workflow contains an OAuth or machine-local connection without a distributed static-secret adapter.")
            if getattr(cred, "extra_fields", ()) and cred.id != "openai_compatible":
                raise CredentialSourceError("unsupported_source", "This connection needs additional credential fields without an audited distributed adapter.")
            if cred.validate.__func__ is not Credential.validate.__func__:
                # Named endpoint has an explicit audited enrollment adapter.
                if cred.id == "openai_compatible":
                    provider = str(data.get("endpoint") or "")
                else:
                    raise CredentialSourceError("unsupported_source", "This connection has no audited distributed static-secret adapter.")
            else:
                provider = cred.id
            if not provider or not await auth.get_credential_source(provider, principal=str(context.get("user_id") or "owner")):
                raise CredentialSourceError("missing_binding", "Configure a 1Password binding for every connection used by this task.")
