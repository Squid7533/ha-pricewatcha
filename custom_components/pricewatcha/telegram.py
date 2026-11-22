"""Telegram front-end: commands and price-drop pushes without any YAML.

Home Assistant's Telegram bot integration already does the transport (polling, token,
allowed chats) and fires `telegram_command` / `telegram_text` events, and offers the
`telegram_bot.send_message` service. This module listens to those events and answers
through that service, so the user never edits configuration.yaml, never adds helpers,
and never looks up a chat id: the first chat to speak to the bot is remembered.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, EVENT_PRICE_CHANGED
from .format import (
    format_alerts,
    format_deals,
    format_help,
    format_price_drop,
    format_search,
    format_track,
    format_usage,
    to_plain,
    truncate,
)

if TYPE_CHECKING:
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
    ("search", "Search the product catalog"),
    ("track", "Track a product URL"),
    ("watch", "Watch a product id"),
    ("alerts", "List price alerts"),
    ("help", "Show commands"),
]


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
    ) -> None:
        self.hass = hass
        self._coordinator = coordinator
        self._min_drop_pct = min_drop_pct
        self._chat_ids: list[int] = []
        self._store: Store[dict[str, Any]] | None = None
        self._unsubscribers: list[Any] = []
        self._warned_missing_service = False
        self._chat_param: str | None = None

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

    def _chat_param_name(self) -> str:
        """Which field this Home Assistant calls the destination chat.

        `target` is deprecated and stops working in 2026.9.0 in favour of `chat_id`, so
        the supported field is read from the registered service schema instead of
        guessing from the version.
        """
        if self._chat_param is not None:
            return self._chat_param

        services = self.hass.services.async_services()
        service = services.get(TELEGRAM_DOMAIN, {}).get(TELEGRAM_SEND_MESSAGE)
        validators = getattr(getattr(service, "schema", None), "schema", None)
        keys = {
            getattr(key, "schema", key)
            for key in validators
        } if isinstance(validators, dict) else set()

        self._chat_param = "chat_id" if "chat_id" in keys else "target"
        if self._chat_param == "target":
            _LOGGER.debug("Telegram bot service still expects 'target'; using it")
        return self._chat_param

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
            try:
                await set_bot_commands(session, str(token), BOT_COMMANDS)
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

        await self._async_send(chat_id, "This bot is already connected to someone else's Home Assistant.")
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
            self._async_broadcast(format_price_drop(dict(data)))
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
            await self._async_send(chat_id, format_help())
        elif command == "/deal":
            await self._async_deals(chat_id, rest)
        elif command == "/search":
            await self._async_search(chat_id, rest)
        elif command == "/track":
            await self._async_track(chat_id, args[0] if args else "")
        elif command == "/watch":
            await self._async_watch(chat_id, args[0] if args else "")
        elif command == "/alerts":
            await self._async_alerts(chat_id)
        else:
            await self._async_send(chat_id, format_help())

    async def _async_handle_text(self, event: Event) -> None:
        data = event.data or {}
        chat_id = self._as_chat_id(data.get("chat_id"))
        if chat_id is None or not await self._async_authorise(chat_id):
            return
        text = str(data.get("text") or "").strip()
        if text.startswith("/") or len(text) <= 2:
            return
        await self._async_deals(chat_id, text)

    @staticmethod
    def _as_chat_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # --- command implementations ----------------------------------------------

    async def _async_deals(self, chat_id: int, query: str) -> None:
        if not query:
            await self._async_send(chat_id, format_usage("/deal", "iphone 18 pro"))
            return
        try:
            payload = await self._coordinator.async_find_best_deals(query, limit=3)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("deal lookup failed", exc_info=True)
            await self._async_send(chat_id, f"Pricewatcha error: {err}")
            return
        await self._async_send(chat_id, format_deals(payload))

    async def _async_search(self, chat_id: int, query: str) -> None:
        if not query:
            await self._async_send(chat_id, format_usage("/search", "iphone 18 pro"))
            return
        try:
            results = await self._coordinator.client.search(query, limit=8)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("search failed", exc_info=True)
            await self._async_send(chat_id, f"Pricewatcha error: {err}")
            return
        await self._async_send(
            chat_id, format_search({"query": query, "count": len(results), "results": results})
        )

    async def _async_track(self, chat_id: int, url: str) -> None:
        if not url.startswith("http"):
            await self._async_send(chat_id, format_usage("/track", "https://shop.example/product"))
            return
        try:
            payload = await self._coordinator.client.track(url, watch=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("track failed", exc_info=True)
            await self._async_send(chat_id, f"Pricewatcha error: {err}")
            return
        await self._async_send(chat_id, format_track(payload))
        await self._coordinator.async_request_refresh()

    async def _async_watch(self, chat_id: int, product_id: str) -> None:
        if not product_id:
            await self._async_send(chat_id, format_usage("/watch", "prod_ONgO0IjXa7YnqQ"))
            return
        try:
            await self._coordinator.client.watch(product_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("watch failed", exc_info=True)
            await self._async_send(chat_id, f"Pricewatcha error: {err}")
            return
        await self._async_send(
            chat_id, f"Now watching {product_id}. Its sensor appears on the next poll."
        )
        await self._coordinator.async_request_refresh()

    async def _async_alerts(self, chat_id: int) -> None:
        try:
            alerts = await self._coordinator.client.alerts()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("alerts failed", exc_info=True)
            await self._async_send(chat_id, f"Pricewatcha error: {err}")
            return
        await self._async_send(
            chat_id, format_alerts({"count": len(alerts), "alerts": alerts})
        )
