"""Sensors: one price sensor per watched product.

The sensor list is a projection of the Pricewatcha watchlist, not a growing log:
every coordinator update adds sensors for newly watched products and removes the
sensors (and their devices) of products that are no longer watched. That keeps
Home Assistant in step with the account when a product is unwatched from the
Telegram bot, the service, or the Pricewatcha dashboard.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .best_deal import condition_of
from .const import DOMAIN
from .coordinator import PricewatchaCoordinator

type PricewatchaConfigEntry = Any

UNIQUE_ID_PREFIX = f"{DOMAIN}_"


def _product_id_of_entry(entry: Any) -> str | None:
    """The product id carried by one of this integration's registry entries."""
    unique_id = str(getattr(entry, "unique_id", "") or "")
    if not unique_id.startswith(UNIQUE_ID_PREFIX):
        return None
    return unique_id[len(UNIQUE_ID_PREFIX) :] or None


@callback
def async_remove_product_entity(
    hass: HomeAssistant,
    product_id: str,
    entity: PricewatchaPriceSensor | None = None,
) -> None:
    """Remove a sensor and, when it was the last one, its product device.

    The entity registry entry is removed first so Home Assistant does not restore
    the sensor on the next start, then the live entity is removed and the device
    entry is cleaned up when no other entity uses it.
    """
    registry = er.async_get(hass)
    unique_id = f"{UNIQUE_ID_PREFIX}{product_id}"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    device_id: str | None = None
    if entity_id is not None:
        entry = registry.async_get(entity_id)
        device_id = entry.device_id if entry is not None else None
        registry.async_remove(entity_id)

    if entity is not None and entity.platform is not None:
        hass.async_create_task(entity.async_remove(force_remove=True))

    if device_id is None:
        return
    devices = dr.async_get(hass)
    if devices.async_get(device_id) is None:
        return
    if not er.async_entries_for_device(
        registry, device_id, include_disabled_entities=True
    ):
        devices.async_remove_device(device_id)


@callback
def _registered_product_entities(hass: HomeAssistant) -> list[Any]:
    """Every sensor entry this integration owns, including stale ones from a restart."""
    registry = er.async_get(hass)
    return [
        entry
        for entry in registry.entities.values()
        if entry.domain == "sensor"
        and entry.platform == DOMAIN
        and _product_id_of_entry(entry) is not None
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PricewatchaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Mirror the watched products as sensors, adding and removing as they change."""
    coordinator: PricewatchaCoordinator = entry.runtime_data.coordinator
    known: dict[str, PricewatchaPriceSensor] = {}

    @callback
    def sync_entities() -> None:
        products = coordinator.data or {}

        new_entities = [
            PricewatchaPriceSensor(coordinator, product_id)
            for product_id in products
            if product_id not in known
        ]
        for entity in new_entities:
            known[entity.product_id] = entity
        if new_entities:
            async_add_entities(new_entities)

        removed: set[str] = set()
        for product_id, entity in list(known.items()):
            if product_id in products:
                continue
            known.pop(product_id, None)
            async_remove_product_entity(hass, product_id, entity)
            removed.add(product_id)

        # Sensors registered by an earlier run whose product was unwatched while
        # Home Assistant was off are not part of `known`; sweep them as well.
        for registered in _registered_product_entities(hass):
            product_id = _product_id_of_entry(registered)
            if product_id is None or product_id in products or product_id in removed:
                continue
            async_remove_product_entity(hass, product_id)

    sync_entities()
    entry.async_on_unload(coordinator.async_add_listener(sync_entities))


class PricewatchaPriceSensor(CoordinatorEntity[PricewatchaCoordinator], SensorEntity):
    """Current price of one watched product."""

    _attr_device_class = SensorDeviceClass.MONETARY
    # HA allows only None or TOTAL with device_class MONETARY; TOTAL is the
    # one that keeps long-term statistics working for a price.
    _attr_state_class = SensorStateClass.TOTAL
    _attr_has_entity_name = True
    _attr_name = None  # the device (the product) carries the name

    def __init__(self, coordinator: PricewatchaCoordinator, product_id: str) -> None:
        super().__init__(coordinator)
        self.product_id = product_id
        self._attr_unique_id = f"{DOMAIN}_{product_id}"

    @property
    def _product(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self.product_id, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self._product)

    @property
    def native_value(self) -> float | None:
        price = self._product.get("current_price")
        try:
            return float(price) if price is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        currency = self._product.get("currency")
        return str(currency).upper() if currency else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        product = self._product
        name = str(product.get("name") or "")
        url = product.get("product_url")
        return {
            "product_id": self.product_id,
            "shop": product.get("shop"),
            "product_url": url,
            "status": product.get("status"),
            # The watchlist product carries the condition only in its name or URL
            # slug ("…-sehr-gut.html"), the same place best_deal has to look.
            "condition": condition_of(name, str(url) if url else None) or "unknown",
            "last_checked_at": product.get("last_checked_at"),
            "watched_at": product.get("watched_at"),
            "google_product_category": product.get("google_product_category_name"),
            "data_source": product.get("data_source_label"),
        }

    @property
    def device_info(self) -> DeviceInfo:
        product = self._product
        return DeviceInfo(
            identifiers={(DOMAIN, self.product_id)},
            name=str(product.get("name") or self.product_id),
            manufacturer=str(product.get("shop") or "Pricewatcha"),
            model="Tracked product",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=product.get("product_url"),
        )
