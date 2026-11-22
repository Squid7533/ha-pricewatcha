"""Telegram front-end: commands and price-drop pushes without any YAML.

Home Assistant's Telegram bot integration already does the transport (polling, token,
allowed chats) and fires `telegram_command` / `telegram_text` events, and offers the
`telegram_bot.send_message` service. This module listens to those events and answers
through that service, so the user never edits configuration.yaml, never adds helpers,
and never looks up a chat id: the first chat to speak to the bot is remembered.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .media import ProductMedia, fetch_product_media
from .const import (
    CONVERSATION_HISTORY_MAX,
    CONVERSATION_STORAGE_KEY,
    CONVERSATION_STORAGE_VERSION,
    CONVERSATION_TEXT_MAX,
    CONVERSATION_TTL_SECONDS,
    DOMAIN,
    EVENT_PRICE_CHANGED,
)
from .format import (
    format_ai_section,
    format_photo_caption,
    format_ai_unavailable,
    format_alerts,
    format_deals,
    format_help,
    format_price_drop,
    format_reset,
    format_search,
    format_track,
    format_usage,
    format_verdict,
    to_plain,
    truncate,
)

if TYPE_CHECKING:
    from .ai import AIAdvisor
    from .coordinator import PricewatchaCoordinator

_LOGGER = logging.getLogger(__name__)

TELEGRAM_DOMAIN = "telegram_bot"
TELEGRAM_SEND_MESSAGE = "send_message"
EVENT_TELEGRAM_COMMAND = "telegram_command"
EVENT_TELEGRAM_TEXT = "telegram_text"

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.telegram"

ISSUE_NO_ALLOWED_CHAT = "telegram_no_allowed_chat"

# Shown in Telegram's "/" menu once registered with the Bot API.
BOT_COMMANDS = [
    ("deal", "Best deal for a product"),
    ("ask", "Ask about your last deal"),
    ("search", "Search the product catalog"),
    ("track", "Track a product URL"),
    ("watch", "Watch a product id"),
    ("alerts", "List price alerts"),
    ("reset", "Forget the current conversation"),
    ("help", "Show commands"),
]

_BOT_COMMAND_LABELS = {
    "de": {
        "deal": "Bester Preis für ein Produkt",
        "ask": "Zum letzten Deal fragen",
        "search": "Produktkatalog durchsuchen",
        "track": "Produkt-URL tracken",
        "watch": "Katalogprodukt beobachten",
        "alerts": "Preisalarme anzeigen",
        "reset": "Aktuelles Gespräch vergessen",
        "help": "Befehle anzeigen",
    }
}


async def set_bot_commands(session, token: str, commands: list[tuple[str, str]]) -> None:
    """Publish the command list to Telegram's "/" menu (best effort)."""
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    payload = {
        "commands": [{"command": name, "description": text} for name, text in commands]
    }
    async with session.post(url, json=payload, timeout=15) as response:
        if response.status >= 400:
            body = await response.text()
            raise RuntimeError(f"setMyCommands failed: HTTP {response.status} {body[:200]}")


