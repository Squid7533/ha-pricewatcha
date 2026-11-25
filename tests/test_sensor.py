"""Guards the sensor's device/state class pairing, which HA validates at runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

import custom_components.pricewatcha.sensor as sensor_module
from custom_components.pricewatcha.sensor import (
    PricewatchaPriceSensor,
    async_remove_product_entity,
    async_setup_entry,
)


def _declared(name: str):
    """Read a declared _attr_* value on both HA layouts.

    Newer Home Assistant turns `_attr_x` into a property and keeps the value in
    `__attr_x`; older versions keep `_attr_x` as a plain class attribute.
    """
    own = vars(PricewatchaPriceSensor)
    for key in (f"__attr_{name}", f"_attr_{name}"):
        value = own.get(key)
        if value is not None and not isinstance(value, property):
            return value
    return None


def test_state_class_is_allowed_for_monetary():
    assert _declared("device_class") is SensorDeviceClass.MONETARY
    # HA rejects 'measurement' for monetary sensors; only None or TOTAL are legal.
    assert _declared("state_class") in (None, SensorStateClass.TOTAL)


class FakeEntityRegistry:
    def __init__(self):
        self.entries = {}
        self.removed = []

    def async_get_entity_id(self, domain, platform, unique_id):
        for entity_id, entry in self.entries.items():
            if (
                entry.domain == domain
                and entry.platform == platform
                and entry.unique_id == unique_id
            ):
                return entity_id
        return None

    def async_get(self, entity_id):
        return self.entries.get(entity_id)

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entries.pop(entity_id, None)

    def async_entries_for_device(self, device_id, include_disabled_entities=False):
        return [entry for entry in self.entries.values() if entry.device_id == device_id]

    @property
    def entities(self):
        return self.entries


class FakeDeviceRegistry:
    def __init__(self):
        self.devices = {"dev1": object()}
        self.removed = []

    def async_get(self, device_id):
        return self.devices.get(device_id)

    def async_remove_device(self, device_id):
        self.removed.append(device_id)
        self.devices.pop(device_id, None)


def test_condition_is_read_from_the_name_or_url_slug():
    coordinator = SimpleNamespace(
        data={
            "prod_a": {
                "name": "Apple iPhone Air 256GB wolkenweiß",
                "current_price": 659,
                "currency": "EUR",
                "product_url": "https://shop.example/iphone-air-sehr-gut.html",
            }
        },
        last_update_success=True,
    )
    sensor = PricewatchaPriceSensor(coordinator, "prod_a")

    assert sensor.extra_state_attributes["condition"] == "very good"


def test_condition_is_unknown_when_the_page_does_not_state_it():
    coordinator = SimpleNamespace(
        data={"prod_a": {"name": "Midea PortaSplit 3,5 kW", "current_price": 999}},
        last_update_success=True,
    )
    sensor = PricewatchaPriceSensor(coordinator, "prod_a")

    assert sensor.extra_state_attributes["condition"] == "unknown"


def test_removing_a_sensor_also_removes_its_device(monkeypatch):
    registry = FakeEntityRegistry()
    devices = FakeDeviceRegistry()
    registry.entries["sensor.prod_a"] = SimpleNamespace(
        domain="sensor",
        platform="pricewatcha",
        unique_id="pricewatcha_prod_a",
        device_id="dev1",
    )
    entity = SimpleNamespace(unique_id="pricewatcha_prod_a", platform=None)
    monkeypatch.setattr(sensor_module.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(sensor_module.dr, "async_get", lambda hass: devices)
    monkeypatch.setattr(
        sensor_module.er,
        "async_entries_for_device",
        lambda registry, device_id, include_disabled_entities=False: (
            registry.async_entries_for_device(device_id, include_disabled_entities)
        ),
    )

    async_remove_product_entity(SimpleNamespace(async_create_task=None), "prod_a")

    assert registry.removed == ["sensor.prod_a"]
    assert devices.removed == ["dev1"]


def test_the_entity_list_follows_the_watchlist(monkeypatch):
    registry = FakeEntityRegistry()
    devices = FakeDeviceRegistry()
    registry.entries["sensor.prod_a"] = SimpleNamespace(
        domain="sensor",
        platform="pricewatcha",
        unique_id="pricewatcha_prod_a",
        device_id="dev1",
    )
    monkeypatch.setattr(sensor_module.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(sensor_module.dr, "async_get", lambda hass: devices)
    monkeypatch.setattr(
        sensor_module.er,
        "async_entries_for_device",
        lambda registry, device_id, include_disabled_entities=False: (
            registry.async_entries_for_device(device_id, include_disabled_entities)
        ),
    )

    class Coordinator:
        def __init__(self):
            self.data = {
                "prod_a": {"name": "A", "current_price": 1},
                "prod_b": {"name": "B", "current_price": 2},
            }
            self.listener = None

        def async_add_listener(self, callback):
            self.listener = callback
            return lambda: None

    async def scenario():
        coordinator = Coordinator()
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(coordinator=coordinator),
            async_on_unload=lambda callback: None,
        )
        added = []
        hass = SimpleNamespace(async_create_task=None)

        await async_setup_entry(hass, entry, added.extend)
        assert [entity.product_id for entity in added] == ["prod_a", "prod_b"]

        # prod_a is unwatched; prod_b stays.
        coordinator.data = {"prod_b": {"name": "B", "current_price": 2}}
        coordinator.listener()

        assert registry.removed == ["sensor.prod_a"]
        assert devices.removed == ["dev1"]

    asyncio.run(scenario())


def test_a_product_unwatched_while_ha_was_off_is_cleaned_up(monkeypatch):
    registry = FakeEntityRegistry()
    devices = FakeDeviceRegistry()
    registry.entries["sensor.prod_old"] = SimpleNamespace(
        domain="sensor",
        platform="pricewatcha",
        unique_id="pricewatcha_prod_old",
        device_id="dev1",
    )
    monkeypatch.setattr(sensor_module.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(sensor_module.dr, "async_get", lambda hass: devices)
    monkeypatch.setattr(
        sensor_module.er,
        "async_entries_for_device",
        lambda registry, device_id, include_disabled_entities=False: (
            registry.async_entries_for_device(device_id, include_disabled_entities)
        ),
    )

    class Coordinator:
        data = {"prod_new": {"name": "New", "current_price": 1}}

        def async_add_listener(self, callback):
            return lambda: None

    async def scenario():
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(coordinator=Coordinator()),
            async_on_unload=lambda callback: None,
        )
        added = []
        await async_setup_entry(
            SimpleNamespace(async_create_task=None), entry, added.extend
        )

        assert [entity.product_id for entity in added] == ["prod_new"]
        assert registry.removed == ["sensor.prod_old"]
        assert devices.removed == ["dev1"]

    asyncio.run(scenario())
