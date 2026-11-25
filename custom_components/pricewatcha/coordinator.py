"""Data coordinator: polls the watched products for the Pricewatcha integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PricewatchaApiError,
    PricewatchaAuthError,
    PricewatchaClient,
    PricewatchaRateLimitError,
)
from .best_deal import find_best_deals
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_PRICE_CHANGED,
    WATCHLIST_MAX_PAGES,
    WATCHLIST_PAGE_SIZE,
)

_LOGGER = logging.getLogger(__name__)


class PricewatchaCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the account watchlist; one dict per watched product."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, client: PricewatchaClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            items = await self._async_watchlist()
        except PricewatchaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PricewatchaRateLimitError as err:
            raise UpdateFailed(f"rate limited (retry after {err.retry_after}s)") from err
        except PricewatchaApiError as err:
            raise UpdateFailed(str(err)) from err

        products: dict[str, dict[str, Any]] = {}
        for item in items:
            product = item.get("product") or {}
            product_id = item.get("product_id") or product.get("product_id")
            if not product_id:
                continue
            products[str(product_id)] = {**product, "watched_at": item.get("watched_at")}

        self._fire_price_changes(products)
        return products

    async def _async_watchlist(self) -> list[dict[str, Any]]:
        """Read every watchlist page.

        The endpoint returns at most 200 rows; reading only the first page would make
        the sensor reconciliation treat every product beyond it as unwatched and
        delete its sensor. The page cap is a guard against a broken API that keeps
        answering full pages.
        """
        items: list[dict[str, Any]] = []
        for page in range(WATCHLIST_MAX_PAGES):
            batch = await self.client.watchlist(
                limit=WATCHLIST_PAGE_SIZE, offset=page * WATCHLIST_PAGE_SIZE
            )
            items.extend(batch)
            if len(batch) < WATCHLIST_PAGE_SIZE:
                break
        return items

    def _fire_price_changes(self, products: dict[str, dict[str, Any]]) -> None:
        """Announce genuinely changed prices; the first poll has no baseline."""
        if not self.data:
            return

        for product_id, product in products.items():
            previous = self.data.get(product_id)
            if not previous:
                continue
            try:
                old_price = float(previous.get("current_price"))
                new_price = float(product.get("current_price"))
            except (TypeError, ValueError):
                continue
            if old_price == new_price:
                continue

            self.hass.bus.async_fire(
                EVENT_PRICE_CHANGED,
                {
                    "product_id": product_id,
                    "name": product.get("name") or previous.get("name"),
                    "shop": product.get("shop"),
                    "currency": product.get("currency"),
                    "product_url": product.get("product_url"),
                    "old_price": old_price,
                    "new_price": new_price,
                    "change": round(new_price - old_price, 2),
                    "change_pct": round((new_price - old_price) / old_price * 100, 2) if old_price else None,
                    "is_drop": new_price < old_price,
                },
            )

    async def async_find_best_deals(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Rank catalog matches by value; used by the service and the intent."""
        return await find_best_deals(self.client, query, **kwargs)


async def async_validate_api_key(hass: HomeAssistant, api_key: str) -> None:
    """Raise if the key cannot read the account watchlist."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    client = PricewatchaClient(async_get_clientsession(hass), api_key)
    await client.watchlist(limit=1)


def scan_interval_of(entry: ConfigEntry) -> int:
    """Configured poll interval in minutes."""
    return int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
