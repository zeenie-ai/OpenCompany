"""Email service - credential resolution + HimalayaService orchestration.

All defaults and constants loaded from config/email_providers.json.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Set

from core.logging import get_logger
from services.plugin.singleton import ServiceSingleton

logger = get_logger(__name__)

_CONFIG: Optional[Dict] = None
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "email_providers.json"


def _load_config() -> Dict:
    global _CONFIG
    if _CONFIG is None:
        with open(_CONFIG_PATH) as f:
            _CONFIG = json.load(f)
    return _CONFIG


class EmailService(ServiceSingleton):
    """Plugin-owned email orchestrator. Inherits ``instance`` /
    ``reset_instance`` from :class:`ServiceSingleton`."""

    @property
    def config(self) -> Dict:
        return _load_config()

    @property
    def defaults(self) -> Dict:
        return self.config.get("defaults", {})

    @property
    def polling(self) -> Dict:
        return self.config.get("polling", {})

    @property
    def himalaya(self):
        from ._himalaya import get_himalaya_service
        return get_himalaya_service()

    def _provider_preset(self, name: str) -> Dict:
        return self.config.get("providers", {}).get(name, {})

    # -- credentials --

    async def resolve_credentials(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Merge stored creds + provider presets + param overrides.

        Precedence (per field): node params > provider preset > stored custom keys.
        Stored custom keys (email_imap_host, email_smtp_port, etc.) are used when
        the provider is 'custom' or when a preset field is empty.
        """
        from services.plugin.deps import get_auth_service
        auth = get_auth_service()

        provider = params.get("provider") or await auth.get_api_key("email_provider") or self.defaults.get("provider")
        preset = self._provider_preset(provider)

        email = params.get("email") or await auth.get_api_key("email_address") or ""
        password = params.get("password") or await auth.get_api_key("email_password") or ""

        if not email:
            raise ValueError("Email address not configured")
        if not password:
            raise ValueError("Email password not configured")

        def _coerce_port(value: Any) -> Any:
            if value in (None, ""):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        imap_host = params.get("imap_host") or preset.get("imap_host") or await auth.get_api_key("email_imap_host") or ""
        imap_port = params.get("imap_port") or preset.get("imap_port") or _coerce_port(await auth.get_api_key("email_imap_port"))
        imap_encryption = params.get("imap_encryption") or preset.get("imap_encryption") or await auth.get_api_key("email_imap_encryption")
        smtp_host = params.get("smtp_host") or preset.get("smtp_host") or await auth.get_api_key("email_smtp_host") or ""
        smtp_port = params.get("smtp_port") or preset.get("smtp_port") or _coerce_port(await auth.get_api_key("email_smtp_port"))
        smtp_encryption = params.get("smtp_encryption") or preset.get("smtp_encryption") or await auth.get_api_key("email_smtp_encryption")

        return {
            "email": email,
            "password": password,
            "display_name": params.get("display_name", ""),
            "imap_host": imap_host,
            "imap_port": imap_port,
            "imap_encryption": imap_encryption,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_encryption": smtp_encryption,
        }

    def resolve_poll_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve polling parameters from node params + JSON polling config."""
        p = self.polling
        interval = params.get("poll_interval", p.get("interval"))
        interval = max(p.get("min_interval"), min(p.get("max_interval"), interval))
        return {
            "interval": interval,
            "folder": params.get("folder", self.defaults.get("folder")),
            "mark_as_read": params.get("mark_as_read", False),
        }

    # -- operations --

    async def send(self, params: Dict[str, Any]) -> Dict[str, Any]:
        creds = await self.resolve_credentials(params)
        d = self.defaults
        result = await self.himalaya.send_email(
            creds,
            to=params.get("to", ""),
            subject=params.get("subject", ""),
            body=params.get("body", ""),
            cc=params.get("cc", ""),
            bcc=params.get("bcc", ""),
            body_type=params.get("body_type", d.get("body_type")),
        )
        return {"from": creds["email"], **(result if isinstance(result, dict) else {})}

    async def read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        creds = await self.resolve_credentials(params)
        d = self.defaults
        op = params.get("operation", "list")
        folder = params.get("folder", d.get("folder"))

        router = {
            "list":    ("list_envelopes",  {"folder": folder,
                                            "page": params.get("page", 1),
                                            "page_size": params.get("page_size", d.get("page_size"))}),
            "search":  ("search_envelopes", {"query": params.get("query", ""), "folder": folder}),
            "read":    ("read_message",     {"message_id": params.get("message_id", ""), "folder": folder}),
            "folders": ("list_folders",     {}),
            "move":    ("move_message",     {"message_id": params.get("message_id", ""),
                                            "target_folder": params.get("target_folder", ""), "folder": folder}),
            "delete":  ("delete_message",   {"message_id": params.get("message_id", ""), "folder": folder}),
            "flag":    ("flag_message",     {"message_id": params.get("message_id", ""),
                                            "flag": params.get("flag", d.get("flag")),
                                            "action": params.get("flag_action", d.get("flag_action")),
                                            "folder": folder}),
        }

        if op not in router:
            raise ValueError(f"Unknown operation: {op}")

        method_name, kwargs = router[op]
        data = await getattr(self.himalaya, method_name)(creds, **kwargs)

        result = {"operation": op, "folder": folder}
        if isinstance(data, dict):
            result.update(data)
        else:
            result["data"] = data
        return result

    # -- polling helpers --

    async def poll_ids(self, creds: dict, folder: str = None) -> Set[str]:
        if folder is None:
            folder = self.defaults.get("folder")
        page_size = self.polling.get("baseline_page_size")
        result = await self.himalaya.list_envelopes(creds, folder=folder, page_size=page_size)
        envs = result if isinstance(result, list) else result.get("data", [])
        return {str(e.get("id") or e.get("uid", "")) for e in envs if e.get("id") or e.get("uid")}

    async def fetch_detail(self, creds: dict, msg_id: str, folder: str = None) -> Dict:
        if folder is None:
            folder = self.defaults.get("folder")
        result = await self.himalaya.read_message(creds, msg_id, folder=folder)
        data = result if isinstance(result, dict) else {"raw": result}
        data.update(message_id=msg_id, folder=folder)
        return data


def get_email_service() -> EmailService:
    return EmailService.instance()
