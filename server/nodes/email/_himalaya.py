"""Himalaya CLI wrapper service for IMAP/SMTP email operations.

Wraps the himalaya CLI (https://github.com/pimalaya/himalaya) to provide
email send/receive/manage capabilities via any IMAP/SMTP provider.
"""

import asyncio
import json
import shutil
import tempfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import List, Optional

from core.logging import get_logger
from services.plugin.singleton import ServiceSingleton

logger = get_logger(__name__)


class HimalayaService(ServiceSingleton):
    """Manages Himalaya CLI configuration and execution.

    Inherits ``instance`` / ``reset_instance`` from
    :class:`ServiceSingleton`."""

    def __init__(self):
        self._binary_path: Optional[str] = None

    async def ensure_binary(self) -> str:
        """Detect himalaya binary in PATH. Returns path or raises."""
        if self._binary_path:
            return self._binary_path

        binary = shutil.which("himalaya")
        if binary:
            self._binary_path = binary
            logger.info(f"[Himalaya] Found binary: {binary}")
            return binary

        raise RuntimeError(
            "himalaya CLI not found in PATH. "
            "Install via: cargo install himalaya, brew install himalaya, "
            "or download from https://github.com/pimalaya/himalaya/releases"
        )

    def _generate_config(self, account_name: str, credentials: dict) -> str:
        """Generate TOML config content for a himalaya account."""
        email = credentials.get("email", "")
        display_name = credentials.get("display_name", "")
        password = credentials.get("password", "")

        imap_host = credentials.get("imap_host", "")
        imap_port = credentials.get("imap_port", 993)
        imap_encryption = credentials.get("imap_encryption", "tls")

        smtp_host = credentials.get("smtp_host", "")
        smtp_port = credentials.get("smtp_port", 465)
        smtp_encryption = credentials.get("smtp_encryption", "tls")

        lines = [
            f"[accounts.{account_name}]",
            f'email = "{email}"',
        ]
        if display_name:
            lines.append(f'display-name = "{display_name}"')

        # IMAP backend
        lines.extend(
            [
                "",
                'backend.type = "imap"',
                f'backend.host = "{imap_host}"',
                f"backend.port = {imap_port}",
                f'backend.encryption = "{imap_encryption}"',
                f'backend.login = "{email}"',
                'backend.auth.type = "password"',
                f'backend.auth.raw = "{password}"',
            ]
        )

        # SMTP sender
        lines.extend(
            [
                "",
                'message.send.backend.type = "smtp"',
                f'message.send.backend.host = "{smtp_host}"',
                f"message.send.backend.port = {smtp_port}",
                f'message.send.backend.encryption = "{smtp_encryption}"',
                f'message.send.backend.login = "{email}"',
                'message.send.backend.auth.type = "password"',
                f'message.send.backend.auth.raw = "{password}"',
            ]
        )

        return "\n".join(lines)

    async def execute(
        self,
        account_name: str,
        credentials: dict,
        args: List[str],
        stdin_data: Optional[str] = None,
    ) -> dict:
        """Execute himalaya CLI command and return parsed JSON output."""
        binary = await self.ensure_binary()
        config_content = self._generate_config(account_name, credentials)

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", prefix="himalaya_", delete=False)
        try:
            tmp.write(config_content)
            tmp.flush()
            tmp.close()

            cmd = [
                binary,
                "-c",
                tmp.name,
                "-a",
                account_name,
                "--output",
                "json",
            ] + args

            logger.debug(f"[Himalaya] Executing: himalaya {' '.join(args)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
            )

            stdin_bytes = stdin_data.encode("utf-8") if stdin_data else None
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=60,
            )

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                error_msg = stderr_str or stdout_str or f"Exit code {proc.returncode}"
                logger.error(f"[Himalaya] Command failed: {error_msg}")
                raise RuntimeError(f"himalaya error: {error_msg}")

            if not stdout_str:
                return {}

            try:
                return json.loads(stdout_str)
            except json.JSONDecodeError:
                return {"raw_output": stdout_str}

        finally:
            Path(tmp.name).unlink(missing_ok=True)

    # =========================================================================
    # HIGH-LEVEL OPERATIONS
    # =========================================================================

    async def send_email(
        self,
        credentials: dict,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        body_type: str = "text",
    ) -> dict:
        """Send an email via SMTP. Composes RFC 2822 and pipes to himalaya."""
        account = self._account_name(credentials)

        if body_type == "html":
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "html", "utf-8"))
        else:
            msg = MIMEText(body, "plain", "utf-8")

        msg["From"] = credentials.get("email", "")
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        return await self.execute(
            account,
            credentials,
            ["message", "send"],
            stdin_data=msg.as_string(),
        )

    async def list_envelopes(
        self,
        credentials: dict,
        folder: str = "INBOX",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List email envelopes in a folder."""
        account = self._account_name(credentials)
        return await self.execute(
            account,
            credentials,
            [
                "envelope",
                "list",
                "-f",
                folder,
                "--page",
                str(page),
                "--page-size",
                str(page_size),
            ],
        )

    async def search_envelopes(
        self,
        credentials: dict,
        query: str,
        folder: str = "INBOX",
    ) -> dict:
        """Search email envelopes by query."""
        account = self._account_name(credentials)
        return await self.execute(
            account,
            credentials,
            ["envelope", "list", "-f", folder, "--query", query],
        )

    async def read_message(
        self,
        credentials: dict,
        message_id: str,
        folder: str = "INBOX",
    ) -> dict:
        """Read full message content."""
        account = self._account_name(credentials)
        return await self.execute(
            account,
            credentials,
            ["message", "read", message_id, "-f", folder],
        )

    async def move_message(
        self,
        credentials: dict,
        message_id: str,
        target_folder: str,
        folder: str = "INBOX",
    ) -> dict:
        """Move a message to another folder."""
        account = self._account_name(credentials)
        return await self.execute(
            account,
            credentials,
            ["message", "move", message_id, target_folder, "-f", folder],
        )

    async def delete_message(
        self,
        credentials: dict,
        message_id: str,
        folder: str = "INBOX",
    ) -> dict:
        """Delete a message."""
        account = self._account_name(credentials)
        return await self.execute(
            account,
            credentials,
            ["message", "delete", message_id, "-f", folder],
        )

    async def flag_message(
        self,
        credentials: dict,
        message_id: str,
        flag: str,
        action: str = "add",
        folder: str = "INBOX",
    ) -> dict:
        """Add or remove a flag on a message."""
        account = self._account_name(credentials)
        flag_cmd = "add" if action == "add" else "remove"
        return await self.execute(
            account,
            credentials,
            ["flag", flag_cmd, message_id, "--flag", flag, "-f", folder],
        )

    async def list_folders(self, credentials: dict) -> dict:
        """List all mailbox folders."""
        account = self._account_name(credentials)
        return await self.execute(account, credentials, ["folder", "list"])

    def _account_name(self, credentials: dict) -> str:
        """Generate a consistent account name from credentials."""
        email = credentials.get("email", "default")
        return email.split("@")[0].replace(".", "_").replace("+", "_")


def get_himalaya_service() -> HimalayaService:
    """Get singleton instance."""
    return HimalayaService.instance()
