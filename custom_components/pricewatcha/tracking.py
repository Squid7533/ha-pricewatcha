"""Persistent external-offer tracking, snapshots and alerts (0.9.0).

The Pricewatcha catalog already has its own watchlist. This module gives web offers
the same treatment: a stored record, periodic refresh, price snapshots and one
notification per real price drop. It deliberately stores plain dictionaries so the HA
Store survives restarts without a migration layer for every new field.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import WATCHLIST_MAX_PAGES, WATCHLIST_PAGE_SIZE
from .media import fetch_page_offer

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_KEY = "pricewatcha.tracked_offers"
SNAPSHOT_LIMIT = 120
DEFAULT_REFRESH_MINUTES = 60

NotifyCallback = Callable[[int, dict[str, Any], dict[str, Any]], Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_url(url: str) -> str:
    """Compare shop URLs without tracking parameters or a trailing slash."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return str(url or "").strip().casefold()
    return f"{parts.netloc.casefold()}{parts.path.rstrip('/').casefold()}"


class TrackedOfferManager:
    """Store, refresh and alert on external offers."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: Any,
        *,
        refresh_minutes: int = DEFAULT_REFRESH_MINUTES,
        store: Store | None = None,
        coordinator: Any | None = None,
    ) -> None:
        self.hass = hass
        self.client = client
        self.refresh_minutes = max(5, int(refresh_minutes))
        self._store = store or Store(hass, STORE_VERSION, STORE_KEY)
        self._offers: dict[str, dict[str, Any]] = {}
        self._notify: NotifyCallback | None = None
        self._task: asyncio.Task | None = None
        self._save_lock = asyncio.Lock()
        # Refreshed after a removal that also stopped a Pricewatcha watch, so the
        # sensor disappears at once instead of on the next scheduled poll.
        self.coordinator = coordinator

    # --- lifecycle ---------------------------------------------------------

    async def async_start(self, notify: NotifyCallback | None = None) -> None:
        await self.async_load()
        self._notify = notify
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_task(self._async_loop())

    def async_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        offers = stored.get("offers") if isinstance(stored, dict) else {}
        if isinstance(offers, dict):
            self._offers = {
                key: value
                for key, value in offers.items()
                if isinstance(value, dict) and value.get("source")
            }

    async def _async_save(self) -> None:
        async with self._save_lock:
            await self._store.async_save(
                {"version": STORE_VERSION, "offers": self._offers}
            )

    async def _async_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.refresh_minutes * 60)
                await self.async_refresh_all()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad refresh must not stop the loop
                _LOGGER.debug("Tracked-offer refresh loop failed", exc_info=True)

    # --- CRUD --------------------------------------------------------------

    def list(self, chat_id: int | None = None) -> list[dict[str, Any]]:
        offers = [
            offer
            for offer in self._offers.values()
            if offer.get("active", True)
            and (chat_id is None or int(offer.get("chat_id", 0)) == int(chat_id))
        ]
        return sorted(offers, key=lambda offer: str(offer.get("created_at") or ""))

    def get(self, offer_id: str, chat_id: int | None = None) -> dict[str, Any] | None:
        offer = self._offers.get(str(offer_id))
        if offer is None or not offer.get("active", True):
            return None
        if chat_id is not None and int(offer.get("chat_id", 0)) != int(chat_id):
            return None
        return offer

    def find(self, chat_id: int, url: str) -> dict[str, Any] | None:
        for offer in self.list(chat_id):
            if offer.get("url") == url:
                return offer
        return None

    async def async_add(
        self,
        *,
        chat_id: int,
        source: str,
        url: str,
        title: str,
        shop: str,
        price: float | None,
        currency: str = "EUR",
        condition: str = "unknown",
        source_id: str | None = None,
        query: str = "",
        target_total: float | None = None,
        notify_on_drop: bool = False,
        product_id: str | None = None,
        watched: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        existing = self.find(chat_id, url)
        if existing is not None:
            # A record may predate /track <url> claiming its watch (or have been
            # created by the service without one). Record the ownership now, so a
            # later removal can undo the watch it did not know about yet.
            changed = False
            if watched and existing.get("watched") is not True:
                existing["watched"] = True
                changed = True
            if product_id and not existing.get("product_id"):
                existing["product_id"] = str(product_id)
                changed = True
            if changed:
                await self._async_save()
            return existing, False
        offer_id = uuid4().hex[:8]
        total = round(float(price), 2) if price is not None else None
        created = _now()
        offer: dict[str, Any] = {
            "id": offer_id,
            "chat_id": int(chat_id),
            "source": source,
            "source_id": source_id or url,
            "url": url,
            "title": title,
            "shop": shop,
            "condition": condition,
            "currency": currency or "EUR",
            "query": query,
            "created_at": created,
            "last_checked_at": created,
            "last_price": _float(price),
            "last_total": total,
            "target_total": _float(target_total),
            "notify_on_drop": bool(notify_on_drop),
            "active": True,
            "last_notified_total": None,
            "last_notified_at": None,
            "last_error": None,
            # The catalog id and whether this record owns a Pricewatcha watch. Both
            # are needed to undo a /track <url>, which watches the product too.
            "product_id": str(product_id) if product_id else None,
            "watched": bool(watched),
            "snapshots": [
                {"at": created, "price": _float(price), "total": total, "available": True}
            ],
        }
        self._offers[offer_id] = offer
        await self._async_save()
        return offer, True

    async def async_remove(self, offer_id: str, chat_id: int | None = None) -> bool:
        offer = self.get(offer_id, chat_id)
        if offer is None:
            return False
        unwatched = await self._async_unwatch(offer)
        offer["active"] = False
        await self._async_save()
        if unwatched and self.coordinator is not None:
            await self.coordinator.async_request_refresh()
        return True

    async def _async_unwatch(self, offer: dict[str, Any]) -> bool:
        """Stop the Pricewatcha watch behind a tracked offer, if it made one.

        Records added through ``/track <url>`` watched the product on the
        Pricewatcha side, which is what created the Home Assistant sensor. Removing
        only the local record would leave that sensor behind. Offers added through
        the service carry ``watched: false`` and never touch the watchlist.
        """
        if offer.get("watched") is False:
            return False
        product_id = str(offer.get("product_id") or "")
        if not product_id and str(offer.get("source") or "") == "web":
            product_id = await self._async_watched_product_id(
                str(offer.get("url") or "")
            )
        if not product_id:
            return False
        try:
            await self.client.unwatch(product_id)
        except Exception as err:  # noqa: BLE001 - the local record is still removed
            _LOGGER.warning(
                "Could not stop watching %s while removing tracked offer %s: %s",
                product_id,
                offer.get("id"),
                err,
            )
            return False
        return True

    async def _async_watched_product_id(self, url: str) -> str | None:
        """Find the watched product with this URL, for records stored before 0.11."""
        target = _canonical_url(url)
        if not target:
            return None
        # The coordinator already holds the current watchlist; only fall back to an
        # API read when it has not caught up yet.
        known = getattr(self.coordinator, "data", None) or {}
        for product_id, product in known.items():
            if _canonical_url(str(product.get("product_url") or "")) == target:
                return str(product_id)
        try:
            items: list[dict[str, Any]] = []
            for page in range(WATCHLIST_MAX_PAGES):
                batch = await self.client.watchlist(
                    limit=WATCHLIST_PAGE_SIZE, offset=page * WATCHLIST_PAGE_SIZE
                )
                items.extend(batch)
                if len(batch) < WATCHLIST_PAGE_SIZE:
                    break
        except Exception:  # noqa: BLE001 - a failed lookup only skips the unwatch
            _LOGGER.debug("Could not read the watchlist", exc_info=True)
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            product = item.get("product") if isinstance(item.get("product"), dict) else {}
            if _canonical_url(str(product.get("product_url") or "")) != target:
                continue
            product_id = product.get("product_id") or item.get("product_id")
            if product_id:
                return str(product_id)
        return None

    async def async_set_alert(
        self,
        offer_id: str,
        *,
        target_total: float | None = None,
        notify_on_drop: bool | None = None,
        chat_id: int | None = None,
    ) -> dict[str, Any] | None:
        offer = self.get(offer_id, chat_id)
        if offer is None:
            return None
        offer["target_total"] = _float(target_total)
        if notify_on_drop is not None:
            offer["notify_on_drop"] = bool(notify_on_drop)
        await self._async_save()
        return offer

    async def async_find_by_index(
        self, chat_id: int, index: int
    ) -> dict[str, Any] | None:
        offers = self.list(chat_id)
        if 0 <= index < len(offers):
            return offers[index]
        return self.get(str(index), chat_id)

    # --- refresh -----------------------------------------------------------

    async def async_refresh_all(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for offer in list(self._offers.values()):
            if not offer.get("active", True):
                continue
            try:
                event = await self.async_refresh(offer["id"])
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Tracked offer refresh failed", exc_info=True)
                event = None
            if event is not None:
                events.append(event)
        await self._async_save()
        return events

    async def async_refresh(self, offer_id: str) -> dict[str, Any] | None:
        offer = self._offers.get(str(offer_id))
        if offer is None or not offer.get("active", True):
            return None
        fetched = await self._async_fetch(offer)
        if fetched is None:
            offer["last_error"] = "unavailable"
            offer["last_checked_at"] = _now()
            return None
        price = fetched.get("price")
        total = _float(price)
        old_total = _float(offer.get("last_total")) or _float(offer.get("last_price"))
        offer["last_error"] = None
        offer["last_checked_at"] = _now()
        offer["last_price"] = _float(price)
        offer["last_total"] = total
        if fetched.get("condition"):
            offer["condition"] = fetched["condition"]
        if fetched.get("title"):
            offer["title"] = fetched["title"]
        if fetched.get("url"):
            offer["url"] = fetched["url"]
        self._append_snapshot(offer, total)
        await self._async_save()
        event = await self._async_notify_drop(offer, old_total, total)
        if event is not None:
            # Persist last_notified_total after the send, so a restart does not repeat
            # a target notification at the same price.
            await self._async_save()
        return event

    async def _async_fetch(self, offer: dict[str, Any]) -> dict[str, Any] | None:
        source = str(offer.get("source") or "")
        if source == "web":
            page = await fetch_page_offer(
                str(offer.get("url") or ""),
                str(offer.get("query") or offer.get("title") or ""),
            )
            if page is None:
                return None
            return {
                "price": page.price,
                "currency": page.currency,
                "condition": page.condition,
                "title": page.title,
                "url": page.url or offer.get("url"),
            }
        if source == "pricewatcha":
            product = await self.client.product(str(offer.get("source_id") or ""))
            if not isinstance(product, dict) or product.get("current_price") is None:
                return None
            return {
                "price": product.get("current_price"),
                "currency": product.get("currency") or "EUR",
                "title": product.get("name") or offer.get("title"),
                "url": product.get("product_url") or offer.get("url"),
            }
        return None

    def _append_snapshot(self, offer: dict[str, Any], total: float | None) -> None:
        snapshots = offer.setdefault("snapshots", [])
        now = _now()
        day = now[:10]
        if snapshots:
            last = snapshots[-1]
            if (
                str(last.get("at") or "")[:10] == day
                and _float(last.get("total")) == total
            ):
                return
        snapshots.append(
            {"at": now, "price": total, "total": total, "available": True}
        )
        del snapshots[:-SNAPSHOT_LIMIT]

    async def _async_notify_drop(
        self, offer: dict[str, Any], old_total: float | None, new_total: float | None
    ) -> dict[str, Any] | None:
        if new_total is None or self._notify is None:
            return None
        target = _float(offer.get("target_total"))
        last_notified = _float(offer.get("last_notified_total"))
        reason: str | None = None
        if target is not None and new_total <= target:
            if last_notified is None or new_total < last_notified - 0.005:
                reason = "target"
        elif (
            offer.get("notify_on_drop")
            and old_total is not None
            and new_total < old_total - 0.005
            and (last_notified is None or new_total < last_notified - 0.005)
        ):
            reason = "drop"
        if reason is None:
            return None
        event = {
            "chat_id": int(offer.get("chat_id", 0)),
            "offer": dict(offer),
            "old_total": old_total,
            "new_total": new_total,
            "reason": reason,
        }
        try:
            await self._notify(int(offer.get("chat_id", 0)), offer, event)
        except Exception:  # noqa: BLE001 - retry on the next refresh
            _LOGGER.debug("Tracked-offer notification failed", exc_info=True)
            return None
        offer["last_notified_total"] = new_total
        offer["last_notified_at"] = _now()
        return event
