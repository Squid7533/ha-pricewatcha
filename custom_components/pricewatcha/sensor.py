"""Sensors: one wallet-per-product price sensor per watched product."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PricewatchaCoordinator

type PricewatchaConfigEntry = Any


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PricewatchaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a sensor per watched product, adding newly watched ones as they appear."""
    coordinator: PricewatchaCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_missing() -> None:
        products = coordinator.data or {}
        new_entities = [
            PricewatchaPriceSensor(coordinator, product_id)
            for product_id in products
            if product_id not in known
        ]
        for entity in new_entities:
            known.add(entity.product_id)
        if new_entities:
            async_add_entities(new_entities)

    add_missing()
    entry.async_on_unload(coordinator.async_add_listener(add_missing))


class PricewatchaPriceSensor(CoordinatorEntity[PricewatchaCoordinator], SensorEntity):
    """Current price of one watched product."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
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
        return {
            "product_id": self.product_id,
            "shop": product.get("shop"),
            "product_url": product.get("product_url"),
            "status": product.get("status"),
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
