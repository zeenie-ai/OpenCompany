"""Telegram bot service — moved from services/ to nodes/ as the
plugin-first home for all telegram business logic.

Public surface (consumed by `_handlers.py`, `telegram_send.py`,
`telegram_receive.py`, and the global `status_broadcaster`):

    get_telegram_service() -> TelegramService     module-level singleton

    service.connect(token=None) -> dict           idempotent connect; reads
                                                  token from TelegramCredential
                                                  when omitted
    service.disconnect() -> dict
    service.connected: bool
    service.owner_chat_id: int | None
    service.has_stored_token() -> bool
    service.set_owner(chat_id) -> None
    service.get_status() -> dict

    service.send_message / send_photo / send_document /
    service.send_location / send_contact            send helpers; the
                                                    parse-mode fallback on
                                                    `BadRequest("can't parse
                                                    entities")` is folded into
                                                    a single helper instead of
                                                    being copy-pasted four
                                                    times.

    service.get_me() / service.get_chat(id)         direct API helpers used by
                                                    the non-workflow WebSocket
                                                    handlers.

Auth lookups go through :class:`TelegramCredential.resolve` (lifted from
the per-key ``auth.get_api_key("telegram")`` / ``..._owner_chat_id``
calls scattered across the previous handler/router code).  The credential
class stays declarative — :class:`ApiKeyCredential` does the heavy lifting,
this service just consumes the resolved dict.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional

from telegram import Bot, Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown

from services.plugin.singleton import ServiceSingleton

from ._credentials import TelegramCredential

logger = logging.getLogger(__name__)


# Telegram Bot API hard limits (https://core.telegram.org/bots/api):
#   sendMessage.text -> 1-4096 chars
#   sendPhoto/Video/Document/Audio.caption -> 0-1024 chars
# Exceeding these raises BadRequest("Message is too long" / "Message
# caption is too long"). We split text messages across multiple
# sends, and for media captions we truncate the caption to fit then
# emit the remainder as a follow-up text message so the LLM/user
# never silently loses content.
_TG_TEXT_LIMIT = 4096
_TG_CAPTION_LIMIT = 1024


def _split_text(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters,
    preferring paragraph -> line -> sentence -> space boundaries before
    falling back to a hard cut. Used for Telegram's 4096 / 1024 caps.

    Pure stdlib, no regex outside ``str`` ops -- the splitting heuristic
    should stay legible.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Search for a clean break point in the last ~25% of the window
        # so we don't end up with tiny tail chunks.
        window = remaining[:limit]
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
            idx = window.rfind(sep)
            if idx > limit // 2:
                cut = idx + len(sep)
                break
        if cut <= 0:
            # No clean break -- hard cut at the limit.
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramService(ServiceSingleton):
    """Singleton service for Telegram bot operations.

    Inherits :meth:`ServiceSingleton.instance` for the lazy accessor.
    Overrides :meth:`reset_instance` because telegram has a side effect
    on reset — it must drain the bot's polling task before clearing.
    """

    _lock = asyncio.Lock()

    def __init__(self):
        self._application: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._token: Optional[str] = None
        self._connected: bool = False
        self._polling_task: Optional[asyncio.Task] = None
        self._bot_info: Dict[str, Any] = {}
        self._owner_chat_id: Optional[int] = None

    @classmethod
    async def reset_instance(cls):  # type: ignore[override]
        existing = cls.__dict__.get("_instance")
        if existing is not None:
            await existing.disconnect()
            cls._instance = None

    @property
    def connected(self) -> bool:
        return self._connected and self._application is not None

    @property
    def owner_chat_id(self) -> Optional[int]:
        """Bot owner's chat ID (auto-captured on first private message).
        Falls back to TELEGRAM_OWNER_CHAT_ID env var."""
        if self._owner_chat_id:
            return self._owner_chat_id
        env_owner = os.environ.get("TELEGRAM_OWNER_CHAT_ID")
        if env_owner:
            try:
                self._owner_chat_id = int(env_owner)
                return self._owner_chat_id
            except ValueError:
                pass
        return None

    async def set_owner(self, chat_id: int):
        self._owner_chat_id = chat_id
        logger.info(f"[Telegram] Owner chat_id restored: {chat_id}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "bot_id": self._bot_info.get("id"),
            "bot_username": self._bot_info.get("username"),
            "bot_name": self._bot_info.get("first_name"),
            "polling_active": self._polling_task is not None and not self._polling_task.done(),
            "owner_chat_id": self._owner_chat_id,
        }

    # =========================================================================
    # Connection lifecycle
    # =========================================================================

    async def has_stored_token(self) -> bool:
        """Whether the `telegram` bot-token credential is persisted in auth."""
        try:
            secrets = await TelegramCredential.resolve()
            return bool(secrets.get("api_key"))
        except PermissionError:
            return False

    async def connect(self, token: Optional[str] = None) -> Dict[str, Any]:
        """Connect to Telegram with bot token and start polling.

        When ``token`` is omitted, falls back to the stored ``telegram``
        bot-token credential — matches the Twitter/Google OAuth pattern
        where the frontend saves credentials first and then calls connect.

        Idempotent: if already connected, returns success without tearing
        down the working bot.  See the in-line comment block for the race
        this guards against.
        """
        # Resolve credentials via the declarative class instead of raw
        # ``auth.get_api_key("telegram")`` / ``..._owner_chat_id``
        # lookups.  Keeps the credential id in one place.
        secrets: Dict[str, Any] = {}
        if not token:
            try:
                secrets = await TelegramCredential.resolve()
                token = secrets.get("api_key")
            except PermissionError:
                pass
        if not token:
            return {"success": False, "error": "Bot token required"}

        async with self._lock:
            # Idempotent: avoid the disconnect-then-rebuild race that
            # caused intermittent ``send_message`` hangs against a closed
            # httpx transport when concurrent _refresh_telegram_status
            # tasks fired on overlapping WebSocket connects.
            if self._connected:
                logger.debug(
                    "[Telegram] connect() called while already connected to " f"@{self._bot_info.get('username')}, returning success",
                )
                return {
                    "success": True,
                    "bot": self._bot_info,
                    "message": (f"Already connected to @{self._bot_info.get('username')}"),
                }

            try:
                logger.info("[Telegram] Connecting with bot token...")

                # Build application. Bot info is populated by
                # ``application.initialize()`` below (it calls getMe once
                # and caches on the bot instance) — the previous code
                # also called ``bot.get_me()`` on a separate throwaway
                # Bot before building the Application, paying for a
                # second redundant network round-trip on every cold
                # start.  Drop that — single round-trip now.
                self._application = (
                    Application.builder()
                    .token(token)
                    # Long-polling timeouts (get_updates holds connection open ~10s)
                    .get_updates_read_timeout(30.0)
                    .get_updates_write_timeout(10.0)
                    .get_updates_connect_timeout(10.0)
                    .get_updates_pool_timeout(5.0)
                    # Regular API call timeouts (send_message, etc.)
                    .read_timeout(10.0)
                    .write_timeout(10.0)
                    .connect_timeout(10.0)
                    .build()
                )
                self._bot = self._application.bot
                self._token = token
                self._application.add_handler(MessageHandler(filters.ALL, self._on_message_received))

                await self._application.initialize()

                me = self._bot
                self._bot_info = {
                    "id": me.id,
                    "username": me.username,
                    "first_name": me.first_name,
                    "can_join_groups": me.can_join_groups,
                    "can_read_all_group_messages": me.can_read_all_group_messages,
                }
                logger.info(f"[Telegram] Bot validated: @{me.username} (ID: {me.id})")

                # Hydrate runtime owner_chat_id from stored credential
                # BEFORE polling starts so the pre-poll peek below can
                # short-circuit when we already know the owner. (Was
                # previously after polling -- race-prone with the
                # capture path in _on_message_received.)
                saved_owner = secrets.get("telegram_owner_chat_id")
                if saved_owner is None:
                    try:
                        saved_owner = (await TelegramCredential.resolve()).get("telegram_owner_chat_id")
                    except PermissionError:
                        saved_owner = None
                if saved_owner:
                    try:
                        self._owner_chat_id = int(saved_owner)
                    except (TypeError, ValueError):
                        pass

                # Pre-poll peek for historical owner capture.
                # Polling starts with drop_pending_updates=True
                # (correct hygiene for operational events -- we do
                # not want stale group spam replaying on every
                # restart) but that ALSO drops any DM the user sent
                # before the bot connected. Without this peek the
                # only path to owner capture is "DM the bot AFTER
                # polling has started", which is the surprising UX
                # that produces "Bot owner not detected" on every
                # fresh setup. get_updates() with no offset returns
                # the same pending queue without advancing it, so
                # the subsequent start_polling drop-pass is a no-op
                # for these messages -- they were going to be
                # dropped anyway, we just inspect them first.
                if self._owner_chat_id is None:
                    await self._capture_owner_from_pending_updates()

                self._polling_task = asyncio.create_task(self._run_polling())
                self._connected = True

                await self._broadcast_status()

                logger.info(f"[Telegram] Connected and polling started for @{me.username}")
                return {
                    "success": True,
                    "bot": self._bot_info,
                    "message": f"Connected to @{me.username}",
                }

            except Exception as e:
                logger.error(f"[Telegram] Connection failed: {e}")
                self._connected = False
                self._application = None
                self._bot = None
                return {"success": False, "error": str(e)}

    async def disconnect(self) -> Dict[str, Any]:
        async with self._lock:
            return await self._disconnect_internal()

    async def _disconnect_internal(self) -> Dict[str, Any]:
        try:
            logger.info("[Telegram] Disconnecting...")
            if self._polling_task and not self._polling_task.done():
                self._polling_task.cancel()
                try:
                    await self._polling_task
                except asyncio.CancelledError:
                    pass
            if self._application:
                try:
                    await self._application.stop()
                    await self._application.shutdown()
                except Exception as e:
                    logger.warning(f"[Telegram] Shutdown warning: {e}")

            self._application = None
            self._bot = None
            self._token = None
            self._connected = False
            self._polling_task = None
            self._owner_chat_id = None
            self._bot_info = {}

            await self._broadcast_status()
            logger.info("[Telegram] Disconnected")
            return {"success": True, "message": "Disconnected"}
        except Exception as e:
            logger.error(f"[Telegram] Disconnect error: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Polling loop
    # =========================================================================

    def _on_polling_error(self, error) -> None:
        if isinstance(error, NetworkError):
            logger.debug(f"[Telegram] Network error during polling (auto-retrying): {error}")
        else:
            logger.error(f"[Telegram] Polling error: {error}")

    async def _run_polling(self):
        try:
            logger.info("[Telegram] Starting polling loop...")
            await self._application.start()
            await self._application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                error_callback=self._on_polling_error,
            )
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("[Telegram] Polling cancelled")
            raise
        except Exception as e:
            logger.error(f"[Telegram] Polling error: {e}")
            self._connected = False
            await self._broadcast_status()

    async def _capture_owner_from_pending_updates(self) -> None:
        """Inspect the bot's pending update queue (24h Telegram retention)
        for any private message and capture the owner if found. Called
        from connect() before polling starts -- after polling begins the
        ``drop_pending_updates=True`` flag throws these away unread.

        Uses the same atomic-write-through invariant as
        ``_on_message_received``: persist FIRST, set in-memory only on
        successful persist, so a restart can re-capture cleanly if the
        DB write fails.
        """
        try:
            # offset=None / 0 returns the queue without advancing the
            # confirmed offset. allowed_updates=["message"] skips
            # callback_query / poll / inline_query payloads we don't
            # care about for owner capture.
            pending = await self._bot.get_updates(
                timeout=0,
                allowed_updates=["message"],
            )
        except Exception as e:
            logger.warning(
                f"[Telegram] Pre-poll owner-capture peek failed "
                f"({type(e).__name__}): {e}. Owner capture will fall "
                f"back to live polling once the user DMs the bot.",
            )
            return

        for upd in pending:
            m = upd.message
            if not (m and m.chat and m.chat.type == "private" and m.from_user):
                continue
            captured_id = m.from_user.id
            try:
                from services.plugin.deps import get_auth_service

                await get_auth_service().store_api_key(
                    "telegram_owner_chat_id",
                    str(captured_id),
                    models=[],
                )
            except Exception as persist_err:
                logger.error(
                    f"[Telegram] FAILED to persist retroactively-captured "
                    f"owner {captured_id}: {persist_err}. Workaround: "
                    f"set TELEGRAM_OWNER_CHAT_ID={captured_id} in .env",
                    exc_info=True,
                )
                return
            self._owner_chat_id = captured_id
            logger.info(f"[Telegram] Owner retroactively captured from pending " f"updates: @{m.from_user.username} (ID: {captured_id})")
            return

    async def _on_message_received(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not update.message:
                return
            msg = update.message

            # Auto-capture bot owner from first private message.
            # ATOMIC WRITE-THROUGH: persist to credentials DB FIRST, set
            # in-memory only on success. The previous order
            # (in-memory then await persist, with WARNING-level logging
            # of failures) had a silent failure mode: if the persist
            # raised, the rest of the process lifetime worked correctly,
            # then the next restart wiped in-memory and DB had nothing
            # -- subsequent telegramSend(recipient_type=self) failed
            # forever with "Bot owner not detected" and no trace of
            # what went wrong. The new order preserves the invariant
            # "in-memory has owner => DB has owner" so a restart can
            # always re-capture from the next inbound private message.
            if self._owner_chat_id is None and msg.chat.type == "private" and msg.from_user:
                captured_id = msg.from_user.id
                persist_ok = False
                try:
                    from services.plugin.deps import get_auth_service

                    await get_auth_service().store_api_key(
                        "telegram_owner_chat_id",
                        str(captured_id),
                        models=[],
                    )
                    persist_ok = True
                except Exception as persist_err:
                    # ERROR-level so the failure surfaces in default
                    # uvicorn output, not just DEBUG. Includes the
                    # captured id so the user can manually set
                    # TELEGRAM_OWNER_CHAT_ID in .env as an immediate
                    # workaround. exc_info=True so the underlying
                    # cause (encryption not initialised, DB locked,
                    # disk full, ...) is visible.
                    logger.error(
                        f"[Telegram] FAILED to persist owner chat_id "
                        f"{captured_id}: {persist_err}. The bot will not "
                        f"recover this binding on restart. Workaround: "
                        f"set TELEGRAM_OWNER_CHAT_ID={captured_id} in .env",
                        exc_info=True,
                    )
                if persist_ok:
                    self._owner_chat_id = captured_id
                    logger.info(f"[Telegram] Owner detected and persisted: " f"@{msg.from_user.username} (ID: {captured_id})")
                    await self._broadcast_status()

            event_data = self._format_message(msg)
            logger.info(
                f"[Telegram] Message received: {event_data.get('content_type')} from "
                f"{event_data.get('from_username', event_data.get('from_id'))}, "
                f"chat_type={event_data.get('chat_type')}"
            )

            # Route via plugin _events.py wrapper — canary CloudEvents
            # path (Visibility-query Signal + WS broadcast).
            from . import dispatch_telegram_message_received

            await dispatch_telegram_message_received(event_data)
        except Exception as e:
            logger.error(f"[Telegram] Message handler error: {e}")

    def _format_message(self, msg) -> Dict[str, Any]:
        """Format Telegram message to unified event data."""
        content_type = "text"
        if msg.photo:
            content_type = "photo"
        elif msg.video:
            content_type = "video"
        elif msg.audio:
            content_type = "audio"
        elif msg.voice:
            content_type = "voice"
        elif msg.document:
            content_type = "document"
        elif msg.sticker:
            content_type = "sticker"
        elif msg.location:
            content_type = "location"
        elif msg.contact:
            content_type = "contact"
        elif msg.poll:
            content_type = "poll"

        data = {
            "message_id": msg.message_id,
            "chat_id": msg.chat.id,
            "chat_type": msg.chat.type,
            "chat_title": msg.chat.title,
            "from_id": msg.from_user.id if msg.from_user else None,
            "from_username": msg.from_user.username if msg.from_user else None,
            "from_first_name": msg.from_user.first_name if msg.from_user else None,
            "from_last_name": msg.from_user.last_name if msg.from_user else None,
            "is_bot": msg.from_user.is_bot if msg.from_user else False,
            "text": msg.text or msg.caption or "",
            "content_type": content_type,
            "date": msg.date.isoformat() if msg.date else datetime.now().isoformat(),
            "reply_to_message_id": msg.reply_to_message.message_id if msg.reply_to_message else None,
        }

        if msg.photo:
            photo = msg.photo[-1]
            data["photo"] = {
                "file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "file_size": photo.file_size,
            }
        elif msg.document:
            data["document"] = {
                "file_id": msg.document.file_id,
                "file_name": msg.document.file_name,
                "mime_type": msg.document.mime_type,
                "file_size": msg.document.file_size,
            }
        elif msg.location:
            data["location"] = {
                "latitude": msg.location.latitude,
                "longitude": msg.location.longitude,
            }
        elif msg.contact:
            data["contact"] = {
                "phone_number": msg.contact.phone_number,
                "first_name": msg.contact.first_name,
                "last_name": msg.contact.last_name,
                "user_id": msg.contact.user_id,
            }
        return data

    async def _broadcast_status(self):
        try:
            # Wave 12 B3: route via plugin _events.py wrapper.
            from . import broadcast_telegram_status

            await broadcast_telegram_status(
                connected=self._connected,
                bot_id=self._bot_info.get("id"),
                bot_username=self._bot_info.get("username"),
                bot_name=self._bot_info.get("first_name"),
                owner_chat_id=self._owner_chat_id,
            )
        except Exception as e:
            logger.warning(f"[Telegram] Status broadcast failed: {e}")

    # =========================================================================
    # Send helpers — parse-mode preprocessing + BadRequest fallback in one place
    # =========================================================================

    @staticmethod
    def _escape_text(text: Optional[str], parse_mode: Optional[str]) -> Optional[str]:
        if not text or not parse_mode:
            return text
        if parse_mode == "MarkdownV2":
            return escape_markdown(text, version=2)
        if parse_mode == "Markdown":
            return escape_markdown(text, version=1)
        return text

    @staticmethod
    def _format_auto(text: Optional[str]) -> tuple[Optional[str], str]:
        if not text:
            return text, "HTML"
        from services.markdown_formatter import to_telegram_html

        return to_telegram_html(text), "HTML"

    @classmethod
    def _resolve_body(cls, body: Optional[str], parse_mode: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """Apply parse-mode preprocessing to a text/caption.  Returns
        ``(body, effective_parse_mode)``."""
        if parse_mode == "Auto":
            return cls._format_auto(body)
        return cls._escape_text(body, parse_mode), parse_mode or None

    async def _send_with_parse_fallback(
        self,
        method: Callable[..., Awaitable[Any]],
        *,
        body_kw: str,
        body: Optional[str],
        parse_mode: Optional[str],
        **kwargs: Any,
    ) -> Any:
        """Call ``method(**{body_kw: processed_body, parse_mode: pm}, **kwargs)``;
        on Telegram ``BadRequest("can't parse entities")`` retry once with the
        raw body and ``parse_mode=None``.  Folds 4 copies of the same try/except
        into a single helper.
        """
        processed, effective_pm = self._resolve_body(body, parse_mode)
        try:
            return await method(
                **{body_kw: processed},
                parse_mode=effective_pm,
                **kwargs,
            )
        except BadRequest as e:
            if "can't parse entities" in str(e).lower() and effective_pm:
                logger.warning(f"[Telegram] Parse mode {effective_pm} failed for " f"{method.__name__}, sending as plain text")
                return await method(
                    **{body_kw: body},
                    parse_mode=None,
                    **kwargs,
                )
            raise

    def _require_bot(self) -> Bot:
        if not self._bot:
            raise ValueError("Telegram bot not connected")
        return self._bot

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: Optional[str] = None,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        bot = self._require_bot()
        # Telegram caps sendMessage.text at 4096. Split into chunks and
        # emit a chain (each chunk replies to the previous one so they
        # render as a thread). Returns the FIRST message's metadata
        # plus a parts/message_ids summary so callers can track the
        # whole chain.
        chunks = _split_text(text, _TG_TEXT_LIMIT)
        if len(chunks) > 1:
            logger.info(
                "[Telegram] Splitting %d-char message into %d parts",
                len(text),
                len(chunks),
            )

        first_msg: Any = None
        message_ids: list[int] = []
        reply_to = reply_to_message_id
        for chunk in chunks:
            msg = await self._send_with_parse_fallback(
                bot.send_message,
                body_kw="text",
                body=chunk,
                parse_mode=parse_mode,
                chat_id=chat_id,
                disable_notification=disable_notification,
                reply_to_message_id=reply_to,
            )
            if first_msg is None:
                first_msg = msg
            message_ids.append(msg.message_id)
            # Thread subsequent chunks under the previous chunk.
            reply_to = msg.message_id

        return {
            "message_id": first_msg.message_id,
            "chat_id": first_msg.chat.id,
            "date": first_msg.date.isoformat(),
            "text": first_msg.text,
            "parts": len(chunks),
            "message_ids": message_ids,
        }

    async def send_photo(
        self,
        chat_id: str | int,
        photo: str,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = None,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        bot = self._require_bot()
        msg = await self._send_with_parse_fallback(
            bot.send_photo,
            body_kw="caption",
            body=caption,
            parse_mode=parse_mode,
            chat_id=chat_id,
            photo=photo,
            disable_notification=disable_notification,
            reply_to_message_id=reply_to_message_id,
        )
        return {
            "message_id": msg.message_id,
            "chat_id": msg.chat.id,
            "date": msg.date.isoformat(),
        }

    async def send_document(
        self,
        chat_id: str | int,
        document: str,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = None,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        bot = self._require_bot()
        msg = await self._send_with_parse_fallback(
            bot.send_document,
            body_kw="caption",
            body=caption,
            parse_mode=parse_mode,
            chat_id=chat_id,
            document=document,
            disable_notification=disable_notification,
            reply_to_message_id=reply_to_message_id,
        )
        return {
            "message_id": msg.message_id,
            "chat_id": msg.chat.id,
            "date": msg.date.isoformat(),
        }

    async def send_location(
        self,
        chat_id: str | int,
        latitude: float,
        longitude: float,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        bot = self._require_bot()
        msg = await bot.send_location(
            chat_id=chat_id,
            latitude=latitude,
            longitude=longitude,
            disable_notification=disable_notification,
            reply_to_message_id=reply_to_message_id,
        )
        return {
            "message_id": msg.message_id,
            "chat_id": msg.chat.id,
            "date": msg.date.isoformat(),
        }

    async def send_contact(
        self,
        chat_id: str | int,
        phone_number: str,
        first_name: str,
        last_name: Optional[str] = None,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        bot = self._require_bot()
        msg = await bot.send_contact(
            chat_id=chat_id,
            phone_number=phone_number,
            first_name=first_name,
            last_name=last_name,
            disable_notification=disable_notification,
            reply_to_message_id=reply_to_message_id,
        )
        return {
            "message_id": msg.message_id,
            "chat_id": msg.chat.id,
            "date": msg.date.isoformat(),
        }

    # =========================================================================
    # Misc API helpers (used by the credentials-modal WS handlers)
    # =========================================================================

    async def get_me(self) -> Dict[str, Any]:
        bot = self._require_bot()
        me = await bot.get_me()
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "can_join_groups": me.can_join_groups,
        }

    async def get_chat(self, chat_id: str | int) -> Dict[str, Any]:
        bot = self._require_bot()
        chat = await bot.get_chat(chat_id)
        return {
            "id": chat.id,
            "type": chat.type,
            "title": chat.title,
            "username": chat.username,
            "first_name": chat.first_name,
            "last_name": chat.last_name,
            "description": chat.description,
        }


def get_telegram_service() -> TelegramService:
    """Get TelegramService singleton instance."""
    return TelegramService.instance()