class TelegramFrontend:
    """Answers Telegram commands and pushes price drops."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: PricewatchaCoordinator,
        min_drop_pct: float,
        advisor: AIAdvisor | None = None,
        language: str | None = None,
        photos: bool = False,
    ) -> None:
        self.hass = hass
        self._coordinator = coordinator
        self._min_drop_pct = min_drop_pct
        self._advisor = advisor
        self._photos = photos
        # The bot language is one setting: table, verdict, help and AI text all
        # follow it. Tests may pass an advisor alone, so fall back to its language.
        self._language = language or (advisor.language if advisor else "en")
        self._media_cache: dict[str, tuple[float, ProductMedia | None]] = {}
        self._last_deals: dict[int, dict[str, Any]] = {}
        self._last_assessments: dict[int, str] = {}
        self._last_history: dict[int, list[dict[str, str]]] = {}
        self._memory_updated: dict[int, float] = {}
        self._memory_lock = asyncio.Lock()
        self._conversations: Store[dict[str, Any]] | None = None
        self._chat_ids: list[int] = []
        self._store: Store[dict[str, Any]] | None = None
        self._unsubscribers: list[Any] = []
        self._warned_missing_service = False
        self._chat_params: dict[str, str] = {}

    # --- lifecycle -------------------------------------------------------------

    async def async_start(self) -> None:
        """Load the remembered chats and start listening."""
        self._store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)
        stored = await self._store.async_load() or {}
        self._chat_ids = [
            int(chat)
            for chat in stored.get("chat_ids", [])
            if str(chat).lstrip("-").isdigit()
        ]

        self._conversations = Store(
            self.hass, CONVERSATION_STORAGE_VERSION, CONVERSATION_STORAGE_KEY
        )
        await self._async_load_memory()

        self._unsubscribers = [
            self.hass.bus.async_listen(EVENT_TELEGRAM_COMMAND, self._on_command),
            self.hass.bus.async_listen(EVENT_TELEGRAM_TEXT, self._on_text),
            self.hass.bus.async_listen(EVENT_PRICE_CHANGED, self._on_price_changed),
        ]

        self._async_check_allowed_chats()
        self.hass.async_create_task(self._async_register_commands())

        if not self._service_available():
            _LOGGER.info(
                "Telegram bot service is not available yet; Pricewatcha commands stay idle "
                "until the Telegram bot integration is set up"
            )

    @callback
    def async_stop(self) -> None:
        """Detach from the event bus."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @property
    def chat_ids(self) -> list[int]:
        """Chats this front-end answers."""
        return list(self._chat_ids)

    # --- plumbing --------------------------------------------------------------

    def _service_available(self) -> bool:
        return self.hass.services.has_service(TELEGRAM_DOMAIN, TELEGRAM_SEND_MESSAGE)

    def _chat_param_name(self, service_name: str = TELEGRAM_SEND_MESSAGE) -> str:
        """Which field this Home Assistant calls the destination chat.

        `target` is deprecated and stops working in 2026.9.0 in favour of `chat_id`, so
        the supported field is read from the registered service schema instead of
        guessing from the version.
        """
        if service_name in self._chat_params:
            return self._chat_params[service_name]

        services = self.hass.services.async_services()
        telegram = services.get(TELEGRAM_DOMAIN, {})
        service = telegram.get(service_name) or telegram.get(TELEGRAM_SEND_MESSAGE)
        validators = getattr(getattr(service, "schema", None), "schema", None)
        keys = {
            getattr(key, "schema", key)
            for key in validators
        } if isinstance(validators, dict) else set()

        param = "chat_id" if "chat_id" in keys else "target"
        if param == "target":
            _LOGGER.debug("Telegram bot service still expects 'target'; using it")
        self._chat_params[service_name] = param
        return param

    async def _async_call(self, chat_id: int, message: str, parse_mode: str) -> None:
        await self.hass.services.async_call(
            TELEGRAM_DOMAIN,
            TELEGRAM_SEND_MESSAGE,
            {
                self._chat_param_name(): [int(chat_id)],
                "message": message,
                "parse_mode": parse_mode,
            },
            blocking=True,
        )

    async def _async_send(self, chat_id: int, text: str) -> None:
        if not self._service_available():
            if not self._warned_missing_service:
                self._warned_missing_service = True
                _LOGGER.warning(
                    "Cannot answer in Telegram: set up the Telegram bot integration first "
                    "(Settings -> Devices & services -> Add integration -> Telegram bot)"
                )
            return
        # HTML gives bold prices and tappable links. The formatters escape every
        # dynamic value, but a rejected entity must still not swallow the reply: fall
        # back to the same text as plain text once.
        try:
            await self._async_call(chat_id, truncate(text), "html")
        except Exception as err:  # noqa: BLE001 - a failed reply must not kill the listener
            _LOGGER.debug("Telegram rejected the HTML reply (%s); retrying as plain text", err)
            try:
                await self._async_call(chat_id, truncate(to_plain(text)), "plain_text")
            except Exception as plain_err:  # noqa: BLE001
                _LOGGER.warning("Telegram send failed: %s", plain_err)

    async def _async_load_memory(self) -> None:
        """Restore the last deal, assessment and Q&A for each chat."""
        if self._conversations is None:
            return
        stored = await self._conversations.async_load() or {}
        now = time.time()
        chats = stored.get("chats") if isinstance(stored, dict) else None
        for raw_chat, entry in (chats or {}).items():
            if not str(raw_chat).lstrip("-").isdigit() or not isinstance(entry, dict):
                continue
            try:
                updated = float(entry.get("updated_at") or 0)
            except (TypeError, ValueError):
                updated = 0.0
            if now - updated > CONVERSATION_TTL_SECONDS:
                continue
            chat_id = int(raw_chat)
            deal = entry.get("deal")
            if isinstance(deal, dict):
                self._last_deals[chat_id] = deal
            assessment = entry.get("assessment")
            if isinstance(assessment, str) and assessment:
                self._last_assessments[chat_id] = assessment
            history = entry.get("history")
            if isinstance(history, list):
                self._last_history[chat_id] = [
                    item for item in history if isinstance(item, dict)
                ][-CONVERSATION_HISTORY_MAX:]
            self._memory_updated[chat_id] = updated

    async def _async_save_memory(self) -> None:
        if self._conversations is None:
            return
        async with self._memory_lock:
            now = time.time()
            chats: dict[str, dict[str, Any]] = {}
            for chat_id, deal in self._last_deals.items():
                updated = self._memory_updated.get(chat_id, now)
                if now - updated > CONVERSATION_TTL_SECONDS:
                    continue
                entry: dict[str, Any] = {"updated_at": updated, "deal": deal}
                assessment = self._last_assessments.get(chat_id)
                if assessment:
                    entry["assessment"] = assessment[: 2 * CONVERSATION_TEXT_MAX]
                history = self._last_history.get(chat_id)
                if history:
                    entry["history"] = history[-CONVERSATION_HISTORY_MAX:]
                chats[str(chat_id)] = entry
            await self._conversations.async_save({"chats": chats})

    def _touch_memory(self, chat_id: int) -> None:
        self._memory_updated[chat_id] = time.time()

    def _deal_for(self, chat_id: int) -> dict[str, Any] | None:
        """The stored deal, unless it is older than the conversation TTL."""
        updated = self._memory_updated.get(chat_id)
        if updated is not None and time.time() - updated > CONVERSATION_TTL_SECONDS:
            self._last_deals.pop(chat_id, None)
            self._last_assessments.pop(chat_id, None)
            self._last_history.pop(chat_id, None)
            self._memory_updated.pop(chat_id, None)
            return None
        return self._last_deals.get(chat_id)

    async def _async_reset(self, chat_id: int) -> None:
        self._last_deals.pop(chat_id, None)
        self._last_assessments.pop(chat_id, None)
        self._last_history.pop(chat_id, None)
        self._memory_updated.pop(chat_id, None)
        await self._async_save_memory()
        await self._async_send(chat_id, format_reset(self._language))

    async def _async_media(self, page_url: str) -> ProductMedia | None:
        cached = self._media_cache.get(page_url)
        ttl = 6 * 3600 if cached is not None and cached[1] is not None else 600
        if cached is not None and time.monotonic() - cached[0] < ttl:
            return cached[1]
        try:
            media = await fetch_product_media(page_url)
        except Exception:  # noqa: BLE001 - a blocked shop only costs the photo
            _LOGGER.debug("Product media failed for %s", page_url, exc_info=True)
            media = None
        self._media_cache[page_url] = (time.monotonic(), media)
        return media

    async def _async_send_photo(self, chat_id: int, payload: dict[str, Any]) -> None:
        """Send the top deal's product photo, if the shop exposes one."""
        if not self._photos or not payload.get("deals"):
            return
        if not self.hass.services.has_service(TELEGRAM_DOMAIN, "send_photo"):
            return
        deal = payload["deals"][0]
        page_url = deal.get("url")
        if not page_url:
            return

        media = await self._async_media(page_url)
        if media is None:
            return

        # The page can state a condition the catalog does not carry; keep it for
        # the caption and for a later /ask about the same deal, even if the image
        # itself could not be downloaded.
        if deal.get("condition") is None and media.condition:
            deal["condition"] = media.condition
        if not media.image:
            return

        caption = format_photo_caption(deal, self._language, media.condition)
        directory = self.hass.config.path("pricewatcha_tmp")
        path = await self.hass.async_add_executor_job(
            _write_temp_file, media.image, media.suffix, directory
        )
        try:
            await self.hass.services.async_call(
                TELEGRAM_DOMAIN,
                "send_photo",
                {
                    self._chat_param_name("send_photo"): [int(chat_id)],
                    "file": path,
                    "caption": caption,
                    "parse_mode": "html",
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - text already delivered
            _LOGGER.debug("Telegram photo send failed: %s", err)
        finally:
            try:
                await self.hass.async_add_executor_job(os.unlink, path)
            except OSError:
                pass

    async def _async_register_commands(self) -> None:
        """Make the commands visible in Telegram's "/" menu."""
        entries = self.hass.config_entries.async_entries(TELEGRAM_DOMAIN)
        if not entries:
            return

        session = async_get_clientsession(self.hass)
        for entry in entries:
            token = entry.data.get("api_key")
            if not token:
                continue
            labels = _BOT_COMMAND_LABELS.get(self._language, {})
            commands = [
                (name, labels.get(name, description))
                for name, description in BOT_COMMANDS
            ]
            try:
                await set_bot_commands(session, str(token), commands)
                _LOGGER.debug("Registered the Pricewatcha command menu on %s", entry.title)
            except Exception as err:  # noqa: BLE001 - cosmetic, never fatal
                _LOGGER.debug("Could not register Telegram commands: %s", err)

    @callback
    def _async_check_allowed_chats(self) -> None:
        """Warn loudly when the bot will drop every message.

        Since HA 2026.2 a Telegram bot ignores chats that are not added as allowed-chat
        subentries, so this is the most likely reason for a silent bot.
        """
        entries = self.hass.config_entries.async_entries(TELEGRAM_DOMAIN)
        if not entries:
            return

        without_chat = [entry for entry in entries if not entry.subentries]
        if len(without_chat) == len(entries):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_NO_ALLOWED_CHAT,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_ALLOWED_CHAT,
            )
            _LOGGER.warning(
                "The Telegram bot has no allowed chats, so Home Assistant drops every message "
                "before Pricewatcha can answer it. Open Settings -> Devices & services -> "
                "Telegram bot -> Add allowed chat, enter your chat id, then message the bot again"
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_NO_ALLOWED_CHAT)

    async def _async_authorise(self, chat_id: int) -> bool:
        """The first chat to write becomes the owner; others are refused."""
        if chat_id in self._chat_ids:
            return True

        if not self._chat_ids:
            self._chat_ids = [chat_id]
            if self._store is not None:
                await self._store.async_save({"chat_ids": self._chat_ids})
            _LOGGER.info("Remembered Telegram chat %s for Pricewatcha", chat_id)
            return True

        message = (
            "Dieser Bot ist bereits mit einem anderen Home Assistant verbunden."
            if self._language == "de"
            else "This bot is already connected to someone else's Home Assistant."
        )
        await self._async_send(chat_id, message)
        return False

    # --- event handlers --------------------------------------------------------

    @callback
    def _on_command(self, event: Event) -> None:
        self.hass.async_create_task(self._async_handle_command(event))

    @callback
    def _on_text(self, event: Event) -> None:
        self.hass.async_create_task(self._async_handle_text(event))

    @callback
    def _on_price_changed(self, event: Event) -> None:
        data = event.data or {}
        if not data.get("is_drop") or not self._chat_ids:
            return
        try:
            drop = abs(float(data.get("change_pct") or 0))
        except (TypeError, ValueError):
            return
        if drop < self._min_drop_pct:
            return
        self.hass.async_create_task(
            self._async_broadcast(format_price_drop(dict(data), self._language))
        )

    async def _async_broadcast(self, text: str) -> None:
        for chat_id in self.chat_ids:
            await self._async_send(chat_id, text)

    # --- commands --------------------------------------------------------------

    async def _async_handle_command(self, event: Event) -> None:
        data = event.data or {}
        chat_id = self._as_chat_id(data.get("chat_id"))
        if chat_id is None or not await self._async_authorise(chat_id):
            return

        command = str(data.get("command") or "").lower()
        args = [str(arg) for arg in (data.get("args") or [])]
        rest = " ".join(args).strip()

        if command in ("/start", "/help"):
            await self._async_send(chat_id, format_help(self._language))
        elif command == "/deal":
            await self._async_deals(chat_id, rest)
        elif command == "/ask":
            await self._async_ask(chat_id, rest)
        elif command == "/search":
            await self._async_search(chat_id, rest)
        elif command == "/track":
            await self._async_track(chat_id, args[0] if args else "")
        elif command == "/watch":
            await self._async_watch(chat_id, args[0] if args else "")
        elif command == "/alerts":
            await self._async_alerts(chat_id)
        elif command == "/reset":
            await self._async_reset(chat_id)
        else:
            await self._async_send(chat_id, format_help(self._language))

    async def _async_handle_text(self, event: Event) -> None:
        data = event.data or {}
        chat_id = self._as_chat_id(data.get("chat_id"))
        if chat_id is None or not await self._async_authorise(chat_id):
            return
        text = str(data.get("text") or "").strip()
        if text.startswith("/") or len(text) <= 2:
            return
        if (
            self._advisor is not None
            and self._advisor.available
            and chat_id in self._last_deals
            and _looks_like_followup(text)
        ):
            await self._async_ask(chat_id, text)
            return
        await self._async_deals(chat_id, text)

    def _error_text(self, err: Exception) -> str:
        prefix = "Pricewatcha-Fehler" if self._language == "de" else "Pricewatcha error"
        return f"{prefix}: {err}"

    @staticmethod
    def _as_chat_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # --- command implementations ----------------------------------------------

    async def _async_deals(self, chat_id: int, query: str) -> None:
        if not query:
            await self._async_send(chat_id, format_usage("/deal", "iphone 18 pro", self._language))
            return
        try:
            payload = await self._coordinator.async_find_best_deals(query, limit=3)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("deal lookup failed", exc_info=True)
            await self._async_send(chat_id, self._error_text(err))
            return
        ai_active = self._advisor is not None and self._advisor.available
        # With AI on, the assessment is the verdict; keeping both duplicated (and
        # sometimes contradicted) the recommendation. If it fails, the verdict is
        # sent as its own short message below.
        await self._async_send(
            chat_id,
            format_deals(payload, self._language, include_verdict=not ai_active),
        )
        if not payload.get("deals"):
            return
        self._last_deals[chat_id] = payload
        self._last_assessments.pop(chat_id, None)
        self._last_history[chat_id] = []
        self._touch_memory(chat_id)
        await self._async_send_photo(chat_id, payload)

        if ai_active:
            try:
                result = await self._advisor.async_advise(payload)
            except Exception:  # noqa: BLE001 - the table is already delivered
                _LOGGER.debug("AI assessment failed", exc_info=True)
                result = None
            if result is not None:
                self._last_assessments[chat_id] = result.text
                await self._async_send(
                    chat_id, format_ai_section(result, self._language)
                )
                self._touch_memory(chat_id)
                await self._async_save_memory()
                return
            verdict = format_verdict(payload, self._language)
            if verdict:
                await self._async_send(chat_id, verdict)
        self._touch_memory(chat_id)
        await self._async_save_memory()

    async def _async_ask(self, chat_id: int, question: str) -> None:
        language = self._language
        if not question:
            await self._async_send(chat_id, format_usage("/ask", "is 669 a good price?", self._language))
            return
        if self._advisor is None or not self._advisor.available:
            reason = (
                "Richte unter Einstellungen → Geräte & Dienste → Pricewatcha → "
                "Konfigurieren → KI-Assistent einen Anbieter, ein Modell und einen "
                "Schlüssel ein."
                if language == "de"
                else "Configure a provider, model and key under Settings → Devices & "
                "services → Pricewatcha → Configure → AI assistant."
            )
            await self._async_send(chat_id, format_ai_unavailable(reason, language))
            return

        payload = self._deal_for(chat_id)
        if payload is None:
            message = (
                "Starte zuerst /deal, damit ich die Angebote kenne."
                if language == "de"
                else "Run /deal first so I know which offers to talk about."
            )
            await self._async_send(chat_id, format_ai_unavailable(message, language))
            return

        previous = self._last_assessments.get(chat_id)
        history = self._last_history.get(chat_id, [])
        try:
            result = await self._advisor.async_advise(
                payload,
                question,
                previous,
                web=self._advisor.web_available,
                history=history,
            )
        except Exception:  # noqa: BLE001 - never leave /ask without an answer
            _LOGGER.debug("AI question failed", exc_info=True)
            result = None
        if result is None:
            reason = self._advisor.last_error or "provider_error"
            await self._async_send(chat_id, format_ai_unavailable(reason, language))
            return
        await self._async_send(
            chat_id, format_ai_section(result, language, answer=True)
        )
        self._last_history[chat_id] = [
            *history,
            {
                "q": question[:CONVERSATION_TEXT_MAX],
                "a": result.text[:CONVERSATION_TEXT_MAX],
            },
        ][-CONVERSATION_HISTORY_MAX:]
        self._touch_memory(chat_id)
        await self._async_save_memory()

    async def _async_search(self, chat_id: int, query: str) -> None:
        if not query:
            await self._async_send(chat_id, format_usage("/search", "iphone 18 pro", self._language))
            return
        try:
            results = await self._coordinator.client.search(query, limit=8)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("search failed", exc_info=True)
            await self._async_send(chat_id, self._error_text(err))
            return
        await self._async_send(
            chat_id,
            format_search(
                {"query": query, "count": len(results), "results": results},
                self._language,
            ),
        )

    async def _async_track(self, chat_id: int, url: str) -> None:
        if not url.startswith("http"):
            await self._async_send(chat_id, format_usage("/track", "https://shop.example/product", self._language))
            return
        try:
            payload = await self._coordinator.client.track(url, watch=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("track failed", exc_info=True)
            await self._async_send(chat_id, self._error_text(err))
            return
        await self._async_send(chat_id, format_track(payload, self._language))
        await self._coordinator.async_request_refresh()

    async def _async_watch(self, chat_id: int, product_id: str) -> None:
        if not product_id:
            await self._async_send(chat_id, format_usage("/watch", "prod_ONgO0IjXa7YnqQ", self._language))
            return
        try:
            await self._coordinator.client.watch(product_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("watch failed", exc_info=True)
            await self._async_send(chat_id, self._error_text(err))
            return
        message = (
            f"Beobachte jetzt {product_id}. Der Sensor erscheint beim nächsten Abruf."
            if self._language == "de"
            else f"Now watching {product_id}. Its sensor appears on the next poll."
        )
        await self._async_send(chat_id, message)
        await self._coordinator.async_request_refresh()

    async def _async_alerts(self, chat_id: int) -> None:
        try:
            alerts = await self._coordinator.client.alerts()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("alerts failed", exc_info=True)
            await self._async_send(chat_id, self._error_text(err))
            return
        await self._async_send(
            chat_id,
            format_alerts({"count": len(alerts), "alerts": alerts}, self._language),
        )


def _write_temp_file(data: bytes, suffix: str, directory: str) -> str:
    """Write image bytes inside the HA config dir, which send_photo may read."""
    os.makedirs(directory, exist_ok=True)
    handle, path = tempfile.mkstemp(suffix=suffix, dir=directory)
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    return path


_FOLLOWUP_STARTS = (
    "ist ",
    "sind ",
    "soll ",
    "sollte ",
    "kann ",
    "könnte ",
    "koennte ",
    "wird ",
    "würde ",
    "wuerde ",
    "lohnt ",
    "was ",
    "wie ",
    "warum ",
    "wieso ",
    "gibt ",
    "hat ",
    "haben ",
    "welche ",
    "welches ",
    "is ",
    "are ",
    "should ",
    "can ",
    "could ",
    "will ",
    "would ",
    "what ",
    "how ",
    "why ",
    "does ",
)


def _looks_like_followup(text: str) -> bool:
    """Whether free text should be a question about the stored deal, not a new search."""
    lowered = text.lower().strip()
    if "?" in lowered:
        return True
    if lowered in ("black friday", "black week", "cyber monday"):
        return True
    return lowered.startswith(_FOLLOWUP_STARTS)
